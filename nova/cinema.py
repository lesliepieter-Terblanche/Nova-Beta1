"""Cinematic mode: the look of film, made on this PC for free.

  looks     colour grades (teal & orange, warm film, moody, noir…) + vignette, film grain and widescreen bars
  3D shots  a still photo becomes a moving camera shot: a small depth model (MiDaS, 66 MB, CPU) works out what is
            near and far, and the camera pushes in / pulls out / orbits with real parallax
  brand     your name, colours, logo and handle: an intro sting, a lower third, a watermark and an outro
  captions  word-by-word captions with the spoken word highlighted in your accent colour
  sound     music faded in and out, dipped under the voice, loudness evened out, cuts on the beat

Everything is numpy / OpenCV / ffmpeg — no cloud, no per-clip cost. Used by nova/skills/cinematic.py.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np

from . import ffmpeg
from .config import resolve

FPS = 30
SIZES = {"landscape": (1920, 1080), "vertical": (1080, 1920), "square": (1080, 1080)}
PHOTO = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VIDEO = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
DEPTH_URL = "https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx"

# ── looks ─────────────────────────────────────────────────
LOOKS = {
    "teal_orange": ("Teal & orange — the blockbuster look: warm skin and light, cool shadows",
                    "eq=contrast=1.07:saturation=1.14,colorbalance=rs=-0.07:gs=0.0:bs=0.09:rm=0.03:bm=-0.03:"
                    "rh=0.05:gh=0.01:bh=-0.055,curves=all='0/0 0.25/0.21 0.75/0.80 1/1'"),
    "warm_film": ("Warm film — soft, golden, slightly faded like 35 mm",
                  "eq=contrast=1.03:saturation=0.96,colorbalance=rs=0.04:bs=-0.04:rm=0.04:bm=-0.03:rh=0.06:gh=0.02:"
                  "bh=-0.07,curves=all='0/0.035 0.5/0.5 1/0.965'"),
    "moody": ("Moody — dark, cool and desaturated, for drama",
              "eq=contrast=1.14:saturation=0.78:brightness=-0.035,colorbalance=rs=-0.05:bs=0.07:rm=-0.02:bm=0.03:"
              "bh=-0.02,curves=all='0/0 0.3/0.23 0.7/0.72 1/0.97'"),
    "golden_hour": ("Golden hour — glowing sunset warmth",
                    "eq=contrast=1.05:saturation=1.2:gamma=1.03,colorbalance=rs=0.05:bs=-0.06:rm=0.07:gm=0.02:"
                    "bm=-0.06:rh=0.09:gh=0.04:bh=-0.1"),
    "noir": ("Noir — high-contrast black and white",
             "hue=s=0,eq=contrast=1.22:brightness=-0.02,curves=all='0/0 0.25/0.18 0.75/0.84 1/1'"),
    "clean": ("Clean — true colours with a little extra punch",
              "eq=contrast=1.05:saturation=1.08,curves=all='0/0 0.25/0.235 0.75/0.775 1/1'"),
}
LOOK_WORDS = {"teal": "teal_orange", "orange": "teal_orange", "blockbuster": "teal_orange", "warm": "warm_film",
              "film": "warm_film", "vintage": "warm_film", "moody": "moody", "dark": "moody", "drama": "moody",
              "golden": "golden_hour", "sunset": "golden_hour", "noir": "noir", "black": "noir", "bw": "noir",
              "clean": "clean", "natural": "clean", "none": "clean"}
TRANSITIONS = {"fade": "fade", "dissolve": "dissolve", "dip": "fadeblack", "black": "fadeblack", "slide": "smoothleft",
               "wipe": "wipeleft", "zoom": "zoomin", "circle": "circleopen", "cut": "cut"}
MOVES = ["push_in", "orbit_right", "pull_out", "orbit_left", "rise"]


def look_name(look: str, default: str = "teal_orange") -> str:
    key = re.sub(r"[^a-z]+", "_", (look or "").lower()).strip("_")
    if key in LOOKS:
        return key
    return next((v for k, v in LOOK_WORDS.items() if k in key), default) if key else default


def grade_filter(look: str) -> str:
    return LOOKS[look_name(look)][1]


def finish_filter(size, bars: bool = True, grain: bool = True, vignette: bool = True) -> str:
    """Vignette, film grain and widescreen bars for a frame of this size (after the colour grade)."""
    w, h = size
    parts = []
    if vignette:
        parts.append("vignette=angle=PI/5.2")
    if grain:
        parts.append("noise=alls=7:allf=t+u")
    if bars:
        bar = int((h - w / 2.39) / 2) // 2 * 2 if w > h else int(h * 0.055) // 2 * 2
        if bar > 4:
            parts.append(f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill,"
                         f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill")
    return ",".join(parts) or "null"


# ── reading and writing frames through ffmpeg ─────────────
def probe(path) -> dict:
    """{"w","h","fps","duration","audio"} of a video, as it is displayed (phone rotation applied)."""
    p = subprocess.run([ffmpeg.exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    err = p.stderr
    m = re.search(r"Stream #.*Video:.*?[, ](\d{2,5})x(\d{2,5})", err)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    rot = re.search(r"rotat\w*\s*(?:of|:)\s*(-?\d+)", err)
    if rot and abs(int(rot.group(1))) % 180 == 90:
        w, h = h, w
    f = re.search(r"([\d.]+) fps", err)
    d = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return {"w": w, "h": h, "fps": float(f.group(1)) if f else 30.0,
            "duration": int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0,
            "audio": bool(re.search(r"Stream #.*Audio:", err))}


class Writer:
    """Raw RGB frames in → an H.264 file out."""

    def __init__(self, out, size, fps: float = FPS, vf: str = "", extra_inputs: list | None = None,
                 extra_args: list | None = None):
        w, h = size
        cmd = [ffmpeg.exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{w}x{h}", "-r", f"{fps:g}", "-i", "-", *map(str, extra_inputs or [])]
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                *map(str, extra_args or []), str(out)]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, frame) -> None:
        self.p.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())

    def close(self) -> None:
        self.p.stdin.close()
        err = self.p.stderr.read().decode(errors="replace")
        if self.p.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err.strip()[-600:]}")


def frames(path, size, fps: float = FPS):
    """Every frame of a video as an RGB array of this size."""
    w, h = size
    p = subprocess.Popen([ffmpeg.exe(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf",
                          f"scale={w}:{h},fps={fps:g}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    n = w * h * 3
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


# ── 3D photo motion ───────────────────────────────────────
_depth_session = None


def depth_model():
    """The MiDaS-small depth model (downloaded once, 66 MB). None when it can't be had — shots then move flat."""
    global _depth_session
    if _depth_session is not None:
        return _depth_session or None
    p = resolve("models/depth/midas_small.onnx")
    try:
        if not p.exists() or p.stat().st_size < 30_000_000:
            import httpx
            p.parent.mkdir(parents=True, exist_ok=True)
            print("[cinema] downloading the depth model for 3D photo motion (66 MB, once)…")
            tmp = p.with_suffix(".part")
            with httpx.stream("GET", DEPTH_URL, timeout=300, follow_redirects=True) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 16):
                        fh.write(chunk)
            tmp.replace(p)
        import onnxruntime as ort
        _depth_session = ort.InferenceSession(str(p), providers=["CPUExecutionProvider"])
    except Exception as e:
        print(f"[cinema] no depth model, photos will move without 3D: {e}")
        _depth_session = False
    return _depth_session or None


def depth_map(rgb) -> tuple[np.ndarray, bool]:
    """(how near each pixel is, 0 = far … 1 = near; True when it came from the depth model)."""
    import cv2
    h, w = rgb.shape[:2]
    model = depth_model()
    if model is None:                                  # no model: pretend the middle is nearest — a gentle bulge
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        d = 1 - np.clip(np.hypot((x - w / 2) / (w / 2), (y - h * 0.55) / (h / 2)), 0, 1)
        return d * 0.6, False
    x = cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_CUBIC).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    out = model.run(None, {model.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0][0]
    lo, hi = np.percentile(out, 2), np.percentile(out, 98)
    d = np.clip((out - lo) / max(hi - lo, 1e-6), 0, 1).astype(np.float32)
    d = cv2.resize(d, (w, h), interpolation=cv2.INTER_CUBIC)
    k = max(3, int(min(w, h) / 45) | 1)                # soft edges, so nothing tears where near meets far
    return np.clip(cv2.GaussianBlur(d, (k, k), 0), 0, 1), True


def _cover(img, size, over: float = 1.0):
    """Resize + centre-crop so the picture fills the frame (optionally a bit larger, for room to move)."""
    import cv2
    w, h = int(size[0] * over), int(size[1] * over)
    ih, iw = img.shape[:2]
    s = max(w / iw, h / ih)
    img = cv2.resize(img, (max(w, int(iw * s + 0.5)), max(h, int(ih * s + 0.5))),
                     interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    y, x = (img.shape[0] - h) // 2, (img.shape[1] - w) // 2
    return img[y:y + h, x:x + w]


def load_photo(path):
    """A photo as an RGB array, turned the right way up."""
    from PIL import Image, ImageOps
    return np.asarray(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))


def photo_shot(photo, out, size, seconds: float = 4.0, move: str = "push_in", look: str = "", strength: float = 1.0,
               fps: float = FPS) -> dict:
    """A still photo → a moving camera shot with parallax. Returns {"depth": bool, "move": …, "seconds": …}."""
    import cv2
    w, h = size
    src = _cover(load_photo(photo), size, 1.0)
    depth, real = depth_map(src)
    focus = float(np.median(depth))
    n = max(2, int(round(seconds * fps)))
    cx, cy = (w - 1) / 2, (h - 1) / 2
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    amp = 0.026 * min(w, h) * strength * (1.0 if real else 0.5)        # sideways travel of the nearest things, px
    move = move if move in MOVES else "push_in"
    wr = Writer(out, size, fps, vf=grade_filter(look) if look else "")
    try:
        for i in range(n):
            t = i / (n - 1)
            zoom, radial, sx, sy = 1.06, 0.0, 0.0, 0.0
            if move == "push_in":
                zoom, radial, sy = 1.03 + 0.11 * t, 0.07 * t * strength, -amp * 0.25 * (t - 0.5)
            elif move == "pull_out":
                zoom, radial = 1.14 - 0.11 * t, 0.07 * (1 - t) * strength
            elif move in ("orbit_right", "orbit_left"):
                zoom, sx = 1.07 + 0.03 * t, amp * 2 * (t - 0.5) * (1 if move == "orbit_right" else -1)
            elif move == "rise":
                zoom, sy = 1.07 + 0.03 * t, amp * 2 * (0.5 - t)
            bx, by = cx + (gx - cx) / zoom, cy + (gy - cy) / zoom       # the plain zoom…
            d = cv2.remap(depth, bx, by, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE) - focus
            mx = bx + d * sx - d * radial * (bx - cx)                    # …and near things travel further
            my = by + d * sy - d * radial * (by - cy)
            wr.write(cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101))
    finally:
        wr.close()
    return {"depth": real, "move": move, "seconds": n / fps}


def video_shot(path, out, size, seconds: float, look: str = "", speed: float = 1.0, start: float | None = None) -> dict:
    """A piece of a video, filling the frame, graded, without sound."""
    w, h = size
    info = probe(path)
    speed = max(0.25, min(2.0, float(speed or 1.0)))
    need = seconds * speed                                       # how much of the source that uses
    if start is None:
        start = max(0.0, (info["duration"] - need) / 2)         # the middle is usually the good part
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setpts=PTS/{speed:g},fps={FPS},setsar=1"
          + (f",{grade_filter(look)}" if look else ""))
    loop = ["-stream_loop", "-1"] if info["duration"] and info["duration"] < start + need + 0.1 else []
    ffmpeg.run([*loop, "-ss", f"{start:.2f}", "-i", path, "-vf", vf, "-an", "-t", f"{seconds:.3f}", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", out])
    return {"seconds": seconds, "speed": speed}


# ── brand kits ────────────────────────────────────────────
BRAND_DEFAULTS = {"name": "", "handle": "", "tagline": "", "colour": "#101a3a", "accent": "#ffb347", "logo": "",
                  "font": "", "look": "", "call_to_action": ""}


def _brand_file() -> Path:
    from . import context
    try:
        return resolve(str(context.cfg["media"].get("brands_file") or "data/brands.json"))
    except (TypeError, KeyError, AttributeError):
        return resolve("data/brands.json")


def brands() -> dict:
    try:
        return json.loads(_brand_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default": "", "kits": {}}


def save_brand(name: str, default: bool | None = None, **fields) -> dict:
    data = brands()
    key = name.strip().lower()
    kit = {**BRAND_DEFAULTS, **data["kits"].get(key, {}), "name": name.strip()}
    for k, v in fields.items():
        if k in BRAND_DEFAULTS and v not in (None, ""):
            kit[k] = str(v).strip()
    for k in ("colour", "accent"):
        kit[k] = "#%02x%02x%02x" % rgb_of(kit[k], BRAND_DEFAULTS[k])
    data["kits"][key] = kit
    if default or (default is None and not data.get("default")):
        data["default"] = key
    _brand_file().parent.mkdir(parents=True, exist_ok=True)
    _brand_file().write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return kit


def brand(name: str = "") -> dict | None:
    """The brand kit by (part of) its name, else the default one, else None."""
    data = brands()
    key = (name or "").strip().lower()
    if key in ("none", "no", "off"):
        return None
    if key:
        hit = data["kits"].get(key) or next((k for n, k in data["kits"].items() if key in n or n in key), None)
        if hit:
            return {**BRAND_DEFAULTS, **hit}
    d = data["kits"].get(data.get("default") or "")
    return {**BRAND_DEFAULTS, **d} if d and not key else None


NAMED = {"black": (0, 0, 0), "white": (255, 255, 255), "red": (220, 50, 60), "green": (40, 170, 110),
         "blue": (40, 110, 230), "navy": (16, 26, 58), "yellow": (255, 205, 60), "gold": (232, 184, 75),
         "orange": (255, 140, 50), "purple": (130, 80, 220), "pink": (240, 100, 160), "teal": (0, 160, 160),
         "grey": (120, 125, 135), "gray": (120, 125, 135)}


def rgb_of(colour: str, fallback: str = "#101a3a") -> tuple[int, int, int]:
    c = (colour or "").strip().lower()
    m = re.fullmatch(r"#?([0-9a-f]{6})", c) or re.fullmatch(r"#?([0-9a-f]{3})", c)
    if m:
        hx = m.group(1) if len(m.group(1)) == 6 else "".join(ch * 2 for ch in m.group(1))
        return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
    if c in NAMED:
        return NAMED[c]
    return rgb_of(fallback, "#101a3a") if colour != fallback else (16, 26, 58)


# ── titles: intro sting, outro, lower third, watermark ────
def _font(size: int, bold: bool = True, path: str = ""):
    from PIL import ImageFont
    if path and Path(path).exists():
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    from .skills.media import font
    return font(size, bold)


def _tracked(draw, xy, text, fnt, fill, tracking: float = 0.0, measure: bool = False) -> float:
    """Draw text with extra space between the letters (the cinema-title look). Returns its width."""
    x = xy[0]
    for ch in text:
        if not measure:
            draw.text((x, xy[1]), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking
    return x - xy[0] - (tracking if text else 0)


def _fit_tracked(draw, text, max_w, size, bold, tracking_ratio, font_path):
    while True:
        f = _font(size, bold, font_path)
        if _tracked(draw, (0, 0), text, f, None, size * tracking_ratio, measure=True) <= max_w or size <= 18:
            return f
        size = int(size * 0.92)


def _logo(path: str, max_w: int, max_h: int):
    from PIL import Image
    if not path or not Path(path).exists():
        return None
    try:
        im = Image.open(path).convert("RGBA")
    except OSError:
        return None
    s = min(max_w / im.width, max_h / im.height)
    return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)


def _backdrop(size, kit) -> np.ndarray:
    """A dark, softly lit backdrop in the brand colour."""
    import cv2
    w, h = size
    base, acc = np.array(rgb_of(kit["colour"]), np.float32), np.array(rgb_of(kit["accent"], "#ffb347"), np.float32)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (y / h)[..., None]
    img = base * (0.55 - 0.4 * t) + 6
    glow = np.exp(-(((x - w * 0.5) / (w * 0.55)) ** 2 + ((y - h * 0.42) / (h * 0.35)) ** 2))[..., None]
    img = img + (base * 0.55 + acc * 0.10) * glow
    d = np.hypot((x - w / 2) / (w / 2), (y - h / 2) / (h / 2))[..., None]
    img = img * (1 - 0.5 * np.clip(d - 0.45, 0, 1))
    return cv2.GaussianBlur(img.clip(0, 255).astype(np.uint8), (0, 0), 3)


def title_card(out, size, kit, headline: str, subline: str = "", seconds: float = 1.8, small: str = "") -> float:
    """An animated title card: logo, headline in spaced capitals, an accent line that draws itself, a subline."""
    import cv2
    from PIL import Image, ImageDraw
    w, h = size
    bg = _backdrop(size, kit)
    acc = rgb_of(kit["accent"], "#ffb347")
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    m = min(w, h)
    head = (headline or "").upper()
    hf = _fit_tracked(d, head, w * 0.84, int(m * 0.10), True, 0.14, kit.get("font", ""))
    sf = _font(int(m * 0.040), False, kit.get("font", ""))
    logo = _logo(kit.get("logo", ""), int(w * 0.30), int(h * 0.16))
    block = (logo.height + int(m * 0.05) if logo else 0) + hf.size + int(m * 0.06) + (sf.size if subline else 0)
    y = int(h * 0.5 - block / 2)
    if logo:
        layer.alpha_composite(logo, ((w - logo.width) // 2, y))
        y += logo.height + int(m * 0.05)
    hw = _tracked(d, (0, 0), head, hf, None, hf.size * 0.14, measure=True)
    _tracked(d, ((w - hw) / 2, y), head, hf, (255, 255, 255, 255), hf.size * 0.14)
    line_y = y + hf.size + int(m * 0.03)
    if subline:
        sw = d.textlength(subline, font=sf)
        d.text(((w - sw) / 2, line_y + int(m * 0.028)), subline, font=sf, fill=(225, 228, 240, 235))
    if small:
        ff = _font(int(m * 0.028), True, kit.get("font", ""))
        fw = _tracked(d, (0, 0), small, ff, None, ff.size * 0.08, measure=True)
        _tracked(d, ((w - fw) / 2, int(h * 0.86)), small, ff, (*acc, 255), ff.size * 0.08)
    rgba = np.asarray(layer).astype(np.float32)
    n = max(2, int(round(seconds * FPS)))
    line_w = min(hw, w * 0.5)
    wr = Writer(out, size)
    try:
        for i in range(n):
            t = i / FPS
            a = min(1.0, t / 0.55)
            a = a * a * (3 - 2 * a)                                  # ease in
            scale = 1.045 - 0.045 * min(1.0, t / max(seconds, 0.1))  # a slow settle, never still
            M = cv2.getRotationMatrix2D((w / 2, h / 2), 0, scale)
            lay = cv2.warpAffine(rgba, M, (w, h), flags=cv2.INTER_LINEAR)
            alpha = lay[..., 3:4] / 255.0 * a
            frame = bg.astype(np.float32) * (1 - alpha) + lay[..., :3] * alpha
            lw = int(line_w * min(1.0, max(0.0, (t - 0.25) / 0.6)) ** 0.7)
            if lw > 2:
                x0 = int(w / 2 - lw / 2)
                frame[line_y:line_y + max(3, int(m * 0.004)), x0:x0 + lw] = acc
            wr.write(frame.clip(0, 255))
    finally:
        wr.close()
    return n / FPS


def lower_third(path, size, kit, title: str, subtitle: str = "", clear_of_captions: bool = False) -> Path:
    """A transparent PNG: accent bar + title + subtitle on a soft dark wash — bottom-left, or wherever the
    captions aren't when there are captions."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    m = min(w, h)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    tf, sf = _font(int(m * 0.046), True, kit.get("font", "")), _font(int(m * 0.030), False, kit.get("font", ""))
    d = ImageDraw.Draw(img)
    x, pad = int(w * 0.07), int(m * 0.02)
    while d.textlength(title, font=tf) > w * 0.8 and tf.size > 20:
        tf = _font(int(tf.size * 0.92), True, kit.get("font", ""))
    bh = tf.size + (sf.size + pad // 2 if subtitle else 0) + pad
    if clear_of_captions:                    # captions sit at 68 % (vertical) / 80 % (wide) of the height
        y = int(h * 0.80) if h > w else int(h * 0.10)
    else:
        y = int(h * (0.86 if h > w else 0.88)) - bh
    tw = max(d.textlength(title, font=tf), d.textlength(subtitle, font=sf) if subtitle else 0)
    wash = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(wash).rounded_rectangle((x - pad * 2, y - pad, x + tw + pad * 4, y + bh + pad),
                                           radius=pad, fill=(0, 0, 0, 120))
    img.alpha_composite(wash.filter(ImageFilter.GaussianBlur(pad)))
    d = ImageDraw.Draw(img)
    d.rectangle((x - pad, y, x - pad + max(4, int(m * 0.006)), y + bh), fill=(*rgb_of(kit["accent"], "#ffb347"), 255))
    d.text((x + pad, y - int(tf.size * 0.12)), title, font=tf, fill=(255, 255, 255, 255))
    if subtitle:
        d.text((x + pad, y + tf.size + pad // 2), subtitle, font=sf, fill=(228, 231, 242, 240))
    img.save(path)
    return Path(path)


def watermark(path, size, kit) -> Path | None:
    """A transparent PNG with the logo top-right — or, without a logo, the handle small at the bottom."""
    from PIL import Image, ImageDraw
    w, h = size
    m = min(w, h)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    logo = _logo(kit.get("logo", ""), int(w * 0.13), int(h * 0.07))
    if logo:
        logo.putalpha(logo.getchannel("A").point(lambda v: int(v * 0.8)))
        img.alpha_composite(logo, (w - logo.width - int(m * 0.05), int(h * (0.07 if h > w else 0.06))))
    elif kit.get("handle"):
        f = _font(int(m * 0.026), True, kit.get("font", ""))
        d = ImageDraw.Draw(img)
        tw = d.textlength(kit["handle"], font=f)
        d.text(((w - tw) / 2, int(h * 0.925)), kit["handle"], font=f, fill=(255, 255, 255, 150),
               stroke_width=2, stroke_fill=(0, 0, 0, 90))
    else:
        return None
    img.save(path)
    return Path(path)


# ── captions with the spoken word highlighted ─────────────
def word_groups(words: list[tuple[float, float, str]], per: int = 3, max_gap: float = 0.6) -> list[list[tuple]]:
    """Short punchy captions: about three words, broken at pauses and sentence ends."""
    out, cur = [], []
    for wd in words:
        if cur and (len(cur) >= per or wd[0] - cur[-1][1] > max_gap or cur[-1][2][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(wd)
    if cur:
        out.append(cur)
    return out


def spread_words(text: str, duration: float, lead: float = 0.12) -> list[tuple[float, float, str]]:
    """Word timings for a narration when nothing listened to it: in proportion to each word's length."""
    words = text.split()
    if not words or duration <= 0:
        return []
    total = sum(len(w) + 2 for w in words)
    t, out = lead, []
    usable = max(0.4, duration - lead - 0.1)
    for w in words:
        span = usable * (len(w) + 2) / total
        out.append((t, t + span, w))
        t += span
    return out


def _caption_sprite(texts: list[str], active: int, size, accent, font_path: str = ""):
    """(rgb, alpha, x, y): the caption with one word in the accent colour, trimmed to its box."""
    from PIL import Image, ImageDraw
    w, h = size
    vertical = h > w
    f = _font(int(min(w, h) * (0.068 if vertical else 0.056)), True, font_path)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    space = d.textlength(" ", font=f)
    lines, cur, cur_w = [], [], 0.0
    for i, t in enumerate(texts):
        tw = d.textlength(t, font=f)
        if cur and cur_w + space + tw > w * 0.84:
            lines.append(cur)
            cur, cur_w = [], 0.0
        cur.append((i, t, tw))
        cur_w += tw + (space if len(cur) > 1 else 0)
    lines.append(cur)
    y = int(h * (0.68 if vertical else 0.80)) - (len(lines) - 1) * (f.size + 10) // 2
    stroke = max(3, f.size // 11)
    for line in lines:
        x = (w - (sum(tw for _, _, tw in line) + space * (len(line) - 1))) / 2
        for i, t, tw in line:
            d.text((x, y), t, font=f, fill=(*accent, 255) if i == active else (255, 255, 255, 255),
                   stroke_width=stroke, stroke_fill=(0, 0, 0, 240))
            x += tw + space
        y += f.size + 10
    box = img.getbbox()
    if not box:
        return None
    a = np.asarray(img.crop(box)).astype(np.float32)
    return a[..., :3], a[..., 3:4] / 255.0, box[0], box[1]


def burn_captions(src, out, words: list[tuple[float, float, str]], accent=(255, 205, 60), upper: bool = True,
                  font_path: str = "", size=None) -> int:
    """Burn word-by-word captions into a video; the word being spoken lights up. Returns how many captions."""
    info = probe(src)
    size = size or (info["w"] // 2 * 2, info["h"] // 2 * 2)
    fps = min(60.0, info["fps"] or FPS)
    cues = []                                           # (start, end, group texts, [(word start, index)])
    groups = word_groups(words)
    for gi, g in enumerate(groups):
        end = max(g[-1][1], g[0][0] + 0.35)
        if gi + 1 < len(groups):
            end = min(end + 0.25, groups[gi + 1][0][0])
        texts = [re.sub(r"\s+", " ", x[2]).strip() for x in g]
        cues.append((g[0][0], end, [t.upper() if upper else t for t in texts], [x[0] for x in g]))
    cache: dict = {}
    wr = Writer(out, size, fps, extra_inputs=["-i", src], extra_args=["-map", "0:v", "-map", "1:a?", "-c:a", "copy",
                                                                      "-shortest", "-movflags", "+faststart"])
    try:
        for n, frame in enumerate(frames(src, size, fps)):
            t = n / fps
            cue = next((c for c in cues if c[0] <= t < c[1]), None)
            if cue:
                active = max((i for i, st in enumerate(cue[3]) if st <= t), default=0)
                key = (cue[0], active)
                if key not in cache:
                    if len(cache) > 40:
                        cache.clear()
                    cache[key] = _caption_sprite(cue[2], active, size, accent, font_path)
                sp = cache[key]
                if sp:
                    rgb, alpha, x, y = sp
                    frame = frame.copy()
                    region = frame[y:y + rgb.shape[0], x:x + rgb.shape[1]].astype(np.float32)
                    rh, rw = region.shape[:2]
                    frame[y:y + rh, x:x + rw] = (rgb[:rh, :rw] * alpha[:rh, :rw] + region * (1 - alpha[:rh, :rw]))
            wr.write(frame)
    finally:
        wr.close()
    return len(cues)


# ── sound: the beat of the music ──────────────────────────
def beat_grid(music, max_seconds: float = 90.0) -> tuple[float, float] | None:
    """(seconds per beat, time of the first beat) of a piece of music — None when it has no clear pulse."""
    rate, hop = 11025, 128
    p = subprocess.run([ffmpeg.exe(), "-hide_banner", "-loglevel", "error", "-i", str(music), "-t", str(max_seconds),
                        "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0
    if len(x) < rate * 6:
        return None
    n = len(x) // hop
    energy = np.log1p(1000 * (x[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    onset = np.maximum(0, np.diff(energy, prepend=energy[0]))
    onset = np.convolve(onset, [0.25, 0.5, 0.25], mode="same")
    if onset.std() < 1e-6:
        return None
    onset = (onset - onset.mean()) / onset.std()
    hop_s = hop / rate

    def comb(period: float) -> tuple[float, float]:
        """(how strongly the music pulses at this period, when its first beat falls, how sure that is)."""
        lag = period / hop_s
        beats = int((n - 1) / lag) - 1
        if beats < 6:
            return -1.0, 0.0, 0.0
        offs = np.arange(int(lag))
        idx = np.rint(offs[:, None] + np.arange(beats)[None, :] * lag).astype(int)
        scores = onset[idx].mean(axis=1)
        k = int(np.argmax(scores))
        return float(scores[k]), k * hop_s, float(scores[k]) * math.sqrt(beats)

    best, period, phase, sure = -1.0, 0.0, 0.0, 0.0
    for bpm in np.arange(70, 180.01, 0.25):
        sc, ph, z = comb(60.0 / bpm)
        sc *= math.exp(-0.5 * ((bpm - 118) / 50) ** 2)            # people hear the beat near 120
        if sc > best:
            best, period, phase, sure = sc, 60.0 / bpm, ph, z
    if sure < 8.0:                                                # no clear pulse (ambient, speech, noise)
        return None
    return period, phase


def audio_filter(total: float, voice_at: float | None, has_music: bool, music_volume: float = 0.55) -> tuple[str, str]:
    """ffmpeg filter for the sound bed. Inputs: [music] and/or [voice]. Returns (filter, output label)."""
    parts = []
    fade = f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, total - 1.6):.2f}:d=1.6"
    if has_music:
        parts.append(f"[music]atrim=0:{total:.2f},asetpts=PTS-STARTPTS,volume={music_volume},{fade},"
                     "aformat=sample_rates=44100:channel_layouts=stereo[m0]")
    if voice_at is not None:
        ms = int(voice_at * 1000)
        parts.append(f"[voice]aformat=sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms},apad,"
                     f"atrim=0:{total:.2f}[v0]")
    if has_music and voice_at is not None:              # the music dips whenever the voice speaks
        parts.append("[v0]asplit=2[v1][vk];[m0][vk]sidechaincompress=threshold=0.02:ratio=10:attack=25:release=450"
                     "[md];[md][v1]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    elif has_music:
        parts.append("[m0]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    elif voice_at is not None:
        parts.append("[v0]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")
    return ";".join(parts), "[aout]"


def cut_lengths(count: int, per_shot: float, grid: tuple[float, float] | None, first_cut_after: float = 0.0,
                min_total: float = 0.0) -> list[float]:
    """How long each shot is seen for. With a beat grid the cuts land on beats."""
    if count <= 0:
        return []
    per_shot = max(per_shot, min_total / count if min_total else 0)
    if not grid:
        return [per_shot] * count
    period, phase = grid
    while period < 0.33:
        period *= 2
    beats = max(2, round(per_shot / period))
    while beats * period * count < min_total:
        beats += 1
    step = beats * period
    # the first cut lands on a beat: nudge the first shot so (intro + first shot) ends on the grid
    first_end = first_cut_after + step
    k = math.ceil((first_end - phase) / period - 1e-6)
    first = step + (phase + k * period - first_end)
    return [first] + [step] * (count - 1)


# ── putting a reel together ───────────────────────────────
def assemble(clips: list[tuple[Path, float]], out, size, transition: str = "fade", tdur: float = 0.4,
             overlays: list[tuple[Path, float, float]] | None = None, music=None, voice=None,
             voice_at: float = 0.0, bars: bool = False, grain: bool = True, music_volume: float = 0.55) -> float:
    """Join clips with transitions, lay titles over them, finish with grain/bars and mix the sound.
    clips: (file, seconds) · overlays: (png, from second, to second). Returns the length of the result."""
    kind = TRANSITIONS.get((transition or "fade").lower(), "fade")
    if kind == "cut" or len(clips) < 2:
        tdur = 0.0
    tdur = min(tdur, min(d for _, d in clips) / 2.5) if tdur else 0.0
    total = sum(d for _, d in clips) - tdur * (len(clips) - 1)
    args, chain = [], []
    for f, _ in clips:
        args += ["-i", f]
    last = "0:v"
    if len(clips) > 1 and tdur:
        at = 0.0
        for i in range(1, len(clips)):
            at += clips[i - 1][1] - tdur
            chain.append(f"[{last}][{i}:v]xfade=transition={kind}:duration={tdur:.3f}:offset={at:.3f}[x{i}]")
            last = f"x{i}"
    elif len(clips) > 1:
        chain.append("".join(f"[{i}:v]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[x0]")
        last = "x0"
    idx = len(clips)
    for n, (png, a, b) in enumerate(overlays or []):
        args += ["-loop", "1", "-t", f"{total:.2f}", "-i", png]
        chain.append(f"[{idx}:v]format=rgba,fade=t=in:st={a:.2f}:d=0.45:alpha=1,"
                     f"fade=t=out:st={max(a, b - 0.45):.2f}:d=0.45:alpha=1[o{n}]")
        chain.append(f"[{last}][o{n}]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[y{n}]")
        last, idx = f"y{n}", idx + 1
    fin = finish_filter(size, bars=bars, grain=grain, vignette=False)
    chain.append(f"[{last}]{fin},fade=t=in:d=0.35,fade=t=out:st={max(0.0, total - 0.6):.2f}:d=0.6,format=yuv420p[vout]")
    amap = []
    if music or voice:
        if music:
            args += ["-stream_loop", "-1", "-i", music]
            chain.append(f"[{idx}:a]anull[music]")
            idx += 1
        if voice:
            args += ["-i", voice]
            chain.append(f"[{idx}:a]anull[voice]")
            idx += 1
        af, label = audio_filter(total, voice_at if voice else None, bool(music), music_volume)
        chain.append(af)
        amap = ["-map", label, "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    ffmpeg.run([*args, "-filter_complex", ";".join(chain), "-map", "[vout]", *amap, "-c:v", "libx264", "-preset",
                "veryfast", "-crf", "19", "-t", f"{total:.2f}", "-movflags", "+faststart", out], timeout=1800)
    return total


def finish(src, out, look: str = "", bars: bool = True, grain: bool = True, speed: float = 1.0, steady: bool = False,
           logo: Path | None = None) -> None:
    """The cinematic finish on a whole video: grade, vignette, grain, bars — optionally slower and steadier."""
    info = probe(src)
    size = (info["w"], info["h"])
    speed = max(0.25, min(2.0, float(speed or 1.0)))
    vf = []
    if steady:
        vf.append("deshake=rx=32:ry=32")
    if speed != 1.0:
        vf.append(f"setpts=PTS/{speed:g}")
    vf += [grade_filter(look), finish_filter(size, bars=bars, grain=grain)]
    args = ["-i", src]
    if logo:
        args += ["-i", logo]
        graph = f"[0:v]{','.join(vf)}[g];[g][1:v]overlay=0:0,format=yuv420p[v]"
    else:
        graph = f"[0:v]{','.join(vf)},format=yuv420p[v]"
    audio = []
    if info["audio"]:
        if speed == 1.0:
            audio = ["-map", "0:a", "-c:a", "copy"]
        elif speed >= 0.5:
            graph += f";[0:a]atempo={speed:g}[a]"
            audio = ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    ffmpeg.run([*args, "-filter_complex", graph, "-map", "[v]", *audio, "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "19", "-movflags", "+faststart", out], timeout=3600)
