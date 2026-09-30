"""ComfyUI (github.com/comfyanonymous/ComfyUI): free local image generation on your own graphics card.

ComfyUI runs as its own app (ComfyUI Desktop, or the portable build) on http://127.0.0.1:8188. Nova sends it a
workflow, waits, and fetches the pictures. By default Nova builds a simple text-to-image workflow for whatever
checkpoint model you have; for fancier looks, export any workflow from ComfyUI with "Export (API)" and point
media.comfyui.workflow at it — use {{prompt}}, {{negative}}, {{seed}}, {{width}}, {{height}} as placeholders.
"""
from __future__ import annotations

import copy
import json
import random
import subprocess
import time
import uuid
from pathlib import Path

import httpx

from . import context
from .config import resolve

NEGATIVE = "blurry, low quality, watermark, text, logo, deformed, extra fingers, bad anatomy"
SIZES = {"square": (1, 1), "landscape": (16, 9), "vertical": (9, 16), "portrait": (4, 5), "banner": (3, 1)}


def cfg() -> dict:
    media = (context.cfg or {}).get("media") or {}
    return dict(media.get("comfyui") or {})


def base() -> str:
    return str(cfg().get("url", "http://127.0.0.1:8188")).rstrip("/")


def running() -> bool:
    try:
        return httpx.get(f"{base()}/system_stats", timeout=2).status_code == 200
    except Exception:
        return False


def ensure_running(wait: float = 90) -> bool:
    """Start ComfyUI with media.comfyui.start_command (e.g. its run_nvidia_gpu.bat) if it isn't up yet."""
    if running():
        return True
    cmd = str(cfg().get("start_command", "")).strip()
    if not cmd:
        return False
    path = Path(cmd.strip('"'))
    subprocess.Popen(cmd if not path.exists() else [str(path)], cwd=str(path.parent) if path.exists() else None,
                     shell=not path.exists(), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    t0 = time.time()
    while time.time() - t0 < wait:
        if running():
            return True
        time.sleep(2)
    return False


def checkpoints() -> list[str]:
    r = httpx.get(f"{base()}/object_info/CheckpointLoaderSimple", timeout=10)
    r.raise_for_status()
    info = r.json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"]
    names = info[0] if info and isinstance(info[0], list) else (info[1].get("options", []) if len(info) > 1 else [])
    return list(names)


def pick_checkpoint() -> str:
    want = str(cfg().get("checkpoint", "")).strip()
    names = checkpoints()
    if not names:
        raise RuntimeError("ComfyUI has no checkpoint models yet — put one in ComfyUI/models/checkpoints "
                           "(for a 4 GB card: an SD 1.5 model or SDXL-Turbo).")
    if want:
        match = next((n for n in names if want.lower() in n.lower()), None)
        if match:
            return match
    return names[0]


def profile(ckpt: str) -> dict:
    """Sensible settings per model family."""
    n = ckpt.lower()
    if "turbo" in n or "lightning" in n or "schnell" in n:
        return {"steps": 4 if "turbo" in n else 6, "cfg": 1.0 if "turbo" in n else 1.5, "sampler": "euler_ancestral",
                "scheduler": "normal" if "turbo" in n else "sgm_uniform", "long": 512 if "turbo" in n else 1024}
    if "xl" in n or "sdxl" in n or "pony" in n or "illustrious" in n:
        return {"steps": 25, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras", "long": 1024}
    return {"steps": 25, "cfg": 7.0, "sampler": "dpmpp_2m", "scheduler": "karras", "long": 768}


def size_for(fmt: str, long_side: int) -> tuple[int, int]:
    rw, rh = SIZES.get(fmt, SIZES["square"])
    if rw >= rh:
        w, h = long_side, long_side * rh / rw
    else:
        w, h = long_side * rw / rh, long_side
    return int(w) // 8 * 8, max(64, int(h) // 8 * 8)


def default_workflow(prompt: str, negative: str, ckpt: str, width: int, height: int, seed: int, batch: int = 1) -> dict:
    p = profile(ckpt)
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": p["steps"], "cfg": p["cfg"],
                                                   "sampler_name": p["sampler"], "scheduler": p["scheduler"],
                                                   "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0],
                                                   "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "nova", "images": ["8", 0]}},
    }


def _fill(node, values: dict):
    """Replace {{placeholders}} in a user workflow; a value that is only a placeholder keeps its type."""
    if isinstance(node, dict):
        return {k: _fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill(v, values) for v in node]
    if isinstance(node, str) and "{{" in node:
        for k, v in values.items():
            if node == "{{" + k + "}}":
                return v
            node = node.replace("{{" + k + "}}", str(v))
    return node


def user_workflow(prompt: str, negative: str, width: int, height: int, seed: int) -> dict | None:
    path = str(cfg().get("workflow", "")).strip()
    if not path:
        return None
    wf = json.loads(resolve(path).read_text(encoding="utf-8"))
    return _fill(copy.deepcopy(wf), {"prompt": prompt, "negative": negative, "seed": seed, "width": width,
                                     "height": height})


def run(workflow: dict, timeout: float = 600) -> list[bytes]:
    """Queue a workflow and return the PNG bytes of every saved image."""
    r = httpx.post(f"{base()}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"ComfyUI refused the workflow: {r.text[:300]}")
    pid = r.json()["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = httpx.get(f"{base()}/history/{pid}", timeout=15).json()
        if pid in h:
            job = h[pid]
            status = job.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                raise RuntimeError(f"ComfyUI error: {msgs[0][1].get('exception_message', '') if msgs else 'unknown'}")
            out = []
            for node in job.get("outputs", {}).values():
                for img in node.get("images", []):
                    if img.get("type") == "temp":
                        continue
                    v = httpx.get(f"{base()}/view", params={"filename": img["filename"],
                                                            "subfolder": img.get("subfolder", ""),
                                                            "type": img.get("type", "output")}, timeout=60)
                    v.raise_for_status()
                    out.append(v.content)
            return out
        time.sleep(1.0)
    raise TimeoutError("ComfyUI took too long (is the model too big for the graphics card?)")


def generate(prompt: str, fmt: str = "square", count: int = 1, negative: str = "", seed: int | None = None,
             out_dir: Path | None = None) -> list[Path]:
    if not ensure_running():
        raise RuntimeError("ComfyUI isn't running. Start ComfyUI (or set its start command in Settings → Images).")
    seed = random.randint(1, 2**31 - 1) if seed is None else seed
    neg = negative or str(cfg().get("negative", NEGATIVE))
    ckpt = pick_checkpoint() if not cfg().get("workflow") else ""
    w, h = size_for(fmt, profile(ckpt)["long"] if ckpt else 1024)
    wf = user_workflow(prompt, neg, w, h, seed) or default_workflow(prompt, neg, ckpt, w, h, seed, max(1, min(4, count)))
    images = run(wf)
    out_dir = out_dir or resolve("workspace/images")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    paths = []
    for i, data in enumerate(images[:max(1, count)], 1):
        p = out_dir / f"img_{stamp}_{i}.png"
        p.write_bytes(data)
        paths.append(p)
    return paths
