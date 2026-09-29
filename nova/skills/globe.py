"""God's Eye View: a live 3D globe with real aircraft, ships, satellites, earthquakes, weather and public cameras.

Nova installs and runs the open-source God's Eye View app (github.com/bilawalsidhu/gods-eye-view, MIT) on
localhost, flies it anywhere by voice ("show me Johannesburg in night vision"), and answers questions itself
from the same free public feeds: planes overhead (adsb.lol), earthquakes (USGS) and the ISS position.
"""
from __future__ import annotations

import atexit
import datetime as dt
import math
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import httpx

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("globe", ["globe", "god's eye", "gods eye", "god eye", "earth view", "satellite view", "planes",
                         "plane", "aircraft", "flights", "flight", "overhead", "flying", "ship", "ships", "vessel",
                         "satellite", "satellites", "iss", "space station", "earthquake", "earthquakes", "quake",
                         "seismic", "night vision", "thermal", "flir", "fly me to", "fly to"])

REPO = "https://github.com/bilawalsidhu/gods-eye-view.git"
STYLES = {"normal": "normal", "night vision": "nvg", "nvg": "nvg", "surveillance": "nvg", "thermal": "flir",
          "flir": "flir", "infrared": "flir", "crt": "crt", "retro": "crt", "noir": "noir", "black and white": "noir",
          "snow": "snow", "anime": "anime", "cartoon": "anime"}
VIEWS = {"street": (600, -25), "close": (1500, -35), "city": (14000, -45), "region": (160000, -60),
         "country": (1400000, -75), "continent": (5500000, -85), "globe": (18000000, -90)}

_proc: subprocess.Popen | None = None
_installing = threading.Event()


# ── config & state ────────────────────────────────────────
def _cfg() -> dict:
    return dict((context.cfg or {}).get("globe") or {})


def app_dir() -> Path:
    return resolve(_cfg().get("dir", "tools/gods-eye-view"))


def port() -> int:
    return int(_cfg().get("port", 4173))


def base_url() -> str:
    return f"http://localhost:{port()}"


def installed() -> bool:
    d = app_dir()
    return (d / "package.json").exists() and (d / "node_modules").exists()


def running() -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port()}/", timeout=1.5).status_code < 500
    except Exception:
        return False


def _exe(name: str) -> str | None:
    if platform.system() == "Windows":
        return shutil.which(f"{name}.cmd") or shutil.which(f"{name}.exe") or shutil.which(name)
    return shutil.which(name)


def node_version() -> tuple[int, str] | None:
    node = _exe("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out.lstrip("v").split(".")[0]), out
    except Exception:
        return None


def node_ok() -> tuple[bool, str]:
    v = node_version()
    if not v:
        return False, ("Node.js isn't installed. Install Node 24 LTS from nodejs.org (or run "
                       "`winget install OpenJS.NodeJS.LTS`), then ask me again.")
    major, text = v
    if major in (24, 26):
        return True, text
    return False, (f"God's Eye View needs Node.js 24 or 26 — you have {text}. Install Node 24 LTS from nodejs.org, "
                   "then ask me again.")


def status() -> dict:
    ok, node = node_ok()
    return {"installed": installed(), "running": running(), "installing": _installing.is_set(),
            "node_ok": ok, "node": node, "url": base_url(), "dir": str(app_dir())}


def _log_path() -> Path:
    p = resolve("data/logs/globe.log")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _run(cmd: list[str], cwd: Path | None, log) -> int:
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0   # type: ignore[attr-defined]
    return subprocess.run(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags).returncode


# ── install / update / start / stop ───────────────────────
def install(background: bool = True) -> str:
    if installed():
        return "God's Eye View is already installed."
    if _installing.is_set():
        return "Already installing God's Eye View — I'll tell you when it's ready."
    ok, msg = node_ok()
    if not ok:
        return msg
    git, npm = _exe("git"), _exe("npm")
    if not git or not npm:
        return "I need Git and npm to install it (npm comes with Node.js). Install them, then ask again."

    def work():
        _installing.set()
        d = app_dir()
        try:
            with open(_log_path(), "a", encoding="utf-8") as log:
                if not (d / "package.json").exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    if _run([git, "clone", "--depth", "1", REPO, str(d)], None, log) != 0:
                        raise RuntimeError("git clone failed")
                if _run([npm, "ci", "--no-audit", "--no-fund"], d, log) != 0:
                    raise RuntimeError("npm ci failed")
            done = "God's Eye View is installed. Say 'open the globe' to start it."
            if context.store:
                context.store.log("globe", "system", "God's Eye View installed", str(d))
        except Exception as e:
            done = f"Installing God's Eye View failed ({e}). Details are in data/logs/globe.log."
        finally:
            _installing.clear()
        context.push(("🌐 " if "installed" in done else "⚠️ ") + done)
        if context.announce:
            context.announce(done)

    if background:
        threading.Thread(target=work, daemon=True, name="globe-install").start()
        return ("Installing God's Eye View now — it downloads a few hundred megabytes and takes a few minutes. "
                "I'll tell you when it's ready.")
    work()
    return "Installed." if installed() else "Install failed — see data/logs/globe.log."


def start(wait: float = 60) -> str:
    global _proc
    if running():
        return f"God's Eye View is running at {base_url()}."
    if not installed():
        return "God's Eye View isn't installed yet. Say 'install God's Eye View' first."
    ok, msg = node_ok()
    if not ok:
        return msg
    npm = _exe("npm")
    log = open(_log_path(), "a", encoding="utf-8")
    log.write(f"\n=== start {dt.datetime.now():%Y-%m-%d %H:%M} ===\n")
    log.flush()
    flags = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0   # type: ignore[attr-defined]
    _proc = subprocess.Popen([npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port()), "--strictPort"],
                             cwd=app_dir(), stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                             env={**os.environ, "BROWSER": "none", **_remote_env()})
    atexit.register(stop)          # don't leave the globe running after Nova closes
    end = time.time() + wait
    while time.time() < end:
        if running():
            return f"God's Eye View started at {base_url()}."
        if _proc.poll() is not None:
            return "God's Eye View stopped straight away — see data/logs/globe.log for why."
        time.sleep(1)
    return "God's Eye View is still starting; give it a few more seconds."


def _remote_env() -> dict:
    """Let the globe answer on this PC's Tailscale address too (Vite blocks unknown host names)."""
    try:
        from ..remote import status as ts_status
        name = ts_status()["dns_name"]
        return {"__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS": name} if name else {}
    except Exception:
        return {}


def stop() -> str:
    global _proc
    if _proc and _proc.poll() is None:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(_proc.pid)], capture_output=True)
        else:
            _proc.terminate()
        _proc = None
        return "God's Eye View stopped."
    return "God's Eye View isn't running from Nova." + (" (It's running separately.)" if running() else "")


def update() -> str:
    if not installed():
        return "It isn't installed yet."
    was_running = running()
    stop()
    with open(_log_path(), "a", encoding="utf-8") as log:
        if _run([_exe("git"), "pull", "--ff-only"], app_dir(), log) != 0 or \
           _run([_exe("npm"), "ci", "--no-audit", "--no-fund"], app_dir(), log) != 0:
            return "Updating God's Eye View failed — see data/logs/globe.log."
    return "God's Eye View updated." + (" " + start() if was_running else "")


# ── links ─────────────────────────────────────────────────
def view_url(lat: float, lon: float, view: str = "city", style: str = "normal", hud: bool = False,
             heading: float = 0) -> str:
    alt, pitch = VIEWS.get(view, VIEWS["city"])
    parts = [f"lat={lat:.5f}", f"lon={lon:.5f}", f"alt={alt}", f"heading={heading:g}", f"pitch={pitch}",
             f"style={STYLES.get(style.lower().strip(), 'normal')}"]
    if hud:
        parts += ["hud=tactical", "hv=1"]
    return f"{base_url()}/#" + "&".join(parts)


def _place(place: str) -> dict:
    from .weather import geocode
    place = (place or (context.cfg.get("assistant") or {}).get("city") or "").strip()
    if not place:
        raise ValueError("Which place?")
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", place)
    if m:
        return {"name": place, "label": place, "lat": float(m.group(1)), "lon": float(m.group(2))}
    return geocode(place)


def _open(url: str) -> None:
    webbrowser.open(url)


# ── public data (works without the app) ───────────────────
def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def aircraft_near(lat: float, lon: float, radius_km: float = 50) -> list[dict]:
    nm = max(1, min(250, round(radius_km / 1.852)))
    r = httpx.get(f"https://api.adsb.lol/v2/lat/{lat:.4f}/lon/{lon:.4f}/dist/{nm}", timeout=20,
                  headers={"User-Agent": "Nova-assistant"})
    r.raise_for_status()
    out = []
    for a in r.json().get("ac") or []:
        if a.get("lat") is None:
            continue
        alt = a.get("alt_baro")
        out.append({"callsign": (a.get("flight") or "").strip() or a.get("r") or a.get("hex", "?"),
                    "type": a.get("desc") or a.get("t") or "", "reg": a.get("r") or "",
                    "alt_ft": alt if isinstance(alt, (int, float)) else (0 if alt == "ground" else None),
                    "speed_kt": a.get("gs"), "km": round(_km(lat, lon, a["lat"], a["lon"]), 1),
                    "military": bool((a.get("dbFlags") or 0) & 1)})
    out.sort(key=lambda x: x["km"])
    return out


def earthquakes_near(lat: float | None, lon: float | None, radius_km: float, days: int, min_mag: float) -> list[dict]:
    params = {"format": "geojson", "orderby": "time", "limit": 50, "minmagnitude": min_mag,
              "starttime": (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%d")}
    if lat is not None:
        params.update(latitude=lat, longitude=lon, maxradiuskm=min(radius_km, 20001))
    r = httpx.get("https://earthquake.usgs.gov/fdsnws/event/1/query", params=params, timeout=20)
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p, (qlon, qlat, depth) = f["properties"], f["geometry"]["coordinates"]
        out.append({"mag": p.get("mag"), "place": p.get("place") or "", "lat": qlat, "lon": qlon,
                    "depth_km": depth, "when": dt.datetime.fromtimestamp(p["time"] / 1000).strftime("%a %d %b %H:%M"),
                    "url": p.get("url")})
    return out


# ── tools ─────────────────────────────────────────────────
@tool(group="globe")
def show_on_globe(place: str = "", view: str = "city", style: str = "normal", hud: bool = False) -> str:
    """Open the God's Eye View 3D globe (live planes, ships, satellites, quakes, cameras) flying to a place.
    Starts the app if needed. Use for "show me X on the globe", "fly me to X", "God's eye view of X".
    Args:
        place: city, landmark, address or "lat, lon"; empty = home city
        view: street, close, city, region, country, continent or globe
        style: normal, night vision, thermal, crt, noir, snow or anime
        hud: show the military-style heads-up display
    """
    try:
        g = _place(place)
    except Exception as e:
        return f"ERROR: {e}"
    if not running():
        if not installed():
            return ("God's Eye View isn't installed yet. Ask me to 'install God's Eye View' (needs Node.js 24) "
                    "and I'll set it up.")
        msg = start()
        if not running():
            return msg
    url = view_url(g["lat"], g["lon"], view, style, hud)
    _open(url)
    context.record("scrape", f"Globe: {g['label']}", url, f"{view} view, {style}")
    return f"Showing {g['label']} on the globe ({view} view, {style})."


@tool(group="globe")
def planes_overhead(place: str = "", radius_km: float = 40, show: bool = True) -> str:
    """List live aircraft near a place right now (public ADS-B data from adsb.lol, no key needed),
    and optionally show them on the 3D globe.
    Args:
        place: city, airport or "lat, lon"; empty = home city
        radius_km: search radius in km (max ~460)
        show: also open God's Eye View there if it's installed
    """
    try:
        g = _place(place)
        planes = aircraft_near(g["lat"], g["lon"], radius_km)
    except Exception as e:
        return f"ERROR: couldn't get live flights ({e})."
    if show and installed():
        try:
            if running() or "started" in start():
                _open(view_url(g["lat"], g["lon"], "region", "normal"))
        except Exception:
            pass
    if not planes:
        return f"No aircraft are broadcasting within {radius_km:g} km of {g['name']} right now."
    flying = [p for p in planes if p["alt_ft"]]
    lines = []
    for p in planes[:8]:
        alt = f"{p['alt_ft']:,} ft" if p["alt_ft"] else "on the ground"
        kind = f" ({p['type']})" if p["type"] else ""
        mil = " — military" if p["military"] else ""
        lines.append(f"{p['callsign']}{kind}, {p['km']} km away, {alt}{mil}")
    mil_n = sum(p["military"] for p in planes)
    head = (f"{len(planes)} aircraft within {radius_km:g} km of {g['name']}: {len(flying)} flying, "
            f"{len(planes) - len(flying)} on the ground" + (f", {mil_n} military" if mil_n else "") + ". Closest: ")
    return head + "; ".join(lines) + "."


@tool(group="globe")
def recent_earthquakes(place: str = "", radius_km: float = 1000, days: int = 7, min_magnitude: float = 2.5) -> str:
    """Recent earthquakes from USGS (free). Near a place, or worldwide if place is "world".
    Args:
        place: city/country, "world" for everywhere, empty = home city
        radius_km: how far from the place
        days: how many days back (max 30)
        min_magnitude: smallest magnitude to include
    """
    try:
        if (place or "").strip().lower() in ("world", "worldwide", "global", "everywhere"):
            g, quakes = {"name": "the world"}, earthquakes_near(None, None, 0, min(days, 30), max(min_magnitude, 4.5))
        else:
            g = _place(place)
            quakes = earthquakes_near(g["lat"], g["lon"], radius_km, min(days, 30), min_magnitude)
    except Exception as e:
        return f"ERROR: couldn't reach USGS ({e})."
    if not quakes:
        return f"No earthquakes of magnitude {min_magnitude:g}+ near {g['name']} in the last {days} days."
    top = sorted(quakes, key=lambda q: -(q["mag"] or 0))[:5]
    parts = [f"magnitude {q['mag']:.1f} {q['place']} on {q['when']}" for q in top]
    return f"{len(quakes)} earthquakes near {g['name']} in the last {days} days. Strongest: " + "; ".join(parts) + "."


@tool(group="globe")
def where_is_the_iss(show: bool = True) -> str:
    """Where the International Space Station is right now (free live data), optionally shown on the globe."""
    try:
        d = httpx.get("https://api.wheretheiss.at/v1/satellites/25544", timeout=15).json()
        lat, lon = float(d["latitude"]), float(d["longitude"])
    except Exception as e:
        return f"ERROR: couldn't get the ISS position ({e})."
    near = ""
    try:
        r = httpx.get("https://api.wheretheiss.at/v1/coordinates/" + f"{lat},{lon}", timeout=10).json()
        if r.get("country_code") and r["country_code"] != "??":
            near = f" over {r.get('timezone_id', '').split('/')[-1].replace('_', ' ') or r['country_code']}"
    except Exception:
        pass
    if show and running():
        _open(view_url(lat, lon, "continent"))
    return (f"The ISS is at {abs(lat):.1f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"
            f"{near or ' over the ocean'}, {d.get('altitude', 420):.0f} km up, moving at "
            f"{d.get('velocity', 27600):,.0f} km/h.")


@tool(group="globe")
def globe_app(action: str = "status") -> str:
    """Manage the God's Eye View app: status, start, stop, open or update.
    Args:
        action: status, start, stop, open or update
    """
    a = (action or "status").lower().strip()
    if a == "start":
        return start()
    if a == "stop":
        return stop()
    if a == "update":
        return update()
    if a == "open":
        msg = start() if not running() else ""
        if running():
            _open(base_url())
            return "Opened God's Eye View."
        return msg
    s = status()
    if s["installing"]:
        return "God's Eye View is installing right now."
    if not s["installed"]:
        return "God's Eye View isn't installed. " + ("Say 'install God's Eye View'." if s["node_ok"] else s["node"])
    return f"God's Eye View is installed and {'running at ' + s['url'] if s['running'] else 'not running'}."


@tool(group="globe", confirm=True)
def install_globe() -> str:
    """Download and install the God's Eye View 3D globe app (open source, a few hundred MB, needs Node.js 24)."""
    return install()
