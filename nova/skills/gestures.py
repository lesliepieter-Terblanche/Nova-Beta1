"""Gesture control by voice: switch hand-gesture control on or off and hear what each gesture does."""
from __future__ import annotations

from ..tools import register_group, tool

register_group("gestures", ["gesture", "gestures", "hand control", "hand tracking", "hands", "air mouse",
                            "wave", "webcam control"])


@tool(group="gestures")
def gesture_control(action: str = "status") -> str:
    """Turn webcam hand-gesture control on or off, or say what the gestures do.
    Args:
        action: on, off, status or help
    """
    from ..gestures import EMOJI, LABEL, engine
    e = engine()
    a = (action or "status").lower().strip()
    if a in ("on", "start", "enable"):
        return e.start()
    if a in ("off", "stop", "disable"):
        return e.stop()
    if a == "help":
        acts = e.actions()
        names = {"stop": "stop talking", "yes": "say yes", "no": "say no", "listen": "start listening",
                 "escape": "press Escape", "dashboard": "turn the 3D brain", "none": "nothing"}
        parts = [f"{EMOJI[g]} {LABEL[g]}: {names.get(act, act)}" for g, act in acts.items() if g in LABEL]
        return "; ".join(parts) + "; 👉 point moves the mouse and 🤏 pinch clicks or drags."
    s = e.status()
    if not s["enabled"]:
        return "Gesture control is off." + (f" Last problem: {s['error']}" if s["error"] else " Say 'turn on gestures'.")
    return f"Gesture control is on{', I can see your hand' if s['hand'] else ''} ({s['fps']} frames a second)."
