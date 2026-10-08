"""The graphics card (v2.39): check it, set up free AI video for it, and use the bigger thinking models it fits.

  "check my new graphics card"  → what it is, what it can run, what's missing — then the AI video set-up starts
  "how far is the AI video set-up?"
  "use the bigger models"       → qwen2.5:14b every day and gemma3:12b for deeper thinking (16 GB cards)
"""
from __future__ import annotations

import threading

from .. import context, videoai
from ..tools import register_group, tool

register_group("hardware", ["graphics card", "video card", "gpu", "new card", "rtx", "nvidia", "vram",
                            "ai video", "video ai", "set up video", "wan 2.2", "comfyui", "bigger models",
                            "bigger model", "larger models", "smaller models"])

BIG = {"everyday": "nova-qwen14", "deep": "gemma3:12b"}
SMALL = {"everyday": "nova-qwen", "deep": "gemma3:4b"}


@tool(group="hardware")
def check_graphics_card(set_up: bool = True) -> str:
    """Check the graphics card (name, memory, driver), the PC's memory and disk, and what AI video it can run; then
    set up free local AI video for it in the background (ComfyUI + the Wan 2.2 models that fit). Use after a new
    graphics card is installed, or for "set up AI video".
    Args:
        set_up: also start the set-up (downloads in the background); false = only report
    """
    return videoai.start_setup() if set_up else videoai.report()


@tool(group="hardware")
def video_ai_status() -> str:
    """How far the AI video set-up is, or whether AI video is ready."""
    s = videoai.state()
    if videoai.ready():
        return (f"AI video is ready ({'Wan 2.2 14B — best quality' if videoai.level() == 'final' else 'Wan 2.2 5B'})."
                + (f" The test clip took {s['test_seconds']:.0f} s." if s.get("test_seconds") else ""))
    if not s:
        return "AI video isn't set up. Say \"check my graphics card\" to start."
    if s.get("stage") == "stopped":
        return f"The AI video set-up stopped: {s.get('error') or 'unknown reason'}. Say \"set up AI video\" to carry on."
    return f"Setting up AI video — now at: {s.get('stage', 'starting')} (last update {s.get('updated', '?')[11:16]})."


def _switch(models: dict) -> list[str]:
    from .. import activate, settings
    with settings._lock:
        doc = settings.load_doc()
        settings.set_path(doc, "llm.providers.ollama.model", models["everyday"])
        settings.set_path(doc, "llm.providers.ollama_deep.model", models["deep"])
        settings.save_doc(doc)
    done = []
    cfg = {"llm": {"providers": {"ollama": {"model": models["everyday"]}}}}
    done.append(activate.ensure_everyday_model(cfg))
    done.append(activate.ensure_model(models["deep"]))
    return done


@tool(group="hardware")
def use_bigger_models(back_to_small: bool = False) -> str:
    """Switch Nova's local thinking models to the bigger ones a 16 GB graphics card fits — qwen2.5:14b every day and
    gemma3:12b for deeper thinking — or back to the small ones (qwen2.5:3b and gemma3:4b). Downloads what's missing
    (about 17 GB for the big pair) in the background; the switch takes effect after a restart.
    Args:
        back_to_small: true = go back to qwen2.5:3b and gemma3:4b
    """
    card = videoai.gpu()
    if not back_to_small and (not card or card["vram_gb"] < 15):
        have = f"{card['name']} with {card['vram_gb']:.0f} GB" if card else "no NVIDIA card I can see"
        return f"ERROR: the bigger models need a 16 GB graphics card; this PC has {have}. Nothing was changed."
    models = SMALL if back_to_small else BIG

    def run():
        got = _switch(models)
        ok = all(g in ("there", "built", "downloaded") for g in got)
        context.push(("🧠 " + ("Back on the small models" if back_to_small else "The bigger models are ready")
                      + " — say \"restart\" to start using them.") if ok else
                     f"🧠 The model switch is saved, but not everything is ready: {'; '.join(got)}")
    threading.Thread(target=run, daemon=True, name="bigger-models").start()
    return ("Switching to " + ("qwen2.5:3b and gemma3:4b" if back_to_small else
                               "qwen2.5:14b every day and gemma3:12b for deeper thinking")
            + ". Downloading what's missing in the background — I'll tell you when to restart.")
