"""Downloads the local voices:
  - Kokoro-82M (natural, ~120 MB)  -> models/
  - Piper en_GB-alan (fast, ~60 MB) -> voices/
Both are used when ElevenLabs is unavailable. Safe to run again (skips what's there).
"""
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    ROOT / "models" / "kokoro-v1.0.int8.onnx":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
    ROOT / "models" / "voices-v1.0.bin":
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
    ROOT / "voices" / "en_GB-alan-medium.onnx":
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
    ROOT / "voices" / "en_GB-alan-medium.onnx.json":
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json",
}

for dest, url in FILES.items():
    dest.parent.mkdir(exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"already have {dest.name}")
        continue
    print(f"downloading {dest.name} …")
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.replace(dest)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        print(f"  failed: {e} (Nova still works; run this script again later)")
print("Local voices ready.")
