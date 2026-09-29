"""Downloads the offline backup voice (Piper) — used when ElevenLabs is unreachable."""
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
VOICE = "en/en_GB/alan/medium/en_GB-alan-medium"
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"

out_dir = ROOT / "voices"
out_dir.mkdir(exist_ok=True)
for ext in (".onnx", ".onnx.json"):
    dest = out_dir / (Path(VOICE).name + ext)
    if dest.exists():
        print(f"already have {dest.name}")
        continue
    print(f"downloading {dest.name} …")
    with httpx.stream("GET", BASE + VOICE + ext, follow_redirects=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
print("Piper backup voice ready.")
