"""AI video on this PC (v2.39): real moving shots from a photo or a sentence, free, with Wan 2.2 in ComfyUI.

  set-up     "check my new card" — looks at the graphics card, its driver, memory and disk, then installs
             ComfyUI (the official portable build for NVIDIA), two add-ons, and the Wan 2.2 models that fit the
             card. Everything downloads in the background; you get a message as each part is done.
  models     16 GB card ("final"):  Wan 2.2 14B photo-to-video and text-to-video (fp8) with the 4-step
                                    lightx2v speed-ups — the best free quality — plus the 5B for quick drafts
             8–15 GB card ("draft"): the Wan 2.2 5B text/photo-to-video model only
  clips      a photo or a sentence → a 5-second moving shot (720p-class), smoothed to 30 frames a second and
             handed to the cinematic reel, which grades, cuts, captions and brands it as before

While a clip renders, Nova's own AI models are moved out of the graphics card to make room, and come back after.
ComfyUI runs as its own window on http://127.0.0.1:8188.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import httpx

from . import context
from .config import ROOT, resolve

HF = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/"
PORTABLE = "https://github.com/comfyanonymous/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia.7z"
ADDONS = [("ComfyUI-Manager", "https://github.com/Comfy-Org/ComfyUI-Manager"),
          ("ComfyUI-VideoHelperSuite", "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite")]
# (folder, file, size in GB, which set-ups need it)
MODELS = [
    ("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors", 10.0, ("draft", "final")),
    ("vae", "wan2.2_vae.safetensors", 1.4, ("draft", "final")),
    ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6.7, ("draft", "final")),
    ("diffusion_models", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", 14.3, ("final",)),
    ("diffusion_models", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", 14.3, ("final",)),
    ("loras", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", 1.2, ("final",)),
    ("loras", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", 1.2, ("final",)),
    ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors", 14.3, ("final",)),
    ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors", 14.3, ("final",)),
    ("loras", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", 1.2, ("final",)),
    ("loras", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors", 1.2, ("final",)),
    ("vae", "wan_2.1_vae.safetensors", 0.25, ("final",)),
]
MIN_DRIVER = 580          # the portable ComfyUI ships PyTorch for CUDA 13, which needs NVIDIA driver 580 or newer
NEGATIVE = ("blurry, low quality, jpeg artifacts, static, frozen, still image, subtitles, text, watermark, logo, "
            "deformed, disfigured, extra fingers, bad hands, bad face, morphing, flicker, overexposed, washed out")
_lock = threading.Lock()                  # one clip at a time on the graphics card
_setup_thread: threading.Thread | None = None


# ── what's in this PC ─────────────────────────────────────
def _run(cmd: list[str], timeout: float = 20) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags).stdout


def gpu() -> dict | None:
    """The NVIDIA card: {"name", "vram_gb", "driver"}; None when there's none (or no driver)."""
    try:
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            try:
                return {"name": parts[0], "vram_gb": round(float(parts[1]) / 1024, 1), "driver": parts[2]}
            except ValueError:
                continue
    return None


def ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception:
        return 0.0


def tier(card: dict | None) -> str:
    """'final' (16 GB-class card: the 14B models), 'draft' (8–15 GB: the 5B model) or 'none'."""
    if not card:
        return "none"
    if card["vram_gb"] >= 15:
        return "final"
    return "draft" if card["vram_gb"] >= 7.5 else "none"


def driver_ok(card: dict) -> bool:
    try:
        return int(str(card["driver"]).split(".")[0]) >= MIN_DRIVER
    except (ValueError, KeyError):
        return False


def needed(level: str) -> list[tuple[str, str, float]]:
    return [(f, n, s) for f, n, s, t in MODELS if level in t]


# ── where ComfyUI lives ───────────────────────────────────
def cfg() -> dict:
    return dict(((context.cfg or {}).get("media") or {}).get("comfyui") or {})


def home() -> Path:
    """The portable folder (the one with run_nvidia_gpu.bat in it)."""
    d = str(cfg().get("dir") or "").strip()
    return Path(d) if d else resolve("tools/ComfyUI_windows_portable")


def comfy_dir() -> Path:
    h = home()
    return h / "ComfyUI" if (h / "ComfyUI").exists() or not (h / "main.py").exists() else h


def model_path(folder: str, name: str) -> Path:
    return comfy_dir() / "models" / folder / name


def missing(level: str) -> list[tuple[str, str, float]]:
    return [(f, n, s) for f, n, s in needed(level) if not model_path(f, n).exists()]


def _state_file() -> Path:
    return resolve("data/video_ai.json")


def state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(**changes) -> dict:
    s = {**state(), **changes, "updated": dt.datetime.now().isoformat(timespec="seconds")}
    _state_file().parent.mkdir(parents=True, exist_ok=True)
    _state_file().write_text(json.dumps(s, indent=1), encoding="utf-8")
    return s


def level() -> str:
    """What this PC can make now: 'final', 'draft' or '' (not set up)."""
    s = state()
    want = s.get("tier", "")
    if want not in ("final", "draft") or not (comfy_dir() / "main.py").exists():
        return ""
    if not missing(want):
        return want
    return "draft" if want == "final" and not missing("draft") else ""


def ready() -> bool:
    return bool(level())


# ── downloading ───────────────────────────────────────────
def download(url: str, dest: Path, say=None, label: str = "") -> Path:
    """A big file, resumable: a broken download carries on where it stopped next time."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=httpx.Timeout(60, read=300)) as r:
        if r.status_code == 416:                                   # already complete
            part.replace(dest)
            return dest
        r.raise_for_status()
        if have and r.status_code != 206:                          # the server started over
            have = 0
        total = have + int(r.headers.get("content-length") or 0)
        told = int(100 * have / total) // 25 if total else 0
        with open(part, "ab" if have else "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                have += len(chunk)
                if say and total:
                    q = int(100 * have / total) // 25
                    if q > told and q < 4:
                        told = q
                        say(f"{label or dest.name}: {q * 25}% downloaded")
    part.replace(dest)
    return dest


def _extract_7z(archive: Path, into: Path) -> None:
    """Windows' own tar reads .7z files; py7zr is the fallback."""
    into.mkdir(parents=True, exist_ok=True)
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["tar", "-xf", str(archive), "-C", str(into)], capture_output=True, text=True,
                           timeout=3600, creationflags=flags)
        if r.returncode == 0:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    import sys
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "py7zr"], timeout=600, check=False)
    import py7zr
    with py7zr.SevenZipFile(archive, "r") as z:
        z.extractall(into)


def _python() -> list[str]:
    """ComfyUI's own Python (the portable build brings one)."""
    p = home() / "python_embeded" / "python.exe"
    return [str(p)] if p.exists() else []


def install_addon(name: str, url: str) -> str:
    target = comfy_dir() / "custom_nodes" / name
    if target.exists():
        return "there"
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(target)], capture_output=True, text=True, timeout=600)
        ok = r.returncode == 0
    else:
        ok = False
    if not ok:                                                     # no git: the repository as a zip
        import io
        import zipfile
        data = httpx.get(url + "/archive/refs/heads/main.zip", follow_redirects=True, timeout=300).content
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            top = z.namelist()[0].split("/")[0]
            tmp = Path(tempfile.mkdtemp(prefix="node_"))
            z.extractall(tmp)
            shutil.move(str(tmp / top), str(target))
    req = target / "requirements.txt"
    if req.exists() and _python():
        subprocess.run([*_python(), "-m", "pip", "install", "-q", "-r", str(req)], capture_output=True, timeout=1800)
    return "installed"


# ── the set-up ────────────────────────────────────────────
def free_gb() -> float:
    """Free space on the drive ComfyUI goes on."""
    where = home()
    while not where.exists() and where != where.parent:
        where = where.parent
    return shutil.disk_usage(where if where.exists() else ROOT).free / 1024 ** 3


def check() -> dict:
    """What the card can do and what's missing, without changing anything."""
    card = gpu()
    t = tier(card)
    free = free_gb()
    need_gb = sum(s for _, _, s in missing(t)) + (0 if (comfy_dir() / "main.py").exists() else 6) if t != "none" else 0
    return {"gpu": card, "ram_gb": ram_gb(), "tier": t, "driver_ok": bool(card and driver_ok(card)),
            "comfy": (comfy_dir() / "main.py").exists(), "missing": missing(t) if t != "none" else [],
            "free_gb": round(free, 1), "need_gb": round(need_gb, 1), "ready": level()}


def report(c: dict | None = None) -> str:
    c = c or check()
    card = c["gpu"]
    if not card:
        return ("I can't see an NVIDIA graphics card. If the new card is in, install its driver (NVIDIA app or "
                "nvidia.com → Drivers, choose the Studio driver), restart the PC, and ask me again.")
    lines = [f"Graphics card: {card['name']} with {card['vram_gb']:.0f} GB, driver {card['driver']}. "
             f"Memory: {c['ram_gb']:.0f} GB. Free disk: {c['free_gb']:.0f} GB."]
    if c["tier"] == "none":
        lines.append("That card has too little memory for AI video (8 GB or more is needed). Cinematic reels keep "
                     "using 3D photo moves.")
        return " ".join(lines)
    lines.append("It can run the best free video models (Wan 2.2 14B)." if c["tier"] == "final" else
                 "It can run the quick Wan 2.2 5B video model.")
    if not c["driver_ok"]:
        lines.append(f"The driver is too old for the AI video engine — install the newest NVIDIA Studio driver "
                     f"(version {MIN_DRIVER} or newer), restart, and ask me again.")
    if c["tier"] == "final" and 0 < c["ram_gb"] < 24:
        lines.append("With under 24 GB of memory the 14B models will be slow; 32 GB is what they want.")
    if c["ready"]:
        lines.append("AI video is set up and ready.")
    elif c["need_gb"]:
        lines.append(f"To set up: about {c['need_gb']:.0f} GB to download"
                     + (" — not enough free disk space; free some up first." if c["free_gb"] < c["need_gb"] + 10 else "."))
    if card["vram_gb"] >= 15:
        lines.append("Tip: this card also fits bigger thinking models — say \"use the bigger models\" and I'll switch "
                     "to qwen2.5:14b every day and gemma3:12b for deeper thinking.")
    return " ".join(lines)


def set_up(say=None) -> dict:
    """Install whatever is missing for this card. Long: run it in the background (start_setup)."""
    say = say or (lambda m: context.push("🎬 " + m))
    c = check()
    if not c["gpu"] or c["tier"] == "none":
        _save(stage="stopped", error=report(c))
        say(report(c))
        return state()
    if not c["driver_ok"]:
        _save(stage="stopped", error="driver too old", gpu=c["gpu"])
        say(report(c))
        return state()
    if c["free_gb"] < c["need_gb"] + 10:
        _save(stage="stopped", error="not enough disk space", gpu=c["gpu"])
        say(f"Not enough disk space for AI video: about {c['need_gb']:.0f} GB is needed plus room to work, and "
            f"{c['free_gb']:.0f} GB is free. Free some space, then say \"set up AI video\" again.")
        return state()
    _save(stage="starting", tier=c["tier"], gpu=c["gpu"], error="")
    say(f"Setting up AI video for your {c['gpu']['name']}: about {c['need_gb']:.0f} GB to download. "
        "It carries on in the background and picks up where it left off if it's interrupted.")
    try:
        if not c["comfy"]:
            _save(stage="ComfyUI")
            archive = download(PORTABLE, resolve("tools/ComfyUI_windows_portable_nvidia.7z"), say, "ComfyUI")
            say("ComfyUI downloaded — unpacking it…")
            _extract_7z(archive, home().parent)
            archive.unlink(missing_ok=True)
            if not (comfy_dir() / "main.py").exists():
                raise RuntimeError(f"ComfyUI unpacked, but not where I expected it ({home()})")
        _save(stage="add-ons")
        for name, url in ADDONS:
            try:
                install_addon(name, url)
            except Exception as e:                                  # an add-on is a nice-to-have
                print(f"[videoai] {name} not installed: {e}")
        for folder, name, size in missing(c["tier"]):
            _save(stage=f"downloading {name}")
            say(f"Downloading {name} ({size:.1f} GB)…")
            download(HF + f"{folder}/{name}", model_path(folder, name), say, name)
        _point_nova_at_comfy()
        _save(stage="testing")
        took = test_render()
        _save(stage="ready", ready=True, test_seconds=took)
        say(f"AI video is ready ✓ — a test clip rendered in {took:.0f} s. Cinematic reels now turn photos into real "
            "moving shots; or ask me for an AI video clip of anything.")
    except Exception as e:
        _save(stage="stopped", error=str(e)[:400])
        say(f"AI video set-up stopped: {e}. Say \"set up AI video\" to carry on from where it stopped.")
    return state()


def start_setup() -> str:
    global _setup_thread
    if _setup_thread and _setup_thread.is_alive():
        return "The AI video set-up is already running — I'll message you as each part finishes."
    c = check()
    _setup_thread = threading.Thread(target=set_up, daemon=True, name="videoai-setup")
    _setup_thread.start()
    return report(c) + (" Starting the set-up now in the background — I'll message you as each part finishes."
                        if c["tier"] != "none" and c["driver_ok"] and not c["ready"] else "")


def _point_nova_at_comfy() -> None:
    """Tell Nova (config.yaml) where ComfyUI is and how to start it."""
    from . import settings
    bat = home() / "run_nvidia_gpu.bat"
    if (home() / "python_embeded").exists():           # Nova's own launcher: the same, without opening a browser tab
        bat = home() / "nova_comfyui.bat"
        bat.write_text("@echo off\ncd /d \"%~dp0\"\ntitle ComfyUI (Nova)\n"
                       ".\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build "
                       "--disable-auto-launch\n", encoding="utf-8")
    with settings._lock:
        doc = settings.load_doc()
        settings.set_path(doc, "media.comfyui.dir", str(home()))
        if bat.exists():
            settings.set_path(doc, "media.comfyui.start_command", str(bat))
        settings.save_doc(doc)
    media = context.cfg.setdefault("media", {})
    media.setdefault("comfyui", {}).update({"dir": str(home()), **({"start_command": str(bat)} if bat.exists() else {})})


# ── the graphics card is shared: Nova's own models step aside ──
def free_vram() -> list[str]:
    """Unload Ollama's models so the video model has the whole card. Returns what was unloaded."""
    base = "http://localhost:11434"
    try:
        loaded = [m["name"] for m in httpx.get(f"{base}/api/ps", timeout=5).json().get("models", [])]
    except Exception:
        return []
    for m in loaded:
        try:
            httpx.post(f"{base}/api/generate", json={"model": m, "keep_alive": 0}, timeout=30)
        except Exception:
            pass
    return loaded


def reload_llm() -> None:
    try:
        if context.llm:
            threading.Thread(target=lambda: context.llm.warm_up(quiet=True), daemon=True).start()
    except Exception:
        pass


# ── workflows ─────────────────────────────────────────────
def size_for(fmt: str, lvl: str) -> tuple[int, int]:
    """Width × height the model renders at (it is scaled to 1080p after)."""
    if lvl == "final":
        return {"vertical": (576, 1024), "landscape": (1024, 576), "square": (768, 768)}.get(fmt, (576, 1024))
    return {"vertical": (704, 1280), "landscape": (1280, 704), "square": (960, 960)}.get(fmt, (704, 1280))


def frames_for(seconds: float, fps: int) -> int:
    n = int(round(max(1.0, min(seconds, 8.0)) * fps))
    return n - n % 4 + 1                                           # the models want 4k+1 frames


def wf_14b(prompt: str, w: int, h: int, length: int, seed: int, image: str = "", negative: str = NEGATIVE) -> dict:
    """Wan 2.2 14B with the 4-step lightx2v LoRAs: photo-to-video when `image` (a ComfyUI input file) is given,
    else text-to-video. Settings as in ComfyUI's own Wan 2.2 templates."""
    kind = "i2v" if image else "t2v"
    lora = "v1" if kind == "i2v" else "v1.1"
    g = {
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                                                        "type": "wan", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["clip", 0]}},
    }
    for part in ("high", "low"):
        g[f"unet_{part}"] = {"class_type": "UNETLoader", "inputs": {
            "unet_name": f"wan2.2_{kind}_{part}_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}}
        g[f"lora_{part}"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": [f"unet_{part}", 0], "strength_model": 1.0,
            "lora_name": f"wan2.2_{kind}_lightx2v_4steps_lora_{lora}_{part}_noise.safetensors"}}
        g[f"shift_{part}"] = {"class_type": "ModelSamplingSD3", "inputs": {"model": [f"lora_{part}", 0], "shift": 5.0}}
    if image:
        g["img"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        g["latent"] = {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["pos", 0], "negative": ["neg", 0], "vae": ["vae", 0], "width": w, "height": h,
            "length": length, "batch_size": 1, "start_image": ["img", 0]}}
        pos, neg, lat = ["latent", 0], ["latent", 1], ["latent", 2]
    else:
        g["latent"] = {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": w, "height": h, "length": length,
                                                                            "batch_size": 1}}
        pos, neg, lat = ["pos", 0], ["neg", 0], ["latent", 0]
    common = {"steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": pos, "negative": neg}
    g["sample_high"] = {"class_type": "KSamplerAdvanced", "inputs": {
        **common, "model": ["shift_high", 0], "add_noise": "enable", "noise_seed": seed, "latent_image": lat,
        "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable"}}
    g["sample_low"] = {"class_type": "KSamplerAdvanced", "inputs": {
        **common, "model": ["shift_low", 0], "add_noise": "disable", "noise_seed": seed,
        "latent_image": ["sample_high", 0], "start_at_step": 2, "end_at_step": 4, "return_with_leftover_noise": "disable"}}
    g["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample_low", 0], "vae": ["vae", 0]}}
    g["save"] = {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "nova_clip"}}
    return g


def wf_5b(prompt: str, w: int, h: int, length: int, seed: int, image: str = "", negative: str = NEGATIVE) -> dict:
    """Wan 2.2 5B (text or photo to video, 24 frames a second) — the quick model."""
    g = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors",
                                                        "weight_dtype": "default"}},
        "shift": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["unet", 0], "shift": 8.0}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                                                        "type": "wan", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["clip", 0]}},
        "latent": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"vae": ["vae", 0], "width": w, "height": h,
                                                                       "length": length, "batch_size": 1}},
        "sample": {"class_type": "KSampler", "inputs": {
            "model": ["shift", 0], "seed": seed, "steps": 20, "cfg": 5.0, "sampler_name": "uni_pc",
            "scheduler": "simple", "positive": ["pos", 0], "negative": ["neg", 0], "latent_image": ["latent", 0],
            "denoise": 1.0}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": "nova_clip"}},
    }
    if image:
        g["img"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        g["latent"]["inputs"]["start_image"] = ["img", 0]
    return g


def check_nodes(workflow: dict, info: dict) -> list[str]:
    """Problems with a workflow against ComfyUI's own list of nodes (/object_info): missing nodes, unknown inputs,
    model files it doesn't have. Empty = fine."""
    out = []
    for nid, node in workflow.items():
        cls = node["class_type"]
        spec = info.get(cls)
        if spec is None:
            out.append(f"ComfyUI has no '{cls}' node — update ComfyUI (its update folder → update_comfyui.bat)")
            continue
        inputs = {**(spec.get("input", {}).get("required") or {}), **(spec.get("input", {}).get("optional") or {})}
        for k, v in node["inputs"].items():
            if k not in inputs:
                out.append(f"'{cls}' has no input '{k}'")
            elif isinstance(v, str) and k.endswith("_name"):
                opts = _options(inputs[k])
                if opts is not None and v not in opts:
                    out.append(f"ComfyUI can't find the model file {v}")
    return out


def _options(spec) -> list | None:
    """The choices of a drop-down input in /object_info (old style: [[...], {}]; new style: ["COMBO", {"options"}])."""
    if not isinstance(spec, list) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return list(spec[1].get("options") or [])
    return None


def upload(image: Path) -> str:
    """Put a photo in ComfyUI's input folder; returns the name to use in LoadImage."""
    from . import comfy
    name = f"nova_{uuid.uuid4().hex[:8]}{image.suffix.lower() or '.png'}"
    with open(image, "rb") as f:
        r = httpx.post(f"{comfy.base()}/upload/image", files={"image": (name, f, "image/png")},
                       data={"overwrite": "true"}, timeout=120)
    r.raise_for_status()
    return r.json().get("name", name)


def frames_to_video(pngs: list[bytes], out: Path, fps: int, smooth_to: int = 30) -> Path:
    """The rendered frames → an mp4, smoothed to `smooth_to` frames a second (motion-compensated)."""
    from . import ffmpeg
    work = Path(tempfile.mkdtemp(prefix="clipframes_"))
    try:
        for i, b in enumerate(pngs):
            (work / f"f{i:05d}.png").write_bytes(b)
        vf = f"minterpolate=fps={smooth_to}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" if smooth_to > fps else "null"
        ffmpeg.run(["-framerate", str(fps), "-i", str(work / "f%05d.png"), "-vf", f"{vf},format=yuv420p",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "16", out])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def clip(prompt: str, out: Path, image: Path | None = None, seconds: float = 5.0, fmt: str = "vertical",
         quality: str = "final", seed: int | None = None) -> dict:
    """Render one AI video clip. Returns {"path", "seconds", "model", "took"}. Raises with a plain reason."""
    from . import comfy
    lvl = level()
    if not lvl:
        raise RuntimeError("AI video isn't set up yet — say \"check my graphics card\" / \"set up AI video\"")
    use = "final" if quality != "draft" and lvl == "final" else "draft"
    w, h = size_for(fmt, use)
    fps = 16 if use == "final" else 24
    length = frames_for(seconds, fps)
    seed = int(seed if seed is not None else time.time() * 1000) % (2 ** 31)
    with _lock:
        unloaded = free_vram()
        t0 = time.time()
        try:
            if not comfy.ensure_running(wait=240):
                raise RuntimeError("ComfyUI didn't start — open its window (run_nvidia_gpu.bat) to see why")
            img = upload(image) if image else ""
            wf = (wf_14b if use == "final" else wf_5b)(prompt, w, h, length, seed, img)
            problems = check_nodes(wf, httpx.get(f"{comfy.base()}/object_info", timeout=60).json())
            if problems:
                raise RuntimeError("; ".join(dict.fromkeys(problems))[:400])
            pngs = comfy.run(wf, timeout=3600)
            if not pngs:
                raise RuntimeError("ComfyUI finished but returned no frames")
            frames_to_video(pngs, out, fps)
        finally:
            if unloaded:
                reload_llm()
    return {"path": out, "seconds": len(pngs) / fps, "model": "Wan 2.2 14B" if use == "final" else "Wan 2.2 5B",
            "took": time.time() - t0}


def test_render() -> float:
    """A tiny clip to prove the whole chain works. Returns the seconds it took."""
    out = resolve("data/video_ai_test.mp4")
    r = clip("a calm ocean wave rolling onto a sandy beach at golden hour, cinematic", out, seconds=1.0,
             fmt="landscape", quality="draft", seed=1)
    return r["took"]


def scene_prompt(photo: Path, title: str = "", move: str = "push_in") -> str:
    """A short motion prompt for a photo: what's in it (when a vision model can see it) and a camera move."""
    camera = {"push_in": "slow cinematic dolly push-in", "pull_out": "slow cinematic pull-back",
              "orbit_left": "slow orbit to the left", "orbit_right": "slow orbit to the right",
              "rise": "slow crane up"}.get(move, "slow cinematic camera move")
    seen = ""
    try:
        if context.llm:
            seen = (context.llm.see(str(photo), "Describe this photo in one short sentence for a video model: the "
                                                "subject, the setting and the light. No opinions.") or "").strip()
            if seen.lower().startswith(("no vision", "error")):
                seen = ""
    except Exception:
        seen = ""
    bits = [seen or (title or "the scene in the photo"), camera,
            "gentle natural motion, realistic, shallow depth of field, premium commercial look, stable, smooth"]
    return ", ".join(b.rstrip(".") for b in bits if b)


def estimate_minutes(count: int) -> int:
    per = {"final": 4.0, "draft": 2.5}.get(level(), 4.0)
    return max(1, int(round(count * per)))

