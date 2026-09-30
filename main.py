"""Nova — voice-first, local-first AI agent.

    python main.py                 start everything (voice + Telegram + dashboard)
    python main.py --text          type instead of talking (handy for testing in VS Code)
    python main.py --no-voice      Telegram + dashboard only
    python main.py --google-login  connect your Google account (once)
    python main.py --check         check that everything is installed and configured
"""
from __future__ import annotations

import argparse
import sys
import threading
import time

from nova import context, updater
from nova.agent import Agent
from nova.config import load_config, resolve
from nova.extensions import load_playbooks, load_plugins
from nova.mcp_client import MCPManager
from nova.llm import LLM
from nova.skills import load_all
from nova.speech import Speech
from nova.store import Store


def build():
    cfg = load_config()
    context.cfg = cfg
    b = cfg.brain
    context.store = Store(resolve(b.db_file), cfg.llm.providers.ollama.base_url, b.embed_model)
    context.store.vault = resolve(b.vault_dir)
    try:
        from nova import roadmap
        r = roadmap.sync(context.store)
        if r["added"] or r["moved"]:
            print(f"[roadmap] {r['added']} new, {r['moved']} moved on the Projects board")
    except Exception as e:
        print(f"[roadmap] skipped: {e}")
    context.llm = LLM(cfg)
    context.store.keep_alive = context.llm.keep_alive
    threading.Thread(target=keep_warm, daemon=True, name="keep-warm").start()
    context.speech = Speech(cfg)
    skills = load_all()
    plugins = load_plugins()
    books = load_playbooks()
    print(f"[nova] skills: {', '.join(skills)}" + (f" | plugins: {', '.join(plugins)}" if plugins else "")
          + (f" | playbooks: {len(books)}" if books else ""))
    context.mcp = MCPManager(cfg)
    context.mcp.start(timeout=3)          # keeps connecting in the background; never delays start-up
    agent = Agent(cfg, context.llm)
    context.agent = agent
    if (cfg.get("globe") or {}).get("auto_start"):
        from nova.skills import globe
        threading.Thread(target=lambda: print(f"[globe] {globe.start()}"), daemon=True, name="globe").start()
    # index any notes added/edited in the vault while Nova was off
    threading.Thread(target=lambda: context.store.sync_vault(resolve(b.vault_dir)), daemon=True).start()
    return cfg, agent


def startup_summary(cfg, use_voice, tg, remote_state) -> None:
    """One clear block in Nova's window: what's running, and what to fix if something isn't."""
    import time
    time.sleep(8)
    port = cfg.dashboard.get("port", 8765)
    if not cfg.telegram.enabled:
        tg_line = "off (switched off in Settings → Telegram)"
    elif not (tg and tg.token):
        tg_line = "OFF — add your bot token in Settings → API keys, then restart"
    elif not tg.allowed:
        tg_line = "running, but no allowed user yet — message your bot /id, add it in Settings → Telegram"
    else:
        tg_line = "running ✓" if tg.app else "starting… (check the lines above if it doesn't connect)"
    if not (cfg.get("remote") or {}).get("enabled"):
        ph_line = "not set up — click 📱 on the dashboard"
    elif remote_state.get("url"):
        ph_line = f"{remote_state['url']} ✓"
    else:
        ph_line = remote_state.get("message") or "Tailscale isn't connected — open the Tailscale app"
    print("\n[nova] ───────────────── Nova is ready ─────────────────")
    print(f"  Dashboard   http://localhost:{port}")
    print(f"  Voice       {'on — say the wake word or press the hotkey' if use_voice else 'off'}")
    print(f"  Telegram    {tg_line}")
    print(f"  Phone       {ph_line}")
    print("[nova] ─────────────────────────────────────────────────\n")


def keep_warm() -> None:
    """Load the local model + embeddings at start-up and keep them in memory, so typed and spoken
    commands answer straight away instead of waiting for the model to load (can take 10-30 s)."""
    import time
    context.llm.warm_up()
    context.store.embed(["warm up"])
    while True:           # Ollama unloads idle models; a tiny ping every 4 minutes keeps them ready
        time.sleep(240)
        try:
            if context.store.status == "idle":
                context.llm.warm_up(quiet=True)
        except Exception:
            pass


def startup_update_check(speak) -> None:
    try:
        info = updater.check()
        if info.get("behind"):
            msg = f"An update for Nova is available: {info['latest']}. Say 'update yourself' when you're ready."
            print(f"[update] {msg}")
            context.store.log("update", "system", msg)
            context.push("⬆️ " + msg)
    except Exception as e:
        print(f"[update] check skipped: {e}")


def text_mode(agent) -> None:
    print("Type to talk to Nova. 'quit' to exit.\n")
    while True:
        try:
            text = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit"):
            break
        if text:
            r = agent.handle(text, session="text")
            print(f"nova › {r.text}" + (f"\n       files: {r.files}" if r.files else "") + "\n")


def check(cfg) -> None:
    import importlib
    import os
    import httpx
    ok = lambda b: "OK " if b else "-- "
    print("Python packages:")
    for mod in ["openai", "sounddevice", "openwakeword", "faster_whisper", "piper", "telegram", "googleapiclient",
                "playwright", "cv2", "PIL", "trafilatura", "ddgs", "imageio_ffmpeg", "rembg", "mcp", "kokoro_onnx", "soundcard", "pynput"]:
        try:
            importlib.import_module(mod)
            print(f"  {ok(True)}{mod}")
        except Exception as e:
            print(f"  {ok(False)}{mod}  ({type(e).__name__})")
    print("Services:")
    try:
        tags = httpx.get(cfg.llm.providers.ollama.base_url.removesuffix("/v1") + "/api/tags", timeout=5).json()
        names = [m["name"] for m in tags.get("models", [])]
        print(f"  {ok(True)}Ollama running; models: {', '.join(names)}")
    except Exception:
        print(f"  {ok(False)}Ollama not reachable — install from ollama.com and run setup again")
    for k in ["ELEVENLABS_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "TELEGRAM_BOT_TOKEN"]:
        print(f"  {ok(bool(os.environ.get(k)))}{k} in .env")
    print(f"  {ok(resolve(cfg.google.token_file).exists())}Google connected")
    print(f"  {ok(resolve(cfg.tts.piper.voice).exists())}Piper backup voice")
    for name, st in (context.mcp.status if context.mcp else {}).items():
        print(f"  {ok(st.startswith('connected'))}MCP server '{name}': {st}")
    print(f"  {ok(updater.is_repo())}GitHub (git repo) — version {updater.current() if updater.is_repo() else 'n/a'}")


class _Tee:
    """Mirror console output to data/logs/nova.log so problems can be diagnosed after the fact."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        try:
            self.stream.write(text)
        except Exception:
            pass
        self.log.write(text)
        self.log.flush()
        return len(text)

    def flush(self):
        try:
            self.stream.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.stream, name)


def start_logging() -> None:
    import datetime as dt
    import traceback
    logs = resolve("data/logs")
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "nova.log"
    if log_path.exists() and log_path.stat().st_size > 0:
        log_path.replace(logs / "nova.previous.log")
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    log.write(f"=== Nova started {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    sys.stdout = _Tee(sys.__stdout__, log)
    sys.stderr = _Tee(sys.__stderr__, log)

    def crash(exc_type, exc, tb):
        print("\n[nova] CRASH:\n" + "".join(traceback.format_exception(exc_type, exc, tb)), file=sys.stderr)
    sys.excepthook = crash
    threading.excepthook = lambda a: crash(a.exc_type, a.exc_value, a.exc_traceback)


def main() -> None:
    start_logging()
    ap = argparse.ArgumentParser(description="Nova voice agent")
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--google-login", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    cfg, agent = build()

    if args.check:
        return check(cfg)
    if args.google_login:
        from nova.skills.google_ws import login
        try:
            login(interactive=True)
        except Exception as e:
            print(f"\n[google] Not connected yet: {e}\n")
            return
        print("\nGoogle connected. Nova stays signed in — no need to do this again.")
        return

    speech = context.speech
    use_voice = cfg.voice.enabled and not args.no_voice and not args.text

    # Telegram (created first so notifications can use it)
    tg = None
    if cfg.telegram.enabled and not args.text:
        from nova.telegram_bot import TelegramBot
        tg = TelegramBot(cfg, agent, speech)

    def notify(text, files):
        if tg:
            tg.push(text, files)

    context.notify = notify
    from nova.presence import presence

    def say(text):
        """Speak something out of the blue — unless you've walked away (then it's held and sent to Telegram)."""
        if not presence().hold_or_say(text) and use_voice:
            speech.speak(text)

    if use_voice:
        context.speak_now = lambda text: threading.Thread(target=speech.speak, args=(text,), daemon=True).start()
    context.announce = lambda text: threading.Thread(target=say, args=(text,), daemon=True).start()

    if cfg.dashboard.get("enabled", True):
        from nova.dashboard.server import Dashboard
        Dashboard(cfg, agent).start()

    from nova.skills.system import reminder_worker
    from nova.routines import worker as routines_worker
    threading.Thread(target=reminder_worker, args=(say if use_voice else None,), daemon=True).start()
    threading.Thread(target=routines_worker, args=(agent, cfg, say if use_voice else None), daemon=True).start()
    threading.Thread(target=startup_update_check, args=(None,), daemon=True).start()

    if args.text:
        return text_mode(agent)

    # Voice and Telegram each run in their own guarded thread: if one fails (no microphone, bad token…)
    # it's reported and retried, and the dashboard, Settings and everything else keep running.
    if use_voice:
        from nova.voice import VoiceLoop
        loop = VoiceLoop(cfg, agent, speech)
        context.voice = loop
        threading.Thread(target=guarded, args=("Voice", loop.run), daemon=True, name="voice").start()
    remote_state: dict = {}
    if (cfg.get("remote") or {}).get("enabled"):
        from nova import remote

        def _remote():
            try:
                remote_state.update(remote.ensure())      # reconnect Tailscale + share Nova on it
            except Exception as e:
                print(f"[remote] {e}")
        rt = threading.Thread(target=_remote, daemon=True, name="remote")
        rt.start()
        rt.join(timeout=15)                             # so Telegram's 🟢 message can include the phone link
    if tg and tg.token:
        threading.Thread(target=guarded, args=("Telegram", tg.run), daemon=True, name="telegram").start()
    threading.Thread(target=startup_summary, args=(cfg, use_voice, tg, remote_state), daemon=True).start()
    try:
        from nova.dreaming import dreamer
        dreamer().start()                # nightly memory clean-up, journal and encrypted backup
    except Exception as e:
        print(f"[dream] not scheduled: {e}")
    try:
        from nova import wellbeing
        wellbeing.start()                # routine anchors, morning release of held actions, heads-ups
    except Exception as e:
        print(f"[wellbeing] not started: {e}")
    try:
        from nova import activity
        activity.start()                 # ActivityWatch screen time: gentle drift nudges during work hours
    except Exception as e:
        print(f"[activity] not started: {e}")
    try:
        from nova.missions import missions
        missions().start()               # runs scheduled missions (and any that were due while Nova was off)
    except Exception as e:
        print(f"[missions] scheduler not started: {e}")
    try:
        from nova.watcher import manager
        n = manager().load()
        if n:
            print(f"[watch] still watching {n} thing(s) from before the restart")
    except Exception as e:
        print(f"[watch] couldn't restore watches: {e}")
    if (cfg.get("presence") or {}).get("enabled"):
        threading.Thread(target=lambda: print(f"[presence] {presence().start()}"), daemon=True, name="presence").start()
    if (cfg.get("gestures") or {}).get("enabled"):
        from nova.gestures import engine
        threading.Thread(target=lambda: print(f"[gestures] {engine().start()}"), daemon=True, name="gestures").start()
    if not use_voice and not (tg and tg.token):
        print("[nova] Voice is off and Telegram has no token — the dashboard is running at "
              f"http://localhost:{cfg.dashboard.get('port', 8765)} (type in its Ask box).")
    print("[nova] Running. Close this window or press Ctrl+C to stop Nova.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        if tg:
            tg.say_goodbye()
        print("[nova] Stopped.")


def guarded(name: str, fn, retries: int = 3) -> None:
    """Run a part of Nova; if it crashes, explain and retry instead of taking Nova down."""
    import traceback
    for attempt in range(1, retries + 1):
        try:
            fn()
            return
        except Exception as e:
            print(f"\n[nova] {name} stopped with an error (attempt {attempt}/{retries}): {type(e).__name__}: {e}")
            traceback.print_exc()
            if attempt < retries:
                print(f"[nova] Retrying {name} in 20 seconds… (the dashboard and Settings keep working)")
                time.sleep(20)
    print(f"[nova] {name} is off until Nova restarts. See data\\logs\\nova.log for details.")

if __name__ == "__main__":
    main()
