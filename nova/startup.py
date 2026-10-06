"""How long Nova takes to start, and where the time goes (v2.38.1).

Each stage of start-up notes when it finished. The "Nova is ready" block shows the total and the slowest stages,
the last start is kept in data/startup.json, and "why are you slow to start?" reads it back.
"""
from __future__ import annotations

import json
import threading
import time

T0 = time.time()
_marks: list[tuple[str, float, float]] = []       # (stage, seconds it took, seconds since launch when it finished)
_last = {"main": T0}
_lock = threading.Lock()
BACKGROUND = {"AI model loaded", "voice ready to speak", "listening for the wake word"}   # don't hold the window up
ADVICE = {
    "AI model loaded": "the local AI model is loaded from disk into the graphics card — a smaller model, or a cloud "
                       "model as the everyday one (Settings → Models), starts faster",
    "listening for the wake word": "the speech models load before Nova can hear you — a smaller speech model "
                                   "(Settings → Voice) loads faster",
    "voice ready to speak": "the voice loads before the first spoken reply",
    "phone link (Tailscale)": "Nova waits up to 15 s for Tailscale — open the Tailscale app, or switch the phone "
                              "link off in Settings if you don't use it",
    "add-ons started": "the add-ons (Settings → Extensions) start with Nova — switch off the ones you don't use",
    "memory opened": "the memory database is large or the disk is busy",
    "skills loaded": "many skills and playbooks load at start — switch off unused ones in Settings → Extensions",
}


def mark(stage: str, track: str = "main") -> None:
    """A stage just finished. `track` keeps background work (model loading, voice) apart from the main line."""
    now = time.time()
    with _lock:
        took = now - _last.get(track, T0)
        _last[track] = now
        if stage:                                  # an empty name only starts the clock for a background track
            _marks.append((stage, took, now - T0))


def stages() -> list[dict]:
    with _lock:
        return [{"stage": s, "seconds": round(t, 1), "at": round(a, 1)} for s, t, a in _marks]


def report(rows: list[dict] | None = None) -> str:
    """Plain words: how long the last start took and what was slow."""
    rows = stages() if rows is None else rows
    if not rows:
        return "I have no start-up timings yet — they are kept from the next start."
    window = max((r["at"] for r in rows if r["stage"] not in BACKGROUND), default=0)
    ready = max(r["at"] for r in rows)
    slow = sorted((r for r in rows if r["seconds"] >= 2), key=lambda r: -r["seconds"])[:4]
    out = [f"Start-up: the window was up in {window:.0f} s; everything (AI model and voice included) was ready after {ready:.0f} s."]
    if slow:
        out.append("Slowest: " + "; ".join(f"{r['stage']} {r['seconds']:.0f} s" for r in slow) + ".")
        tip = next((ADVICE[r["stage"]] for r in slow if r["stage"] in ADVICE), "")
        if tip:
            out.append("Why: " + tip + ".")
    return " ".join(out)


def save(path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0)), "stages": stages()},
                                   indent=1), encoding="utf-8")
    except OSError:
        pass


def last(path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("stages", [])
    except (OSError, ValueError):
        return []
