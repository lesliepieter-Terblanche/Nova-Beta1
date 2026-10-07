"""One-time switch-on after an update, so new things work without a trip through Settings.

v2.34: the skills library and Real-ESRGAN are downloaded, Playwright MCP is added (when Node.js is there), and
God's Eye View — removed in this version — is cleaned off the PC. v2.34.2: the Windows control, Excel and
ElevenLabs studio add-ons are switched on. Each step runs once; a step that can't run yet
(no internet, no Node.js) is tried again at the next start. What still needs you (API keys) is listed in
one message on the dashboard and Telegram.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from . import context
from .config import ROOT, resolve

MARK = ".activated.json"
FREE_KEYS = [("Cerebras", "CEREBRAS_API_KEY"), ("Mistral", "MISTRAL_API_KEY"), ("GitHub Models", "GITHUB_MODELS_TOKEN")]


def _done(mark_dir: Path) -> dict:
    try:
        return json.loads((Path(mark_dir) / MARK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(mark_dir: Path, done: dict) -> None:
    Path(mark_dir).mkdir(parents=True, exist_ok=True)
    (Path(mark_dir) / MARK).write_text(json.dumps(done, indent=1), encoding="utf-8")


def remove_globe(cfg) -> list[str]:
    """God's Eye View is gone: delete its install folder, its settings and its phone share. Returns what was done."""
    from . import settings
    out = []
    folder = resolve(str((cfg.get("globe") or {}).get("dir") or "tools/gods-eye-view"))
    tools = (ROOT / "tools").resolve()
    if folder.exists() and tools in folder.resolve().parents:        # only ever inside Nova's own tools folder
        shutil.rmtree(folder, ignore_errors=True)
        out.append("deleted its files" if not folder.exists() else "couldn't delete all of its files")
    with settings._lock:
        doc = settings.load_doc()
        changed = False
        if "globe" in doc:
            del doc["globe"]
            changed = True
        off = list(settings.get_path(doc, "skills.disabled", []) or [])
        if "globe" in off:
            settings.set_path(doc, "skills.disabled", [s for s in off if s != "globe"])
            changed = True
        if changed:
            settings.save_doc(doc)
            out.append("removed its settings")
    try:
        from . import remote
        if remote.exe():
            remote._run(["serve", f"--https={remote.OLD_GLOBE_PORT}", "off"])
    except Exception:
        pass
    return out


def add_playwright(cfg) -> bool:
    """Add Microsoft's Playwright MCP to config.yaml, switched on. Needs Node.js (npx). True when it is in place."""
    from . import settings
    if "playwright" in (cfg.get("mcp_servers") or {}):
        return True
    if not shutil.which("npx"):
        return False
    spec = settings.mcp_catalog_spec("playwright")
    with settings._lock:
        doc = settings.load_doc()
        if settings.get_path(doc, "mcp_servers.playwright") is None:
            settings.set_path(doc, "mcp_servers.playwright", spec)
            settings.save_doc(doc)
    cfg.setdefault("mcp_servers", {})["playwright"] = spec          # this start connects to it too
    return True


ADDONS = [("windows", "Windows control"), ("excel", "Excel"), ("elevenlabs", "ElevenLabs studio")]   # v2.34.2
# v2.36: the first two businesses on the Business dashboard, each with two approve-first standing instructions
BUSINESSES = [
    ("TrueHome", "https://www.truehome.co.za",
     "South African property sales and rental platform for buyers, renters, private sellers, landlords and estate "
     "agents. Earns from agent listing fees and private seller fees."),
    ("TackleTrail", "https://www.tackletrail.co.za",
     "South African outdoor adventure platform: fishing and outdoor spots on a map, a community forum and a shop. "
     "Growing traffic and member registrations."),
]


# v2.36.2: added on its own, so it also arrives on a PC that already has the first two
VAPE_ESSENCE = ("VapeEssence", "https://www.vapeessence.co.za",
                "South African online shop (Roodepoort, Johannesburg) selling disposable vapes and nicotine pouches "
                "to adults of 18 and over, delivered countrywide in 48-72 hours. Earns from product sales; has "
                "loyalty points and a refer-a-friend reward. Nicotine is addictive: never sell or market to under-18s.",
                "info@vapeessence.co.za")


def add_vape_essence() -> bool:
    """Add VapeEssence with the same two approve-first standing instructions. False when it is already there."""
    from . import business
    name, url, about, email = VAPE_ESSENCE
    if any(b["id"] == business.slug(name) for b in business.businesses()):
        return False
    b = business.add_business(name, url, about)
    business.set_up(b["id"], email=email)
    business.add_rule(b["id"], "new_lead", "Thank them, answer what they asked, and offer one clear next step. "
                                           "Adults (18+) only; make no health claims.")
    business.add_rule(b["id"], "invoice_overdue", "Polite and firm; ask for a payment date.", after_days=7)
    return True


def set_up_businesses() -> list[str]:
    """Add the first businesses (only when none exist yet). Returns the names added."""
    from . import business
    if business.businesses():
        return []
    for name, url, about in BUSINESSES:
        b = business.add_business(name, url, about)
        business.add_rule(b["id"], "new_lead", "Thank them, answer what they asked, and offer one clear next step.")
        business.add_rule(b["id"], "invoice_overdue", "Polite and firm; ask for a payment date.", after_days=7)
    return [name for name, _, _ in BUSINESSES]


MODELS_MARK = ".local_models_set"
DEEP_MODEL = "gemma3:4b"


def set_local_models(mark_dir: Path) -> bool:
    """One time (v2.38.2): the everyday model is the local qwen2.5:3b (Nova's 'nova-qwen' build of it) and deeper
    thinking goes to the local gemma3:4b first; any cloud models already listed stay behind it as backups.
    Run before the models are set up. After this the choice is yours in Settings → AI brain. True when it changed
    something."""
    from . import settings
    mark = Path(mark_dir) / MODELS_MARK
    if mark.exists():
        return False
    with settings._lock:
        doc = settings.load_doc()
        settings.set_path(doc, "llm.primary", "ollama")
        if "qwen" not in str(settings.get_path(doc, "llm.providers.ollama.model", "nova-qwen")).lower():
            settings.set_path(doc, "llm.providers.ollama.model", "nova-qwen")
        smart = [str(x) for x in (settings.get_path(doc, "llm.smart", []) or []) if str(x) != "ollama_deep"]
        settings.set_path(doc, "llm.smart", ["ollama_deep"] + smart)
        settings.save_doc(doc)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text("everyday: ollama (qwen2.5:3b) | deeper thinking: ollama_deep (gemma3:4b)\n", encoding="utf-8")
    fast = Path(mark_dir) / settings.FAST_MARK                  # and the old "switch to a cloud model" step stays off
    if not fast.exists():
        fast.write_text("the everyday model was chosen by hand\n", encoding="utf-8")
    print("[llm] everyday model: local qwen2.5:3b | deeper thinking: local gemma3:4b (cloud models stay as backups)")
    return True


def ensure_model(name: str = DEEP_MODEL, base: str = "http://localhost:11434") -> str:
    """Make sure Ollama has a model, downloading it when it is missing. Returns 'there', 'downloaded' or the reason
    it couldn't."""
    import httpx
    try:
        have = [m.get("name", "") for m in httpx.get(f"{base}/api/tags", timeout=10).json().get("models", [])]
        if any(h == name or h.startswith(name + "-") or h.split(":")[0] == name for h in have):
            return "there"
        print(f"[llm] downloading {name} for deeper thinking (about 3.3 GB, once)…")
        r = httpx.post(f"{base}/api/pull", json={"name": name, "stream": False}, timeout=3600)
        return "downloaded" if r.status_code < 400 else f"Ollama answered {r.status_code}"
    except Exception as e:
        return f"couldn't reach Ollama: {e}"


def switch_on_addon(cfg, name: str) -> bool:
    """Switch one of the catalogue's MCP add-ons on in config.yaml — adding it, or enabling the entry that is
    already there switched off. These run through uv (uvx). True when it is on."""
    from . import settings
    have = (cfg.get("mcp_servers") or {}).get(name)
    if have and have.get("enabled", True):
        return True
    if not shutil.which("uvx"):
        return False
    spec = settings.mcp_catalog_spec(name)
    with settings._lock:
        doc = settings.load_doc()
        if settings.get_path(doc, f"mcp_servers.{name}") is None:
            settings.set_path(doc, f"mcp_servers.{name}", spec)
        else:
            settings.set_path(doc, f"mcp_servers.{name}.enabled", True)
        settings.save_doc(doc)
    servers = cfg.setdefault("mcp_servers", {})
    if have:
        servers[name]["enabled"] = True
    else:
        servers[name] = spec
    return True


def run(cfg, mark_dir: Path, wait: float = 60.0, background: bool = True) -> dict:
    """Do whatever hasn't been done yet. The config edits happen now (before the MCP servers start); the
    downloads happen in the background so start-up isn't held up."""
    done = _done(mark_dir)
    said = []
    if not done.get("globe_removed"):
        try:
            what = remove_globe(cfg)
            done["globe_removed"] = True
            if what:
                said.append("God's Eye View is removed (" + ", ".join(what) + ").")
        except Exception as e:
            print(f"[activate] God's Eye View clean-up skipped: {e}")
    if not done.get("playwright"):
        try:
            if add_playwright(cfg):
                done["playwright"] = True
                said.append("Playwright browser control is on — say \"use Playwright to…\".")
        except Exception as e:
            print(f"[activate] Playwright not added: {e}")
    if not done.get("businesses"):
        try:
            added = set_up_businesses()
            done["businesses"] = True
            if added:
                said.append(f"The Business dashboard is ready (💼 on the main one) with {' and '.join(added)}.")
        except Exception as e:
            print(f"[activate] businesses not set up: {e}")
    if not done.get("vapeessence"):
        try:
            if add_vape_essence():
                said.append("VapeEssence is added to the Business dashboard, with its own tab.")
            done["vapeessence"] = True
        except Exception as e:
            print(f"[activate] VapeEssence not added: {e}")
    if not done.get("addons"):
        try:
            on = [title for name, title in ADDONS if switch_on_addon(cfg, name)]
            if len(on) == len(ADDONS):
                done["addons"] = True
            if on:
                said.append(f"Switched on: {', '.join(on)} (Settings → Extensions to switch any off).")
        except Exception as e:
            print(f"[activate] add-ons not switched on: {e}")
    _save(mark_dir, done)

    def downloads():
        time.sleep(wait)
        if not done.get("library"):
            try:
                from . import skill_library
                res = skill_library.install()
                done["library"] = True
                on = [n for n in res["added"] if f"lib-{n}" not in res["off"]]
                said.append(f"The skills library is installed ({len(on)} skills ready: {', '.join(on)}).")
            except Exception as e:
                print(f"[activate] skills library not installed yet: {e}")
        if not done.get("deep_model"):
            try:
                got = ensure_model()
                if got in ("there", "downloaded"):
                    done["deep_model"] = True
                    if got == "downloaded":
                        said.append("gemma3:4b is downloaded — I use it for deeper thinking.")
                else:
                    print(f"[activate] gemma3:4b not ready yet: {got}")
            except Exception as e:
                print(f"[activate] gemma3:4b check skipped: {e}")
        if not done.get("upscaler"):
            try:
                from . import upscale
                upscale.install()
                done["upscaler"] = True
                said.append("Real-ESRGAN is installed — small photos in reels are sharpened automatically.")
            except Exception as e:
                print(f"[activate] Real-ESRGAN not installed yet: {e}")
        _save(mark_dir, done)
        todo = []
        if not done.get("playwright"):
            todo.append("Playwright needs Node.js (nodejs.org) — install it and restart me")
        if not done.get("addons") and not shutil.which("uvx") and not done.get("told_keys"):
            todo.append("Windows control, Excel and ElevenLabs studio need 'uv' (run setup.bat again) — then restart me")
        missing = [name for name, key in FREE_KEYS if not os.environ.get(key, "").strip()]
        if missing and not done.get("told_keys"):
            todo.append(f"add a free key for {', '.join(missing)} in Settings → API keys for more free AI use")
        if said or todo:
            done["told_keys"] = True
            _save(mark_dir, done)
            msg = "🆕 " + " ".join(said) if said else "🆕 New in this version:"
            if todo:
                msg += " Still needs you: " + "; ".join(todo) + "."
            context.push(msg)

    if background:
        threading.Thread(target=downloads, daemon=True, name="activate").start()
    else:
        downloads()
    return done
