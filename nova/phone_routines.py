"""Saved phone routines: one phrase runs several steps on the phone ("driving mode" → do not disturb on, Bluetooth
on, open Maps, open Spotify). Kept in data/phone_routines.json; make and change them by voice.

A step is plain words. Simple ones (a switch, brightness / volume, open an app, press a key) run instantly; anything
else is done by reading the screen and tapping, like any other phone task — with the usual stop before sending,
paying, buying or deleting.
"""
from __future__ import annotations

import json
import re

from . import phone as ph
from .config import resolve

DEFAULTS = {
    "Driving mode": ["do not disturb on", "bluetooth on", "media volume 70", "open maps"],
    "Bedtime": ["do not disturb on", "brightness 10", "media volume 0", "battery saver on"],
    "Good morning": ["do not disturb off", "battery saver off", "brightness 60", "ring volume 80"],
}


def _path():
    p = resolve("data/phone_routines.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, list[str]]:
    p = _path()
    if not p.exists():
        return {k: list(v) for k, v in DEFAULTS.items()}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): [str(s) for s in v] for k, v in data.items() if isinstance(v, list)}
    except Exception:
        return {k: list(v) for k, v in DEFAULTS.items()}


def _write(data: dict) -> None:
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def steps_of(text: str) -> list[str]:
    """'dnd on, open maps and then open spotify' → the separate steps."""
    parts = re.split(r"\s*(?:;|\n|,| and then | then |(?<!\w)and (?=(?:open|turn|switch|set|put|press|enable|disable)\b))\s*", text)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def key_of(name: str, data: dict | None = None) -> str:
    data = load() if data is None else data
    n = norm(name)
    return next((k for k in data if norm(k) == n), "") or \
        next((k for k in data if norm(k) in (n + " mode", n.removesuffix(" mode"), n.removesuffix(" routine"))), "")


def save(name: str, steps: list[str]) -> str:
    data = load()
    old = key_of(name, data)
    if old:
        data.pop(old)
    data[name.strip().capitalize()] = steps
    _write(data)
    return name.strip().capitalize()


def delete(name: str) -> bool:
    data = load()
    k = key_of(name, data)
    if not k:
        return False
    data.pop(k)
    _write(data)
    return True


LEAD = re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:(?:start|run|activate|do|launch|begin|switch to|"
                  r"go (?:in)?to|turn on|switch on|enable|set|put (?:my|the) phone (?:in|into|on)) )?(?:my |the )?", re.I)
TAIL = re.compile(r"(?: (?:routine|on (?:my|the) phone|please|now))*[.!]?$", re.I)


def find(text: str) -> str:
    """The routine this sentence is asking for ('driving mode', 'start my bedtime routine') — '' when it isn't one."""
    if len(text) > 60:
        return ""
    core = TAIL.sub("", LEAD.sub("", text.strip(), count=1))
    data = load()
    n = norm(core)
    return next((k for k in data if norm(k) == n), "") if n else ""


def run(name: str, p: ph.Phone | None = None) -> str:
    data = load()
    k = key_of(name, data)
    if not k:
        have = ", ".join(data) or "none yet"
        return f"I don't have a phone routine called '{name}'. Saved routines: {have}."
    p = p or ph.phone()
    err = p.connect()
    if err:
        return err
    done, failed = [], []
    for step in data[k]:
        try:
            out = ph.quick(step, p)
            if out is None:
                out = ph.run_task(step, p=p)
            ok = not re.match(r"^(I stopped|I need you|I didn't finish|Your phone is locked|I'm already busy|I can't)", out)
        except Exception as e:
            out, ok = str(e), False
        (done if ok else failed).append(step if ok else f"{step} ({out[:120]})")
    text = f"{k}: " + (f"did {len(done)} of {len(data[k])} steps" if failed else "all done") + \
        (f" — {', '.join(done)}." if done else ".")
    return text + (f" Not done: {'; '.join(failed)}." if failed else "")
