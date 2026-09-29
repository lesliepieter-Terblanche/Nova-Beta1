"""ffmpeg helper. imageio-ffmpeg ships a free ffmpeg binary, so nothing to install by hand."""
from __future__ import annotations

import shutil
import subprocess


def exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], timeout: int = 900) -> None:
    cmd = [exe(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.strip()[-800:]}")


def to_wav16k(src, dst) -> None:
    run(["-i", src, "-ac", "1", "-ar", "16000", dst])


def to_voice_note(src, dst) -> None:
    """Telegram voice notes must be OGG/Opus."""
    run(["-i", src, "-c:a", "libopus", "-b:a", "48k", dst])


def duration(path) -> float:
    """Media duration in seconds (parsed from ffmpeg's banner)."""
    import re
    p = subprocess.run([exe(), "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
