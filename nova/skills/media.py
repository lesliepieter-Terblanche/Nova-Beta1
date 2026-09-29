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


def render_slide(heading: str, text: str, size, palette, image: str | None, path: Path, index: int, total: int):
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    if image and Path(image).exists():
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
    y = int(h * (0.30 if vertical else 0.28))
    if heading:
        d.rectangle((margin, y - 30, margin + 90, y - 22), fill=(255, 255, 255))
        y = draw_wrapped(d, heading, (margin, y), hf, w - 2 * margin)
    if text:
        draw_wrapped(d, text, (margin, y + 24), bf, w - 2 * margin, fill=(225, 228, 240))
    sf = font(int(h * 0.02), bold=False)
    d.text((margin, h - margin * 0.7), f"{index:02d} / {total:02d}", font=sf, fill=(200, 200, 215))
    bg.save(path)


def _clip(img: Path, audio: Path, out: Path, size, pad: float = 0.5):
    w, h = size
    dur = ffmpeg.duration(audio) + pad
    frames = int(dur * 30)
    vf = (f"scale={w * 2}:-2,zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={w}x{h}:fps=30,format=yuv420p")
    ffmpeg.run(["-loop", "1", "-i", img, "-i", audio, "-filter_complex", f"[0:v]{vf}[v];[1:a]apad[a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-t", f"{dur:.2f}", out])


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
Make {n} slides. Each slide has a short on-screen heading (max 7 words), an on-screen line (max 16 words)
and a spoken narration of 1-3 natural sentences. First slide hooks the viewer; last slide is a call to action.
Reply with JSON only: {{"title": "...", "slides": [{{"heading": "...", "text": "...", "narration": "..."}}]}}"""


def build_video(title: str, slides: list[dict], fmt: str = "landscape", images: list[str] | None = None,
                music: str = "") -> Path:
    size = SIZES.get(fmt, SIZES["landscape"])
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r"[^\w]+", "_", title)[:50].strip("_") or "video"
    work = out_dir() / f".build_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    palette = PALETTES[hash(title) % len(PALETTES)]
    clips = []
    for i, s in enumerate(slides, 1):
        img, wav, clip = work / f"s{i}.png", work / f"s{i}.wav", work / f"s{i}.mp4"
        pic = images[i - 1] if images and i - 1 < len(images) else None
        render_slide(s.get("heading", ""), s.get("text", ""), size, palette, pic, img, i, len(slides))
        context.speech.synth_wav(s.get("narration") or s.get("text") or s.get("heading", ""), wav)
        _clip(img, wav, clip, size)
        clips.append(clip)
    out = out_dir() / f"{name}_{stamp}.mp4"
    _assemble(clips, out, music)
    (work / "s1.png").replace(out.with_suffix(".jpg"))       # thumbnail
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    context.record("video", title, out, f"{len(slides)} slides, {fmt}")
    return out


@tool(group="media")
def make_video(topic: str, style: str = "", slides: int = 6, format: str = "landscape", music_path: str = "") -> str:
    """Create a narrated explainer / promo video about a topic (script, designed slides, voice-over, MP4).
    Args:
        topic: what the video is about
        style: audience, tone or brand notes
        slides: number of scenes (3-12)
        format: landscape (YouTube), vertical (Reels/TikTok/Shorts) or square
        music_path: optional background music file
    """
    raw = context.llm.complete(SCRIPT_PROMPT.format(topic=topic, style=style or "clear, friendly, professional",
                                                    n=max(3, min(12, slides))), prefer_smart=True, temperature=0.7)
    data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    out = build_video(data.get("title", topic), data["slides"], format, music=music_path)
    context.attach(out)
    return f"Video ready: {out} ({ffmpeg.duration(out):.0f} seconds)."


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
