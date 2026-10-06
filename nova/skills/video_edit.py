"""Video editing with moviepy: long video → captioned vertical Shorts / Reels / TikToks, burn in captions,
make a video vertical, join clips. Runs on the PC (ffmpeg under the hood); speech is transcribed locally."""
from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

from .. import context
from ..config import resolve
from ..tools import register_group, tool
from .media import render_caption

register_group("video_edit", ["shorts", "short", "reel", "reels", "tiktok", "clips from", "cut up", "highlights",
                              "captions", "subtitles", "vertical", "9:16", "join videos", "merge videos",
                              "combine videos", "edit video", "edit my video", "video edit", "moviepy"])

VERTICAL = (1080, 1920)
PICK_PROMPT = """You are a social-media video editor. From this timestamped transcript of a {length:.0f}-second video, pick the
{count} best self-contained moments for vertical Shorts / Reels / TikToks, each about {seconds:.0f} seconds (never more
than {max_s:.0f}). Choose moments with a strong hook in the first 3 seconds, one clear idea, and a natural ending.
Reply with JSON only: {{"clips": [{{"start": 12.0, "end": 55.5, "title": "short catchy title", "hook": "why it works"}}]}}

Transcript:
{transcript}"""


def _out(name: str) -> Path:
    d = resolve("workspace/videos")
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\- ]+", "", name).strip()[:50] or "video"
    return d / f"{safe} {time.strftime('%H%M%S')}.mp4"


def _write(clip, out: Path) -> Path:
    clip.write_videofile(str(out), codec="libx264", audio_codec="aac", preset="veryfast", threads=4, fps=clip.fps or 30,
                         logger=None, temp_audiofile=str(out.with_suffix(".m4a")))
    context.record("video", out.stem, out, "edited")
    return out


def _vertical_frame(frame, mode: str = "blur"):
    """One frame → 1080×1920 (OpenCV: fast)."""
    import cv2
    import numpy as np
    W, H = VERTICAL
    h, w = frame.shape[:2]
    if h > w * 1.5:
        return cv2.resize(frame, VERTICAL, interpolation=cv2.INTER_AREA)
    cw = min(w, int(h * W / H))                       # the middle 9:16 slice of the picture
    x0 = (w - cw) // 2
    if mode == "crop":
        return cv2.resize(frame[:, x0:x0 + cw], VERTICAL, interpolation=cv2.INTER_LINEAR)
    small = cv2.resize(frame[:, x0:x0 + cw], (W // 24, H // 24), interpolation=cv2.INTER_AREA)
    bg = cv2.resize(cv2.GaussianBlur(small, (5, 5), 0), VERTICAL, interpolation=cv2.INTER_LINEAR)
    out = (bg.astype(np.float32) * 0.5).astype(np.uint8)
    fh = int(round(h * W / w)) // 2 * 2
    fg = cv2.resize(frame, (W, fh), interpolation=cv2.INTER_AREA if w > W else cv2.INTER_LINEAR)
    y = (H - fh) // 2
    out[y:y + fh] = fg
    return out


def vertical(clip, mode: str = "blur"):
    """9:16. blur = the whole picture on a soft blurred copy of itself; crop = fill the screen (sides cut)."""
    return clip.image_transform(lambda f: _vertical_frame(f, mode), apply_to=[])


def caption_groups(words: list[tuple[float, float, str]], per: int = 3, max_gap: float = 0.6) -> list[tuple]:
    """Group words into short punchy captions: 3 words, broken at pauses and sentence ends."""
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or w[0] - cur[-1][1] > max_gap or cur[-1][2][-1:] in ".?!"):
            out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        out.append((cur[0][0], cur[-1][1], " ".join(x[2] for x in cur)))
    return out


def _caption_sprite(text: str, size, tmp: Path, i: int):
    """(rgb, alpha, x, y) of a rendered caption, trimmed to its box so blending is cheap."""
    import numpy as np
    from PIL import Image
    png = render_caption(text, size, tmp / f"c{i}.png")
    im = Image.open(png).convert("RGBA")
    box = im.getbbox()
    if not box:
        return None
    im = im.crop(box)
    a = np.asarray(im).astype(np.float32)
    return a[..., :3], a[..., 3:4] / 255.0, box[0], box[1]


def with_captions(clip, words: list[tuple[float, float, str]], upper: bool = True):
    """Burn word-group captions into the frames (blended only where the text is)."""
    import numpy as np
    tmp = Path(tempfile.mkdtemp(prefix="nova_caps_"))
    cues = []
    for i, (a, b, text) in enumerate(caption_groups(words)):
        if b <= 0 or a >= clip.duration:
            continue
        sprite = _caption_sprite(text.upper() if upper else text, clip.size, tmp, i)
        if sprite:
            cues.append((max(0.0, a), min(clip.duration, max(b, a + 0.35)), sprite))

    def paint(get_frame, t):
        frame = get_frame(t)
        cue = next((c for c in cues if c[0] <= t < c[1]), None)
        if not cue:
            return frame
        rgb, alpha, x, y = cue[2]
        out = frame.copy()
        h, w = rgb.shape[:2]
        region = out[y:y + h, x:x + w].astype(np.float32)
        out[y:y + h, x:x + w] = (rgb[:region.shape[0], :region.shape[1]] * alpha[:region.shape[0], :region.shape[1]]
                                 + region * (1 - alpha[:region.shape[0], :region.shape[1]])).astype("uint8")
        return out
    return clip.transform(paint, apply_to=[])


def _words(path: Path, start: float = 0.0, end: float | None = None) -> list[tuple[float, float, str]]:
    if not context.speech:
        return []
    words = context.speech.transcribe_words(str(path))
    return [(a - start, b - start, w) for a, b, w in words if a >= start and (end is None or b <= end + 0.3)]


def pick_moments(segments: list[tuple[float, float, str]], length: float, count: int, seconds: float) -> list[dict]:
    """Ask the model for the best moments; fall back to evenly spaced clips."""
    max_s = min(60.0, seconds * 1.4)
    if context.llm and segments:
        transcript = "\n".join(f"[{a:.1f}-{b:.1f}] {t}" for a, b, t in segments)[:14000]
        try:
            raw = context.llm.complete(PICK_PROMPT.format(length=length, count=count, seconds=seconds, max_s=max_s,
                                                          transcript=transcript), prefer_smart=True, temperature=0.3)
            m = re.search(r"\{.*\}", raw, re.S)
            clips = json.loads(m.group(0))["clips"] if m else []
            good = []
            for c in clips:
                a, b = float(c["start"]), float(c["end"])
                if 0 <= a < b <= length + 1 and 5 <= b - a <= max_s + 5:
                    good.append({"start": a, "end": min(b, length), "title": str(c.get("title", ""))[:60]})
            if good:
                return good[:count]
        except Exception as e:
            print(f"[video] picking moments failed, spacing them evenly: {e}")
    step = length / (count + 1)
    return [{"start": max(0.0, step * (i + 1) - seconds / 2), "end": min(length, step * (i + 1) + seconds / 2),
             "title": f"Part {i + 1}"} for i in range(count)]


# ── tools ────────────────────────────────────────────────
@tool(group="video_edit")
def video_to_shorts(path: str, count: int = 3, seconds: int = 40, captions: bool = True) -> str:
    """Turn a long video (talk, webinar, podcast, YouTube video) into vertical Shorts / Reels / TikToks: finds the
    best moments from what's said, crops to 9:16 and burns in bold word-by-word captions.
    Args:
        path: the video file
        count: how many clips (1-8)
        seconds: roughly how long each clip should be (15-60)
        captions: burn in captions
    """
    from moviepy import VideoFileClip
    from .files import safe
    p = safe(path)
    count, seconds = max(1, min(8, int(count))), max(15, min(60, int(seconds)))
    src = VideoFileClip(str(p))
    try:
        segments = context.speech.transcribe_segments(str(p)) if context.speech else []
        moments = pick_moments(segments, src.duration, count, seconds)
        words = context.speech.transcribe_words(str(p)) if (captions and context.speech) else []
        outs = []
        for m in moments:
            clip = vertical(src.subclipped(m["start"], m["end"]))
            if captions and words:
                w = [(a - m["start"], b - m["start"], t) for a, b, t in words if a >= m["start"] and b <= m["end"] + .3]
                clip = with_captions(clip, w)
            outs.append(_write(clip, _out(m["title"] or p.stem)))
    finally:
        src.close()
    for o in outs[:3]:
        context.attach(o)
    names = "\n".join(f"• {o.name}" for o in outs)
    return f"Made {len(outs)} vertical clip(s) in workspace/videos:\n{names}"


@tool(group="video_edit")
def add_captions(path: str, uppercase: bool = True, highlight: bool = True, brand: str = "") -> str:
    """Burn bold, word-by-word captions into a video (transcribed on this PC). The word being spoken lights up in
    the brand's accent colour.
    Args:
        path: the video file
        uppercase: SHOUTY social-media style captions
        highlight: light up each word as it is spoken (false = plain white captions)
        brand: brand kit whose accent colour to use ("" = the default kit)
    """
    from .files import safe
    p = safe(path)
    if highlight:
        from .. import cinema
        words = _words(p)
        if not words:
            return "I couldn't hear any speech in that video to caption."
        kit = cinema.brand(brand) or cinema.BRAND_DEFAULTS
        out = _out(p.stem + " captions")
        cinema.burn_captions(p, out, words, accent=cinema.rgb_of(kit["accent"], "#ffcd3c"), upper=uppercase,
                             font_path=kit.get("font", ""))
        context.record("video", out.stem, out, "captions")
        context.attach(out)
        return f"Captioned video saved: {out}"
    from moviepy import VideoFileClip
    src = VideoFileClip(str(p))
    try:
        words = _words(p)
        if not words:
            return "I couldn't hear any speech in that video to caption."
        out = _write(with_captions(src, words, uppercase), _out(p.stem + " captions"))
    finally:
        src.close()
    context.attach(out)
    return f"Captioned video saved: {out}"


@tool(group="video_edit")
def make_vertical(path: str, mode: str = "blur") -> str:
    """Make a landscape video vertical (9:16) for TikTok / Reels / Shorts / Stories.
    Args:
        path: the video file
        mode: blur (whole picture on a blurred background) or crop (fill the screen, sides cut off)
    """
    from moviepy import VideoFileClip
    from .files import safe
    p = safe(path)
    src = VideoFileClip(str(p))
    try:
        out = _write(vertical(src, mode), _out(p.stem + " vertical"))
    finally:
        src.close()
    context.attach(out)
    return f"Vertical video saved: {out}"


@tool(group="video_edit")
def join_videos(paths: str, fade: float = 0.4, name: str = "joined") -> str:
    """Join several videos into one, with a short cross-fade between them.
    Args:
        paths: the video files, separated by semicolons or new lines, in order
        fade: cross-fade seconds (0 = straight cut)
        name: name for the result
    """
    from moviepy import VideoFileClip, concatenate_videoclips, vfx
    from .files import safe
    files = [safe(x.strip()) for x in re.split(r"[;\n]", paths) if x.strip()]
    if len(files) < 2:
        return "Give me at least two videos to join."
    clips = [VideoFileClip(str(f)) for f in files]
    try:
        size = clips[0].size
        clips2 = [c if c.size == size else c.resized(size) for c in clips]
        if fade > 0:
            clips2 = [clips2[0]] + [c.with_effects([vfx.CrossFadeIn(fade)]) for c in clips2[1:]]
            joined = concatenate_videoclips(clips2, method="compose", padding=-fade)
        else:
            joined = concatenate_videoclips(clips2, method="compose")
        out = _write(joined, _out(name))
    finally:
        for c in clips:
            c.close()
    context.attach(out)
    return f"Joined {len(files)} videos: {out} ({joined.duration:.0f} s)."
