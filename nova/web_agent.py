"""browser-use (github.com/browser-use/browser-use): an AI web agent that works websites for you — portals, forms,
searches, comparisons — in its own Chrome window.

browser-use pins exact versions of many packages (httpx, openai, pillow, mcp…) that would clash with Nova's, so it
lives in its own Python (tools/browser-use/venv) and runs as a separate process (nova/web_agent_runner.py).
Settings → Web agent → Install sets it up once. Logins are kept in its own browser profile.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import context, extras
from .config import ROOT

HOME = ROOT / "tools" / "browser-use"
PROFILE = HOME / "profile"
RISKY = ("pay", "buy", "purchase", "order", "checkout", "send", "submit", "delete", "remove", "transfer", "publish",
         "post", "book", "confirm", "sign up", "subscribe", "cancel")

RULES = """SAFETY RULES FROM THE USER'S ASSISTANT (Nova) — follow them exactly:
- Do NOT complete any of these unless listed under ALLOWED: paying, buying, ordering, checking out, sending a message or
  email, submitting or confirming a form, booking, subscribing, posting or publishing, deleting or cancelling, transferring
  money. Instead fill everything in, stop right before the final button and finish with a summary of what is ready and
  which button would complete it.
- Never type passwords, card numbers or one-time codes that the task did not give you. If a site needs the user to log
  in and you are not logged in, stop and say which site needs a login.
- Prefer official sites. Don't accept cookie or marketing extras beyond what is needed.
ALLOWED: {allowed}"""

_state: dict = {"job": None}


def cfg() -> dict:
    return dict(((context.cfg or {}).get("web_agent") or {}))


def python() -> Path:
    return extras.python("browser_use")


def installed() -> bool:
    return extras.installed("browser_use")


def install() -> str:
    return extras.install("browser_use", extras.announce_done)


# ── which AI drives it ────────────────────────────────────
def pick_llm() -> dict:
    want = str(cfg().get("provider", "auto")).lower()
    providers = (((context.cfg or {}).get("llm") or {}).get("providers") or {})
    order = [want] if want != "auto" else ["gemini", "groq", "xai", "ollama"]
    for p in order:
        pc = dict(providers.get(p) or {})
        if p == "ollama":
            base = str(pc.get("base_url", "http://localhost:11434/v1")).removesuffix("/v1")
            return {"provider": "ollama", "model": str(cfg().get("model") or pc.get("model") or "qwen2.5:7b"),
                    "base_url": base}
        key = os.environ.get(str(pc.get("key_env") or f"{p.upper()}_API_KEY"), "").strip()
        if not key:
            continue
        if p == "xai":
            return {"provider": "openai", "model": str(cfg().get("model") or "grok-4-fast"), "api_key": key,
                    "base_url": "https://api.x.ai/v1"}
        model = str(cfg().get("model") or (pc.get("model") if pc.get("model") not in (None, "", "auto") else ""))
        return {"provider": p, "model": model, "api_key": key}
    raise RuntimeError("The web agent needs a Gemini or Groq key (Settings → AI models) — the small local model "
                       "can't drive a browser reliably.")


# ── running a task ────────────────────────────────────────
def running() -> dict | None:
    job = _state["job"]
    return job if job and job.get("proc") and job["proc"].poll() is None else None


def _allowed(allowed: str) -> str:
    words = [w.strip().lower() for w in (allowed or "").replace(";", ",").split(",") if w.strip()]
    return ", ".join(words) if words else "nothing — stop before every final action"


def start(task: str, allowed: str = "", max_steps: int | None = None, notify=None) -> str:
    if not installed():
        return "The web agent isn't installed yet. Say 'install the web agent' or use Settings → Web agent → Install."
    if running():
        return f"I'm still busy with: {_state['job']['task']}. Say 'stop the web agent' to cancel it."
    llm = pick_llm()
    jobs = HOME / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    stop_file = jobs / f"{stamp}.stop"
    job_file = jobs / f"{stamp}.json"
    PROFILE.mkdir(parents=True, exist_ok=True)
    key = llm.pop("api_key", "")                          # handed over in the environment, never written to disk
    job = {"task": task, **llm, "rules": RULES.format(allowed=_allowed(allowed)), "stop_file": str(stop_file),
           "profile_dir": str(PROFILE), "max_steps": int(max_steps or cfg().get("max_steps", 30)),
           "headless": bool(cfg().get("headless", False)), "executable_path": str(cfg().get("chrome_path", "")), "profile": dict(cfg().get("profile") or {}),
           "files": [str((ROOT / "workspace").resolve())]}
    job_file.write_text(json.dumps(job), encoding="utf-8")
    proc = subprocess.Popen([str(python()), str(Path(__file__).with_name("web_agent_runner.py")), str(job_file)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", cwd=str(HOME), env={**os.environ, "NOVA_WEB_AGENT_KEY": key})
    state = {"task": task, "proc": proc, "steps": [], "result": None, "stop_file": stop_file, "started": time.time(),
             "job_file": job_file}
    _state["job"] = state
    threading.Thread(target=_follow, args=(state, notify), daemon=True, name="web-agent").start()
    return (f"On it — the web agent is working on “{task}” in its own Chrome window "
            f"(up to {job['max_steps']} steps). I'll tell you when it's done; say 'stop the web agent' to cancel.")


def _follow(state: dict, notify=None) -> None:
    final = {}
    for line in state["proc"].stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "step" in msg:
            state["steps"].append(msg)
            if context.store and msg.get("goal"):
                context.store.log("web", "web agent", f"🌐 {msg['step']}. {msg['goal'][:90]}", turn=0)
        if msg.get("done"):
            final = msg
    state["proc"].wait()
    state["result"] = final or {"done": True, "success": False, "error": "the web agent stopped unexpectedly"}
    try:
        state["job_file"].unlink(missing_ok=True)
        state["stop_file"].unlink(missing_ok=True)
    except OSError:
        pass
    text = summary(state)
    (notify or _announce)(text)


def _announce(text: str) -> None:
    if context.push:
        context.push(text, [])
    if context.announce:
        context.announce(text.split("\n")[0][:300])


def summary(state: dict) -> str:
    r = state.get("result") or {}
    if r.get("stopped"):
        head = "🌐 Web agent stopped."
    elif r.get("error"):
        head = f"🌐 The web agent hit a problem: {r['error'][:200]}"
    elif r.get("success") is False:
        head = "🌐 The web agent couldn't fully finish."
    else:
        head = "🌐 Web agent done."
    body = (r.get("result") or "").strip()
    return f"{head}\nTask: {state['task']}" + (f"\n\n{body[:3000]}" if body else "")


def stop() -> str:
    job = running()
    if not job:
        return "The web agent isn't doing anything."
    job["stop_file"].write_text("stop", encoding="utf-8")

    def kill_later():
        time.sleep(25)
        if job["proc"].poll() is None:
            job["proc"].kill()
    threading.Thread(target=kill_later, daemon=True).start()
    return "Stopping the web agent."


def status() -> str:
    if not installed():
        return "The web agent isn't installed yet." + (" Installing now…" if extras.status("browser_use")["state"] == "installing" else "")
    job = _state["job"]
    if not job:
        return "The web agent is ready and idle."
    if running():
        last = job["steps"][-1]["goal"] if job["steps"] else "starting the browser"
        return f"Working on “{job['task']}” — step {len(job['steps'])}: {last}"
    return summary(job)


def open_for_login(url: str = "") -> str:
    """Open the web agent's own Chrome profile so you can log in to a site once; it remembers the login."""
    if running():
        return "Wait until the web agent has finished, then log in."
    exe = str(cfg().get("chrome_path", "")) or _find_browser()
    if not exe:
        return "I couldn't find Chrome or Edge."
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([exe, f"--user-data-dir={PROFILE}", "--no-first-run", url or "about:blank"])
    return ("Opened the web agent's browser. Log in to the site, then close that window — "
            "the web agent will be logged in from now on.")


def _find_browser() -> str:
    cands = [os.path.expandvars(p) for p in (
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium")]
    return next((c for c in cands if Path(c).exists()), "")
