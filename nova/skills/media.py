"""Media: narrated videos, photo slideshows, voice-overs, transcription, conversions,
and optional local AI images.

With a 4 GB GPU, true text-to-video AI models don't fit, so videos are built
from designed slides (or your photos / AI images) with a slow cinematic zoom,
voiced by ElevenLabs (or Piper offline) and assembled with ffmpeg.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

from .. import context, ffmpeg
from ..config import resolve
from ..tools import register_group, tool

register_group("media", ["video", "clip", "reel", "tiktok", "short", "youtube", "slideshow", "image", "picture",
                         "photo", "voiceover", "voice over", "narrat", "transcribe", "recording", "audio", "mp3",
                         "mp4", "convert", "trim", "cut the", "generate an image", "draw", "poster"])

SIZES = {"landscape": (1920, 1080), "vertical": (1080, 1920), "square": (1080, 1080)}
PALETTES = [((12, 18, 48), (88, 42, 160)), ((6, 40, 60), (0, 150, 160)), ((40, 10, 30), (200, 70, 90)),
            ((10, 30, 20), (40, 160, 110)), ((25, 25, 35), (120, 110, 190))]


def out_dir() -> Path:
    p = resolve(context.cfg.media.output_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def font(size: int, bold: bool = True):
    from PIL import ImageFont
    names = (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]) + \
            (["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"])
    dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"),
            Path("/Library/Fonts")]
    for d in dirs:
        for n in names:
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    return ImageFont.load_default(size)


def gradient(size, c1, c2):
    from PIL import Image
    import numpy as np
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    t = (x / w * 0.45 + y / h * 0.55)[..., None]
    arr = (np.array(c1) * (1 - t) + np.array(c2) * t)
    # vignette for a cinematic look
    cx, cy = w / 2, h / 2
    d = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2)[..., None]
    arr = arr * (1 - 0.45 * np.clip(d - 0.4, 0, 1))
    return Image.fromarray(arr.clip(0, 255).astype("uint8"), "RGB")


def draw_wrapped(draw, text, xy, fnt, max_w, fill=(255, 255, 255), spacing=12, anchor_center=False):
    """Draw text wrapped to max_w pixels. Returns the y after the text."""
    x, y = xy
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    for line in lines:
        lx = x - draw.textlength(line, font=fnt) / 2 if anchor_center else x
        draw.text((lx, y), line, font=fnt, fill=fill)
        y += fnt.size + spacing
    return y


def render_slide(heading: str, text: str, size, palette, image: str | None, path: Path, index: int, total: int,
                 transparent: bool = False):
    """Draw a scene. transparent=True draws only the text (with a soft scrim) to lay over B-roll video."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    if transparent:
        scrim = Image.new("L", size, 0)
        sd = ImageDraw.Draw(scrim)
        for y in range(h):                          # darker at the top and bottom, clear in the middle
            edge = max(0.0, 1 - min(y, h - y) / (h * 0.45))
            sd.line([(0, y), (w, y)], fill=int(170 * edge ** 1.4 + 55))
        bg = Image.merge("RGBA", (*Image.new("RGB", size, (0, 0, 0)).split(), scrim))
    elif image and Path(image).exists():
        bg = Image.open(image).convert("RGB")
        scale = max(w / bg.width, h / bg.height)
        bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
        bg = bg.crop(((bg.width - w) // 2, (bg.height - h) // 2, (bg.width - w) // 2 + w, (bg.height - h) // 2 + h))
        shade = Image.new("RGB", size, (0, 0, 0))
        bg = Image.blend(bg, shade, 0.45)
    else:
        bg = gradient(size, *palette)
        glow = Image.new("RGB", size, (0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((w * 0.55, -h * 0.2, w * 1.2, h * 0.6), fill=palette[1])
        bg = Image.blend(bg, glow.filter(ImageFilter.GaussianBlur(160)), 0.35)
    d = ImageDraw.Draw(bg)
    margin = int(w * 0.08)
    vertical = h > w
    hf = font(int(h * (0.052 if vertical else 0.075)))
    bf = font(int(h * (0.028 if vertical else 0.04)), bold=False)
    y = int(h * (0.16 if vertical else 0.14)) if transparent else int(h * (0.30 if vertical else 0.28))
    if heading:
        d.rectangle((margin, y - 30, margin + 90, y - 22), fill=(255, 255, 255))
        y = draw_wrapped(d, heading, (margin, y), hf, w - 2 * margin)
    if text:
        draw_wrapped(d, text, (margin, y + 24), bf, w - 2 * margin, fill=(225, 228, 240))
    sf = font(int(h * 0.02), bold=False)
    d.text((margin, h - margin * 0.7), f"{index:02d} / {total:02d}", font=sf, fill=(200, 200, 215))
    bg.save(path)


# ── captions ──────────────────────────────────────────────
def caption_chunks(text: str, duration: float, words_per_chunk: int = 5, lead: float = 0.15):
    """Split narration into short chunks, timed in proportion to their length."""
    words = text.split()
    if not words or duration <= 0:
        return []
    chunks = [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]
    total = sum(len(c) + 3 for c in chunks)
    t, out = lead, []
    usable = max(0.5, duration - lead - 0.1)
    for c in chunks:
        span = usable * (len(c) + 3) / total
        out.append((t, t + span, c))
        t += span
    return out


def render_caption(text: str, size, path: Path) -> Path:
    """A transparent full-frame PNG with a bold, outlined caption near the bottom."""
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    vertical = h > w
    f = font(int(min(w, h) * (0.062 if vertical else 0.052)))
    lines, cur = [], ""
    for word in text.split():
        test = f"{cur} {word}".strip()
        if d.textlength(test, font=f) <= w * 0.84:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    y = int(h * (0.70 if vertical else 0.80)) - (len(lines) - 1) * (f.size + 8) // 2
    for line in lines:
        x = (w - d.textlength(line, font=f)) / 2
        d.text((x, y), line, font=f, fill=(255, 255, 255, 255), stroke_width=max(3, f.size // 12),
               stroke_fill=(0, 0, 0, 235))
        y += f.size + 8
    img.save(path)
    return path


# ── free stock footage (Pexels) ───────────────────────────
def fetch_broll(query: str, fmt: str, min_seconds: float = 4.0) -> Path | None:
    """Download a matching stock video (or photo) from Pexels. Needs a free PEXELS_API_KEY."""
    import httpx
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key or not query:
        return None
    orient = {"landscape": "landscape", "vertical": "portrait", "square": "square"}.get(fmt, "landscape")
    cache = resolve("workspace/.broll")
    cache.mkdir(parents=True, exist_ok=True)
    target_w = SIZES.get(fmt, SIZES["landscape"])[0]
    headers = {"Authorization": key}
    try:
        r = httpx.get("https://api.pexels.com/videos/search", headers=headers, timeout=20,
                      params={"query": query, "orientation": orient, "per_page": 8, "size": "medium"})
        r.raise_for_status()
        vids = [v for v in r.json().get("videos", []) if v.get("duration", 0) >= min_seconds] or \
            r.json().get("videos", [])
        for v in vids:
            files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("width")]
            if not files:
                continue
            best = min(files, key=lambda f: abs(f["width"] - target_w) + (5000 if f["width"] > 2600 else 0))
            dest = cache / f"pexels_{v['id']}_{best['width']}.mp4"
            if not dest.exists():
                with httpx.stream("GET", best["link"], timeout=120, follow_redirects=True) as dl:
                    dl.raise_for_status()
                    with open(dest, "wb") as fh:
                        for chunk in dl.iter_bytes(1 << 16):
                            fh.write(chunk)
            return dest
        r = httpx.get("https://api.pexels.com/v1/search", headers=headers, timeout=20,
                      params={"query": query, "orientation": orient, "per_page": 1})
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if photos:
            dest = cache / f"pexels_photo_{photos[0]['id']}.jpg"
            if not dest.exists():
                dest.write_bytes(httpx.get(photos[0]["src"]["large2x"], timeout=60, follow_redirects=True).content)
            return dest
    except Exception as e:
        print(f"[broll] '{query}': {e}")
    return None


def _clip(bg: Path, audio: Path, out: Path, size, pad: float = 0.5, overlay: Path | None = None,
          captions: list | None = None, bg_is_video: bool = False):
    """One scene: background (still with slow zoom, or looping video) + text overlay + timed captions + audio."""
    w, h = size
    dur = ffmpeg.duration(audio) + pad
    frames = int(dur * 30)
    if bg_is_video:
        args = ["-stream_loop", "-1", "-i", bg]
        chain = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,"
                 f"eq=brightness=-0.06:saturation=1.05,setsar=1[b0]")
    else:
        args = ["-loop", "1", "-i", bg]
        chain = (f"[0:v]scale={w * 2}:-2,zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-(iw/zoom/2)':"
                 f"y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps=30,setsar=1[b0]")
    args += ["-i", audio]
    idx, last = 2, "b0"
    layers = ([(overlay, None)] if overlay else []) + [(p, (a, b)) for p, a, b in (captions or [])]
    for n, (png, window) in enumerate(layers, 1):
        args += ["-i", png]
        enable = f":enable='between(t,{window[0]:.2f},{window[1]:.2f})'" if window else ""
        chain += f";[{last}][{idx}:v]overlay=0:0{enable}[b{n}]"
        last, idx = f"b{n}", idx + 1
    chain += f";[{last}]format=yuv420p[v];[1:a]apad[a]"
    ffmpeg.run([*args, "-filter_complex", chain, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
                "-t", f"{dur:.2f}", out])


def _assemble(clips: list[Path], out: Path, music: str = ""):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.as_posix()}'\n")
        lst = f.name
    if music and Path(music).exists():
        tmp = out.with_suffix(".tmp.mp4")
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", tmp])
        ffmpeg.run(["-i", tmp, "-stream_loop", "-1", "-i", music, "-filter_complex",
                    "[1:a]volume=0.12[m];[0:a][m]amix=inputs=2:duration=first[a]", "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", out])
        tmp.unlink(missing_ok=True)
    else:
        ffmpeg.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out])
    os.unlink(lst)


SCRIPT_PROMPT = """Write a short video script about: {topic}
Audience/style notes: {style}
Make {n} scenes. Each scene has a short on-screen heading (max 7 words), an on-screen line (max 16 words),
a spoken narration of 1-3 natural sentences, and "broll": a 2-4 word stock-footage search that visually
fits the scene (concrete things, e.g. "server room lights", "team meeting office", "cape town aerial").
The first scene hooks the viewer; the last is a call to action.
Reply with JSON only: {{"title": "...", "slides": [{{"heading": "...", "text": "...", "narration": "...", "broll": "..."}}]}}"""


def build_video(title: str, slides: list[dict], fmt: str = "landscape", images: list[str] | None = None,
                music: str = "", captions: bool | None = None, broll: bool | None = None) -> Path:
    mcfg = context.cfg.get("media") or {}
    captions = mcfg.get("captions", True) if captions is None else captions
    broll = mcfg.get("broll", True) if broll is None else broll
    size = SIZES.get(fmt, SIZES["landscape"])
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r"[^\w]+", "_", title)[:50].strip("_") or "video"
    work = out_dir() / f".build_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    palette = PALETTES[hash(title) % len(PALETTES)]
    words = 4 if size[1] > size[0] else 6
    clips, used_broll = [], 0
    for i, s in enumerate(slides, 1):
        img, wav, clip = work / f"s{i}.png", work / f"s{i}.wav", work / f"s{i}.mp4"
        narration = s.get("narration") or s.get("text") or s.get("heading", "")
        context.speech.synth_wav(narration, wav)
        dur = ffmpeg.duration(wav)
        pic = images[i - 1] if images and i - 1 < len(images) else None
        media = None if pic else (fetch_broll(s.get("broll") or s.get("heading", ""), fmt, dur) if broll else None)
        is_video = bool(media and media.suffix == ".mp4")
        if media and not is_video:
            pic = str(media)
        if is_video:
            used_broll += 1
            render_slide(s.get("heading", ""), s.get("text", ""), size, palette, None, img, i, len(slides),
                         transparent=True)
        else:
            render_slide(s.get("heading", ""), s.get("text", ""), size, palette, pic, img, i, len(slides))
        caps = []
        if captions and narration.strip(" ."):
            for n, (a, b, text) in enumerate(caption_chunks(narration, dur, words)):
                caps.append((render_caption(text, size, work / f"s{i}_c{n}.png"), a, b))
        if is_video:
            _clip(media, wav, clip, size, overlay=img, captions=caps, bg_is_video=True)
        else:
            _clip(img, wav, clip, size, captions=caps)
        clips.append(clip)
    out = out_dir() / f"{name}_{stamp}.mp4"
    _assemble(clips, out, music)
    try:
        ffmpeg.run(["-ss", "1", "-i", out, "-frames:v", "1", out.with_suffix(".jpg")])      # thumbnail
    except Exception:
        pass
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    extra = (f", {used_broll} stock clips" if used_broll else "") + (", captions" if captions else "")
    context.record("video", title, out, f"{len(slides)} scenes, {fmt}{extra}")
    return out


@tool(group="media")
def make_video(topic: str, style: str = "", slides: int = 6, format: str = "landscape", music_path: str = "",
               captions: bool = True, stock_footage: bool = True) -> str:
    """Create a narrated explainer / promo video about a topic: script, scenes, voice-over, burned-in captions
    and (with a free Pexels key) matching stock footage behind each scene.
    Args:
        topic: what the video is about
        style: audience, tone or brand notes
        slides: number of scenes (3-12)
        format: landscape (YouTube), vertical (Reels/TikTok/Shorts) or square
        music_path: optional background music file
        captions: burn subtitles into the video
        stock_footage: use Pexels stock video behind scenes (needs PEXELS_API_KEY)
    """
    raw = context.llm.complete(SCRIPT_PROMPT.format(topic=topic, style=style or "clear, friendly, professional",
                                                    n=max(3, min(12, slides))), prefer_smart=True, temperature=0.7)
    data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    out = build_video(data.get("title", topic), data["slides"], format, music=music_path, captions=captions,
                      broll=stock_footage)
    context.attach(out)
    note = "" if os.environ.get("PEXELS_API_KEY") or not stock_footage else \
        " (Add a free PEXELS_API_KEY to .env for stock footage.)"
    return f"Video ready: {out} ({ffmpeg.duration(out):.0f} seconds).{note}"


@tool(group="media")
def photos_to_video(folder: str, narration: str = "", seconds_per_photo: float = 3.5,
                    format: str = "landscape", music_path: str = "") -> str:
    """Turn a folder of photos into a video slideshow with optional voice-over.
    Args:
        folder: folder with the photos
        narration: optional voice-over text for the whole video
        seconds_per_photo: time per photo if there's no narration
        format: landscape, vertical or square
        music_path: optional background music file
    """
    from .files import safe
    pics = sorted(p for p in safe(folder).iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))[:40]
    if not pics:
        return "No photos in that folder."
    parts = re.split(r"(?<=[.!?])\s+", narration.strip()) if narration else []
    slides = []
    for i, p in enumerate(pics):
        slides.append({"heading": "", "text": "", "narration": parts[i] if i < len(parts) else "."})
    if not narration:
        # silent slideshow: generate silence of the right length per photo
        size = SIZES.get(format, SIZES["landscape"])
        work = Path(tempfile.mkdtemp())
        clips = []
        for i, p in enumerate(pics):
            sil = work / f"{i}.wav"
            ffmpeg.run(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(seconds_per_photo), sil])
            clip = work / f"{i}.mp4"
            _clip(p, sil, clip, size, pad=0)
            clips.append(clip)
        out = out_dir() / f"slideshow_{dt.datetime.now():%Y%m%d_%H%M%S}.mp4"
        _assemble(clips, out, music_path)
        context.record("video", out.stem, out, f"{len(pics)} photos")
    else:
        out = build_video(Path(folder).name, slides, format, [str(p) for p in pics], music_path)
    context.attach(out)
    return f"Slideshow ready: {out}"


@tool(group="media")
def make_voiceover(text: str, name: str = "voiceover") -> str:
    """Create an MP3 voice-over from text in Nova's (ElevenLabs) voice.
    Args:
        text: what to say
        name: file name
    """
    wav = out_dir() / f"{name}.wav"
    context.speech.synth_wav(text, wav)
    mp3 = wav.with_suffix(".mp3")
    ffmpeg.run(["-i", wav, "-b:a", "192k", mp3])
    wav.unlink()
    context.record("audio", name, mp3, text[:200])
    context.attach(mp3)
    return f"Voice-over saved: {mp3}"


@tool(group="media")
def transcribe_file(path: str) -> str:
    """Transcribe an audio or video file (meeting recording, voice memo) to text, saved next to it.
    Args:
        path: the audio/video file
    """
    from .files import safe
    p = safe(path)
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        ffmpeg.to_wav16k(p, wav)
        text = context.speech.transcribe(str(wav))
    out = p.with_suffix(".transcript.txt")
    out.write_text(text, encoding="utf-8")
    context.record("file", out.name, out, "transcript")
    return f"Transcript saved to {out}.\n\n{text[:3000]}"


@tool(group="media")
def trim_media(path: str, start: str, end: str) -> str:
    """Cut a section out of a video or audio file.
    Args:
        path: the file
        start: start time, e.g. 00:01:10 or 70
        end: end time
    """
    from .files import safe
    p = safe(path)
    out = p.with_name(f"{p.stem}_trim{p.suffix}")
    ffmpeg.run(["-i", p, "-ss", start, "-to", end, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out])
    context.record("video", out.name, out, f"trim {start}-{end}")
    return f"Saved {out}"


@tool(group="media")
def convert_media(path: str, to_format: str) -> str:
    """Convert audio/video/image to another format (mp3, mp4, wav, gif, jpg, png, webm…).
    Args:
        path: the file
        to_format: target extension, e.g. mp3
    """
    from .files import safe
    p = safe(path)
    out = p.with_suffix("." + to_format.lstrip("."))
    ffmpeg.run(["-i", p, out])
    context.record("file", out.name, out, "converted")
    return f"Saved {out}"


@tool(group="media")
def generate_image(prompt: str, format: str = "square") -> str:
    """Generate an image locally with Stable Diffusion (if enabled in config). Slow on a 4 GB GPU (~30-60 s).
    Args:
        prompt: what to draw
        format: square, landscape or vertical
    """
    if not context.cfg.media.get("image_gen_enabled"):
        return "Local image generation is off. Enable media.image_gen_enabled in config.yaml and install requirements-imagegen.txt."
    import httpx
    import torch
    from diffusers import StableDiffusionPipeline
    # free the GPU: ask Ollama to unload the chat model for a moment
    try:
        base = context.cfg.llm.providers.ollama.base_url.removesuffix("/v1")
        httpx.post(f"{base}/api/generate", json={"model": context.cfg.llm.providers.ollama.model, "keep_alive": 0}, timeout=10)
    except Exception:
        pass
    global _SD
    if "_SD" not in globals():
        _SD = StableDiffusionPipeline.from_pretrained(context.cfg.media.image_model, torch_dtype=torch.float16,
                                                     safety_checker=None).to("cuda")
        _SD.enable_attention_slicing()
    w, h = {"square": (512, 512), "landscape": (768, 432), "vertical": (432, 768)}.get(format, (512, 512))
    img = _SD(prompt, width=w, height=h, num_inference_steps=25).images[0]
    out = resolve("workspace/images") / f"img_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    context.record("image", prompt[:60], out, prompt)
    context.attach(out)
    return f"Image saved: {out}"
