"""Sharper photos with Real-ESRGAN (github.com/xinntao/Real-ESRGAN, BSD-3-Clause) — free, on this PC.

The small "ncnn-vulkan" build runs on the graphics card through Vulkan (fine on 4 GB), needs no Python packages
and is downloaded once (45 MB) into tools/realesrgan. A small or soft photo is rebuilt at 4× with real detail
instead of being stretched; cinematic reels use it automatically for photos that are too small for the frame.
When it can't run (no Vulkan graphics driver, no internet for the first download) Nova falls back to a plain
resize, and says so.
"""
from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import zipfile
from pathlib import Path

from .config import resolve

RELEASE = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-{os}.zip"
MODEL = "realesrgan-x4plus"                 # the general-photo model; always 4×
MAX_INPUT_PIXELS = 2_100_000                # bigger than ~1920×1080 doesn't need it (and 4× of it wouldn't fit)
_failed = ""                                # why it couldn't run, so a reel doesn't retry for every photo


def tool_dir() -> Path:
    return resolve("tools/realesrgan")


def exe_path() -> Path:
    return tool_dir() / ("realesrgan-ncnn-vulkan.exe" if platform.system() == "Windows" else "realesrgan-ncnn-vulkan")


def installed() -> bool:
    return exe_path().exists() and (tool_dir() / "models" / f"{MODEL}.bin").exists()


def install() -> Path:
    """Download and unpack the Real-ESRGAN program (once). Returns the program's path."""
    if installed():
        return exe_path()
    import httpx
    name = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "ubuntu")
    d = tool_dir()
    d.mkdir(parents=True, exist_ok=True)
    zpath = d / "download.zip"
    print("[upscale] downloading Real-ESRGAN (45 MB, once)…")
    with httpx.stream("GET", RELEASE.format(os=name), timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        with open(zpath, "wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
    with zipfile.ZipFile(zpath) as z:
        for item in z.infolist():
            n = item.filename
            if n.endswith("/") or n.startswith(("input", "onepiece")) or ".." in n:      # skip the demo files
                continue
            if n.startswith("models/") and MODEL not in n:                              # only the photo model
                continue
            target = d / n
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(item))
    zpath.unlink(missing_ok=True)
    if platform.system() != "Windows":
        exe_path().chmod(0o755)
    if not installed():
        raise RuntimeError("the Real-ESRGAN download didn't contain the program")
    return exe_path()


def available() -> bool:
    return installed() and not _failed


def upscale(src, dst, scale: float = 4.0, timeout: int = 300) -> Path:
    """Rebuild a photo at `scale`× (up to 4) with Real-ESRGAN. Raises RuntimeError when it can't run."""
    global _failed
    from PIL import Image, ImageOps
    src, dst = Path(src), Path(dst)
    exe = install()
    scale = max(1.0, min(4.0, float(scale or 4)))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        tmp_in = dst.with_name(dst.stem + "_in.png")
        im.save(tmp_in)                                         # the right way up, in a format it always reads
    tmp_out = dst.with_name(dst.stem + "_x4.png")
    try:
        p = subprocess.run([str(exe), "-i", str(tmp_in), "-o", str(tmp_out), "-n", MODEL, "-s", "4", "-f", "png"],
                           cwd=str(tool_dir()), capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0 or not tmp_out.exists():
            err = (p.stderr or p.stdout or "").strip().splitlines()
            _failed = err[-1][:200] if err else "it stopped without saying why"
            if "vulkan" in " ".join(err).lower() or "vk" in " ".join(err).lower():
                _failed = "this PC's graphics driver has no Vulkan support (update the NVIDIA driver)"
            raise RuntimeError(f"Real-ESRGAN couldn't run: {_failed}")
        with Image.open(tmp_out) as big:
            big = big.convert("RGB")
            if scale < 4:
                big = big.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            big.save(dst, quality=95)
    except subprocess.TimeoutExpired as e:
        _failed = "it took too long"
        raise RuntimeError("Real-ESRGAN took too long on that photo") from e
    finally:
        tmp_in.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
    return dst


def plain_resize(src, dst, scale: float = 2.0) -> Path:
    """The fallback: a clean Lanczos resize with a touch of sharpening — bigger, not more detailed."""
    from PIL import Image, ImageFilter, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        big = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        big = big.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        big.save(dst, quality=95)
    return Path(dst)


def for_frame(photo, size, mode: str = "auto") -> tuple[Path, bool]:
    """The photo to use for a frame of this size: a sharpened copy when the original is too small to fill it
    (kept in workspace/.upscaled so it is only done once), else the original. Returns (path, was it sharpened).
    mode: auto = only if Real-ESRGAN is installed or can be · off = never."""
    global _failed
    from PIL import Image
    photo = Path(photo)
    if str(mode).lower() in ("off", "false", "no", "never") or _failed:
        return photo, False
    try:
        with Image.open(photo) as im:
            w, h = im.size
    except OSError:
        return photo, False
    need = max(size[0] / w, size[1] / h)                 # how much it must grow to cover the frame
    if need <= 1.15 or w * h > MAX_INPUT_PIXELS:
        return photo, False
    st = photo.stat()
    key = hashlib.sha1(f"{photo.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    cached = resolve("workspace/.upscaled") / f"{photo.stem}_{key}.jpg"
    if cached.exists():
        return cached, True
    try:
        return upscale(photo, cached, 4.0), True
    except Exception as e:
        _failed = _failed or str(e)[:200]                 # don't try again for every photo in the reel
        print(f"[upscale] photos left as they are: {e}")
        return photo, False


def uninstall() -> None:
    shutil.rmtree(tool_dir(), ignore_errors=True)
