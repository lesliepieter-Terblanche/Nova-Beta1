"""On-screen cards: when you ask for the weather, PC stats or other information, Nova shows it big in the middle of the
dashboard (on the PC and on your phone) until you close it. Tools call show(); the dashboard picks cards up with its
normal activity poll."""
from __future__ import annotations

import collections
import shutil
import subprocess
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_cards: collections.deque = collections.deque(maxlen=20)
_seq = 0
_last_view = 0.0                 # when a dashboard last asked for news


def show(kind: str, title: str, data: dict | None = None) -> int:
    """Put a card on every open dashboard. kind: weather | pc | screen_time | info."""
    global _seq
    with _lock:
        _seq += 1
        _cards.append({"id": _seq, "t": time.time(), "kind": kind, "title": title, "data": data or {}})
        return _seq


def recent(since: int = 0, max_age: float = 120) -> list[dict]:
    """Cards newer than `since` (and less than two minutes old, so a dashboard opened later doesn't replay them)."""
    global _last_view
    _last_view = time.time()
    now = time.time()
    with _lock:
        return [c for c in _cards if c["id"] > since and now - c["t"] <= max_age]


def latest_id() -> int:
    return _seq


def viewer_active(within: float = 10) -> bool:
    """Is a dashboard open somewhere right now (PC or phone)?"""
    return time.time() - _last_view <= within


# ── live PC stats for the circle gauges ───────────────────
_gpu = {"t": 0.0, "v": None}


def _gpu_stats() -> dict | None:
    if time.time() - _gpu["t"] < 3:
        return _gpu["v"]
    v = None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4).stdout.strip()
        if out:
            name, util, used, total, temp = [x.strip() for x in out.splitlines()[0].split(",")]
            v = {"name": name, "util": float(util), "mem_used_gb": round(float(used) / 1024, 1),
                 "mem_total_gb": round(float(total) / 1024, 1), "mem_percent": round(100 * float(used) / float(total)),
                 "temp": float(temp)}
    except Exception:
        v = None
    _gpu.update(t=time.time(), v=v)
    return v


def pc_stats() -> dict:
    import psutil
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(Path.home().anchor or "/")
    out = {"cpu": psutil.cpu_percent(interval=None if getattr(pc_stats, "_primed", False) else 0.3),
           "cores": psutil.cpu_count(logical=True),
           "ram": vm.percent, "ram_used_gb": round(vm.used / 1e9, 1), "ram_total_gb": round(vm.total / 1e9, 1),
           "disk": round(100 * du.used / du.total), "disk_free_gb": round(du.free / 1e9), "disk_total_gb": round(du.total / 1e9),
           "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 1)}
    pc_stats._primed = True
    try:
        f = psutil.cpu_freq()
        if f:
            out["ghz"] = round(f.current / 1000, 2)
    except Exception:
        pass
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bat:
        out["battery"] = round(bat.percent)
        out["plugged_in"] = bool(bat.power_plugged)
    gpu = _gpu_stats()
    if gpu:
        out["gpu"] = gpu
    return out
