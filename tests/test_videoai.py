"""v2.39: AI video on this PC — the graphics card check, the set-up, Wan 2.2 workflows, clips and AI motion in reels."""
import io
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from nova import context, videoai
from nova.tools import REGISTRY

SMI_5060 = "NVIDIA GeForce RTX 5060 Ti, 16311, 581.42\n"


@pytest.fixture()
def pc(nova, monkeypatch, tmp_path):
    cfg, _ = nova
    cfg.setdefault("media", {})["comfyui"] = {"dir": str(tmp_path / "ComfyUI_windows_portable"), "url": "http://127.0.0.1:8188"}
    monkeypatch.setattr(videoai, "resolve", lambda p: tmp_path / p)
    monkeypatch.setattr(videoai, "_run", lambda cmd, timeout=20: SMI_5060)
    monkeypatch.setattr(videoai, "ram_gb", lambda: 31.8)
    monkeypatch.setattr(videoai, "free_gb", lambda: 400.0)
    told = []
    monkeypatch.setattr(context, "push", lambda text, files=None: told.append((text, files)))
    return {"tmp": tmp_path, "told": told, "cfg": cfg}


def _install_models(level):
    (videoai.comfy_dir()).mkdir(parents=True, exist_ok=True)
    (videoai.comfy_dir() / "main.py").write_text("")
    for folder, name, _ in videoai.needed(level):
        p = videoai.model_path(folder, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_the_new_card_is_recognised(pc, monkeypatch):
    card = videoai.gpu()
    assert card == {"name": "NVIDIA GeForce RTX 5060 Ti", "vram_gb": 15.9, "driver": "581.42"}
    assert videoai.tier(card) == "final" and videoai.driver_ok(card)
    assert videoai.tier({"name": "GTX 1650", "vram_gb": 4.0, "driver": "560.1"}) == "none"
    assert videoai.tier({"name": "RTX 3060", "vram_gb": 12.0, "driver": "581"}) == "draft"
    assert not videoai.driver_ok({"driver": "566.36"})
    c = videoai.check()
    assert c["tier"] == "final" and not c["comfy"] and len(c["missing"]) == 12 and 79 < c["need_gb"] < 90
    said = videoai.report(c)
    assert said.startswith("Graphics card: NVIDIA GeForce RTX 5060 Ti with 16 GB, driver 581.42. Memory: 32 GB.")
    assert "It can run the best free video models (Wan 2.2 14B)." in said and "use the bigger models" in said
    monkeypatch.setattr(videoai, "_run", lambda cmd, timeout=20: "NVIDIA GeForce RTX 5060 Ti, 16311, 566.36\n")
    assert "The driver is too old for the AI video engine" in videoai.report()
    monkeypatch.setattr(videoai, "free_gb", lambda: 50.0)
    monkeypatch.setattr(videoai, "_run", lambda cmd, timeout=20: SMI_5060)
    assert "not enough free disk space" in videoai.report()
    assert videoai.set_up(say=lambda m: None)["error"] == "not enough disk space"
    monkeypatch.setattr(videoai, "_run", lambda cmd, timeout=20: (_ for _ in ()).throw(FileNotFoundError("nvidia-smi")))
    assert videoai.report().startswith("I can't see an NVIDIA graphics card.")


def test_big_files_download_and_resume(pc, monkeypatch):
    import httpx
    blob = bytes(range(256)) * 40
    seen = []

    class Stream:
        def __init__(self, method, url, headers=None, **k):
            start = int((headers or {}).get("Range", "bytes=0-")[6:-1] or 0)
            seen.append(start)
            self.status_code = 206 if start else 200
            self.headers = {"content-length": str(len(blob) - start)}
            self.body = blob[start:]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            yield self.body
    monkeypatch.setattr(httpx, "stream", Stream)
    dest = pc["tmp"] / "models" / "a.safetensors"
    dest.parent.mkdir(parents=True)
    dest.with_suffix(".safetensors.part").write_bytes(blob[:1000])                # an earlier, broken download
    videoai.download("https://example/a", dest)
    assert seen == [1000] and dest.read_bytes() == blob and not dest.with_suffix(".safetensors.part").exists()


def test_set_up_installs_everything_and_points_nova_at_it(pc, monkeypatch):
    from nova import settings
    doc = {}
    monkeypatch.setattr(settings, "load_doc", lambda: doc)
    monkeypatch.setattr(settings, "save_doc", lambda d: None)
    got = []

    def download(url, dest, say=None, label=""):
        got.append(url.rsplit("/", 2)[-2:] if "huggingface" in url else "portable")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        return dest

    def extract(archive, into):
        root = into / "ComfyUI_windows_portable"
        (root / "ComfyUI").mkdir(parents=True)
        (root / "ComfyUI" / "main.py").write_text("")
        (root / "run_nvidia_gpu.bat").write_text("")
    monkeypatch.setattr(videoai, "download", download)
    monkeypatch.setattr(videoai, "_extract_7z", extract)
    monkeypatch.setattr(videoai, "install_addon", lambda n, u: "installed")
    monkeypatch.setattr(videoai, "test_render", lambda: 42.0)
    s = videoai.set_up(say=lambda m: pc["told"].append((m, None)))
    assert s["stage"] == "ready" and s["tier"] == "final" and videoai.ready() and videoai.level() == "final"
    assert got[0] == "portable" and ["diffusion_models", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"] in got
    assert len(got) == 13
    assert doc["media"]["comfyui"]["start_command"].endswith("run_nvidia_gpu.bat")
    msgs = [m for m, _ in pc["told"]]
    assert msgs[0].startswith("Setting up AI video for your NVIDIA GeForce RTX 5060 Ti: about 8") and \
        msgs[-1].startswith("AI video is ready ✓ — a test clip rendered in 42 s.")
    assert "AI video is ready (Wan 2.2 14B — best quality). The test clip took 42 s." == REGISTRY["video_ai_status"].func()


def test_wan_workflows_match_comfyui(pc):
    i2v = videoai.wf_14b("a boat at sunrise", 576, 1024, 81, 7, image="nova_x.png")
    classes = sorted({n["class_type"] for n in i2v.values()})
    assert classes == ["CLIPLoader", "CLIPTextEncode", "KSamplerAdvanced", "LoadImage", "LoraLoaderModelOnly",
                       "ModelSamplingSD3", "SaveImage", "UNETLoader", "VAEDecode", "VAELoader", "WanImageToVideo"]
    assert i2v["sample_high"]["inputs"]["end_at_step"] == 2 and i2v["sample_low"]["inputs"]["start_at_step"] == 2
    assert i2v["sample_low"]["inputs"]["latent_image"] == ["sample_high", 0]
    assert i2v["lora_high"]["inputs"]["lora_name"] == "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
    t2v = videoai.wf_14b("a boat at sunrise", 1024, 576, 81, 7)
    assert t2v["latent"]["class_type"] == "EmptyHunyuanLatentVideo"
    assert t2v["lora_low"]["inputs"]["lora_name"] == "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"
    every_file = {n for _, n, _, _ in videoai.MODELS}
    for wf in (i2v, t2v, videoai.wf_5b("x", 704, 1280, 121, 1, image="a.png")):
        named = {v for n in wf.values() for k, v in n["inputs"].items() if k.endswith("_name") and k != "sampler_name"}
        assert named <= every_file                                          # every model file used is downloaded
    # the inputs as ComfyUI lists them (checked against its source, nodes.py / nodes_wan.py)
    info = {"UNETLoader": {"input": {"required": {"unet_name": [sorted(every_file)], "weight_dtype": [["default"]]}}},
            "CLIPLoader": {"input": {"required": {"clip_name": [sorted(every_file)], "type": [["wan"]]},
                                     "optional": {"device": [["default"]]}}},
            "VAELoader": {"input": {"required": {"vae_name": [sorted(every_file)]}}},
            "CLIPTextEncode": {"input": {"required": {"text": ["STRING"], "clip": ["CLIP"]}}},
            "LoraLoaderModelOnly": {"input": {"required": {"model": ["MODEL"], "lora_name": ["COMBO", {"options": sorted(every_file)}],
                                                           "strength_model": ["FLOAT"]}}},
            "ModelSamplingSD3": {"input": {"required": {"model": ["MODEL"], "shift": ["FLOAT"]}}},
            "LoadImage": {"input": {"required": {"image": [["nova_x.png"]]}}},
            "WanImageToVideo": {"input": {"required": {k: [] for k in ("positive", "negative", "vae", "width", "height", "length", "batch_size")},
                                          "optional": {"clip_vision_output": [], "start_image": []}}},
            "KSamplerAdvanced": {"input": {"required": {k: [] for k in (
                "model", "add_noise", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "positive", "negative",
                "latent_image", "start_at_step", "end_at_step", "return_with_leftover_noise")}}},
            "VAEDecode": {"input": {"required": {"samples": [], "vae": []}}},
            "SaveImage": {"input": {"required": {"images": [], "filename_prefix": []}}}}
    assert videoai.check_nodes(i2v, info) == []
    del info["WanImageToVideo"]
    info["VAELoader"]["input"]["required"]["vae_name"] = [["other.safetensors"]]
    assert videoai.check_nodes(i2v, info) == [
        "ComfyUI can't find the model file wan_2.1_vae.safetensors",
        "ComfyUI has no 'WanImageToVideo' node — update ComfyUI (its update folder → update_comfyui.bat)"]
    assert videoai.frames_for(5, 16) == 81 and videoai.frames_for(5, 24) == 121 and videoai.frames_for(1, 24) == 25


def _png(i):
    b = io.BytesIO()
    Image.new("RGB", (64, 112), (i * 20 % 255, 80, 140)).save(b, "PNG")
    return b.getvalue()


def test_a_clip_is_rendered_with_the_card_to_itself(pc, monkeypatch):
    import httpx

    from nova import comfy
    _install_models("final")
    videoai._save(tier="final")
    queued, unloaded = [], []
    monkeypatch.setattr(videoai, "free_vram", lambda: unloaded.append(1) or ["nova-qwen"])
    monkeypatch.setattr(videoai, "reload_llm", lambda: unloaded.append("back"))
    monkeypatch.setattr(comfy, "ensure_running", lambda wait=90: True)
    monkeypatch.setattr(videoai, "upload", lambda image: "nova_up.png")
    monkeypatch.setattr(videoai, "check_nodes", lambda wf, info: [])
    monkeypatch.setattr(httpx, "get", lambda url, timeout=60: type("R", (), {"json": lambda self: {}})())
    monkeypatch.setattr(comfy, "run", lambda wf, timeout=600: queued.append(wf) or [_png(i) for i in range(17)])
    photo = pc["tmp"] / "boat.jpg"
    Image.new("RGB", (400, 700), (10, 90, 160)).save(photo)
    r = videoai.clip("the boat rocks gently", pc["tmp"] / "clip.mp4", image=photo, seconds=1, fmt="vertical")
    assert r["model"] == "Wan 2.2 14B" and r["path"].exists() and unloaded == [1, "back"]
    wf = queued[0]
    assert wf["img"]["inputs"]["image"] == "nova_up.png" and wf["latent"]["inputs"]["width"] == 576
    from nova import ffmpeg
    assert ffmpeg.duration(r["path"]) == pytest.approx(17 / 16, abs=0.15)
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=r_frame_rate",
                          "-of", "csv=p=0", str(r["path"])], capture_output=True, text=True).stdout.strip()
    assert out == "30/1"                                                        # smoothed from 16 to 30 frames a second
    r = videoai.clip("a dam at dawn", pc["tmp"] / "draft.mp4", seconds=1, quality="draft")
    assert r["model"] == "Wan 2.2 5B" and queued[1]["sample"]["class_type"] == "KSampler" and "img" not in queued[1]
    (videoai.model_path("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors")).unlink()
    assert videoai.level() == "draft"                                           # one big file missing: drafts still work


def test_reels_bring_photos_to_life_and_fall_back_to_3d(pc, monkeypatch):
    from nova import ffmpeg
    from nova.skills import cinematic
    photos = []
    for i in range(2):
        p = pc["tmp"] / f"p{i}.jpg"
        Image.new("RGB", (900, 1400), (30 + i * 90, 100, 150)).save(p)
        photos.append(p)
    calls = []

    def clip(prompt, out, image=None, seconds=5.0, fmt="vertical", quality="final", seed=None):
        calls.append((prompt, image.name, round(seconds, 1)))
        if len(calls) == 2:
            raise RuntimeError("ComfyUI didn't start")
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc=size=576x1024:rate=30", "-t", f"{seconds:.2f}", "-pix_fmt", "yuv420p", out])
        return {"path": Path(out), "seconds": seconds, "model": "Wan 2.2 14B", "took": 1}
    monkeypatch.setattr(cinematic.videoai, "clip", clip)
    monkeypatch.setattr(cinematic.videoai, "scene_prompt", lambda f, title, move: f"{f.name} {move}")
    monkeypatch.setattr(cinematic, "out_dir", lambda: pc["tmp"])
    monkeypatch.setattr(cinematic.upscale, "for_frame", lambda f, size, mode: (f, False))
    monkeypatch.setattr(cinematic, "_files", lambda spec: photos)
    said = cinematic._make_reel(photos, "Harbour", "", "", "", "none", "", "vertical", 2.0, "cut", "", False, True)
    assert calls[0][0] == "p0.jpg push_in" and calls[0][2] >= 3.0
    assert "1 photo brought to life with AI motion (Wan 2.2)" in said
    assert "AI motion stopped part-way (ComfyUI didn't start), so the remaining photos used 3D camera moves." in said
    assert len(calls) == 2                                                     # after a failure it stops asking
    monkeypatch.setattr(cinematic.videoai, "ready", lambda: False)
    assert REGISTRY["cinematic_reel"].func("p0.jpg", motion="ai").startswith("ERROR: AI video isn't set up yet")
    assert REGISTRY["ai_video_clip"].func("a dam").startswith("ERROR: AI video isn't set up yet")


def test_bigger_models_only_on_a_big_card(pc, monkeypatch):
    monkeypatch.setattr(videoai, "_run", lambda cmd, timeout=20: "NVIDIA GeForce GTX 1650, 4096, 560.94\n")
    assert REGISTRY["use_bigger_models"].func().startswith("ERROR: the bigger models need a 16 GB graphics card; this PC has NVIDIA GeForce GTX 1650 with 4 GB")
    from nova.skills import hardware
    switched = []
    monkeypatch.setattr(videoai, "_run", lambda cmd, timeout=20: SMI_5060)
    monkeypatch.setattr(hardware, "_switch", lambda models: switched.append(models) or ["built", "downloaded"])
    assert "qwen2.5:14b every day and gemma3:12b" in REGISTRY["use_bigger_models"].func()
    import time
    for _ in range(50):
        if pc["told"]:
            break
        time.sleep(0.05)
    assert switched == [hardware.BIG] and "The bigger models are ready" in pc["told"][0][0]
    assert json.loads(json.dumps(hardware.SMALL)) == {"everyday": "nova-qwen", "deep": "gemma3:4b"}
