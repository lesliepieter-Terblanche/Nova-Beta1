"""One-time switch-on after an update, so new things work without a trip through Settings.

v2.34: the skills library and Real-ESRGAN are downloaded, Playwright MCP is added (when Node.js is there), and
God's Eye View — removed in this version — is cleaned off the PC. Each step runs once; a step that can't run yet
(no internet, no Node.js) is tried again at the next start. What still needs you (API keys, Postiz) is listed in
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
        missing = [name for name, key in FREE_KEYS if not os.environ.get(key, "").strip()]
        if missing and not done.get("told_keys"):
            todo.append(f"add a free key for {', '.join(missing)} in Settings → API keys for more free AI use")
        if not os.environ.get("POSTIZ_API_KEY", "").strip() and not done.get("told_keys"):
            todo.append("social posting needs Postiz set up once (docs/POSTIZ.md)")
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
