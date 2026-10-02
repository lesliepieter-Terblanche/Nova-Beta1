"""The agent loop: user text in -> tools -> reply out.

Risky tools (send email, delete, formatting disks…) pause and ask for a yes/no (Settings → Ask me before);
the next message from the same session answers it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from . import context
from .extensions import matching_playbooks
from .store import learn_from_turn
from .tools import REGISTRY, Tool, select_tools

# Settings → General → "Ask me before…" (system.confirm):
#   irreversible (default) — only things that can't be undone or that reach other people
#   all — every tool marked risky (the old behaviour) · never — nothing, except clearly destructive shell commands
IRREVERSIBLE = {"delete_path", "gmail_send", "gmail_reply", "calendar_invite", "calendar_delete", "power_action",
                "browser_submit", "rollback_version", "phone_call", "apply_brain_plan"}
IRREVERSIBLE_NAME = re.compile(r"(send|delete|remove|trash|publish|post|pay|purchase|transfer|cancel|wipe|erase)",
                               re.I)
DANGEROUS_SHELL = re.compile(
    r"(\bformat(-volume)?\s+[a-z]:?|\bdiskpart\b|\bclear-disk\b|\brm\s+-[a-z]*r[a-z]*\s+[/~\\]|\b(rd|rmdir)\s+/s\b|"
    r"\bdel\s+(/[a-z]\s+)*/s\b|remove-item\b.*-recurse|\breg(\.exe)?\s+delete\b|\bbcdedit\b|\bcipher\s+/w\b|"
    r"\b(stop|restart)-computer\b|\bshutdown(\.exe)?\b|\bset-executionpolicy\b.*unrestricted|\bnet\s+user\b)", re.I)


def needs_yes(t: Tool, args: dict) -> bool:
    """Should Nova ask before running this tool call?"""
    mode = str(((context.cfg or {}).get("system") or {}).get("confirm", "irreversible")).lower()
    if t.name == "run_shell" and DANGEROUS_SHELL.search(str(args.get("command", ""))):
        return True                                       # formatting disks & co. always ask
    if not t.confirm or mode == "never":
        return False
    if mode == "all":
        return True
    return t.name in IRREVERSIBLE or (t.name not in REGISTRY_SAFE and bool(IRREVERSIBLE_NAME.search(t.name)))


REGISTRY_SAFE = {"send_held", "send_to_phone"}          # you asked for these by name — no second question

YES = re.compile(r"^\s*(yes|yeah|yep|yup|ja|sure|ok(ay)?|do it|go ahead|confirm(ed)?|please do|affirmative)\b", re.I)
NO = re.compile(r"^\s*(no|nope|nee|cancel|stop|don'?t|abort|never ?mind)\b", re.I)


@dataclass
class Reply:
    text: str
    files: list[str] = field(default_factory=list)


@dataclass
class Pending:
    tool: Tool
    args: dict
    call_id: str
    messages: list
    tools: list
    prefer_smart: bool
    skipped_ids: list[str]
    log_id: int | None = None
    created: float = field(default_factory=time.time)


class Agent:
    def __init__(self, cfg, llm):
        self.cfg, self.llm = cfg, llm
        self.histories: dict[str, list] = {}
        self.pending: dict[str, Pending] = {}
        self.lock = threading.Lock()      # one request at a time (one GPU)

    # ── public ─────────────────────────────────────────────
    def handle(self, text: str, session: str = "voice", prefer_smart: bool = False, on_delta=None) -> Reply:
        """on_delta(text): called with each new piece of the answer while it is being written (live speech)."""
        store = context.store
        with self.lock:
            self._on_delta = on_delta
            context.begin_turn()
            self._tools_used = []
            self._model_ms = 0
            self._rounds = 0
            t0 = time.perf_counter()
            turn = None
            if store:
                store.set_status("thinking")
                title = None
                if session.startswith("mission:"):
                    m = re.search(r"Step (\d+) of (\d+) — ([^:\n]+)", text)
                    title = f"🚀 Mission step {m.group(1)}/{m.group(2)}: {m.group(3)}" if m else "🚀 Mission step"
                turn = store.begin_turn(session, text, title)
            failed = False
            try:
                out = self._handle(text.strip(), session, prefer_smart)
            except Exception as e:
                failed = True
                out = f"Sorry, something went wrong: {e}"
            files = context.attachments()
            total = int((time.perf_counter() - t0) * 1000)
            model = getattr(self.llm, "last_provider", "") or "?"
            timing = (f"{total / 1000:.1f}s total — thinking {self._model_ms / 1000:.1f}s on {model} "
                      f"({self._rounds} step{'s' if self._rounds != 1 else ''})"
                      + (f", tools: {', '.join(self._tools_used)}" if self._tools_used else ""))
            print(f"[timing] {timing}")
            if store:
                waiting = session in self.pending
                store.log("reply", session, out[:200], out, f"turn:{turn}" if turn else "",
                          status="error" if failed else "ok", ms=total)
                store.log("timing", session, timing, json.dumps({"total_ms": total, "model_ms": self._model_ms,
                          "model": model, "rounds": self._rounds, "tools": self._tools_used}), turn=turn)
                store.end_turn(turn, "error" if failed else "waiting" if waiting else "done", total)
                store.set_status("idle")
                # Learn in the background so the reply isn't delayed.
                if self.cfg.brain.get("learn_automatically", True) and not session.startswith("mission:"):
                    threading.Thread(target=learn_from_turn, daemon=True, args=(
                        store, self.llm, self.cfg.assistant.owner, text, out, list(self._tools_used)),
                        kwargs={"turn": turn}).start()
            return Reply(out, files)

    def waiting_session(self) -> str | None:
        """The conversation whose yes/no question was asked most recently (for 👍/👎 gestures)."""
        if not self.pending:
            return None
        return max(self.pending.items(), key=lambda kv: kv[1].created)[0]

    def reset(self, session: str) -> None:
        self.histories.pop(session, None)
        self.pending.pop(session, None)

    # ── internals ──────────────────────────────────────────
    def _handle(self, text: str, session: str, force_smart: bool = False) -> str:
        if session in self.pending:
            p = self.pending.pop(session)
            if YES.match(text):
                return self._resume(session, text, p, approved=True)
            if NO.match(text):
                return self._resume(session, text, p, approved=False)
            # Anything else: treat as a new request and drop the pending action.

        history = self.histories.setdefault(session, [])
        books = matching_playbooks(text)
        tools = select_tools(text, extra={g for b in books for g in b.groups})
        prefer_smart = force_smart or self.llm.should_escalate(text)
        messages = [{"role": "system", "content": self._system_prompt(session, text)}] + history + [
            {"role": "user", "content": text}
        ]
        return self._loop(session, text, messages, tools, prefer_smart)

    def _loop(self, session, user_text, messages, tools, prefer_smart) -> str:
        schemas = [t.schema() for t in tools]
        max_rounds = int(self.cfg.llm.get("max_tool_rounds", 6))
        for round_no in range(max_rounds):
            # If the local model is going round in circles, hand over to the smart one.
            smart = prefer_smart or round_no >= 3
            t = time.perf_counter()
            live = {"on_delta": self._on_delta} if getattr(self, "_on_delta", None) else {}
            reply = self.llm.chat(messages, schemas, prefer_smart=smart, **live)
            self._model_ms = getattr(self, "_model_ms", 0) + int((time.perf_counter() - t) * 1000)
            self._rounds = getattr(self, "_rounds", 0) + 1
            if not reply.tool_calls:
                self._remember(session, user_text, reply.content)
                return reply.content

            messages.append({
                "role": "assistant",
                "content": reply.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in reply.tool_calls
                ],
            })
            for i, call in enumerate(reply.tool_calls):
                t = REGISTRY.get(call.name)
                wait = self._should_hold(t, call.arguments) if t is not None else None
                if t is None:
                    result = f"ERROR: no tool named {call.name}"
                elif wait:
                    from .wellbeing import hold
                    hid = hold(t.name, call.arguments, *wait)
                    result = (f"HELD (not done): {wait[0]}. It's saved as held action #{hid} until "
                              f"{wait[1]:%A %H:%M}. Tell the user kindly in one sentence; they can say 'send held #{hid}' "
                              "to do it anyway.")
                elif needs_yes(t, call.arguments):
                    later = [c.id for c in reply.tool_calls[i + 1:]]
                    self.pending[session] = Pending(t, call.arguments, call.id, messages, tools, smart, later)
                    if context.store:
                        self.pending[session].log_id = context.store.log(
                            "tool", session, t.name.replace("_", " ") + " (waiting for your yes/no)",
                            json.dumps(call.arguments, ensure_ascii=False)[:1000], status="waiting")
                    return self._confirmation_question(t, call.arguments)
                else:
                    result = self._run_tool(session, t, call.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result[:6000]})
        return "I couldn't finish that in a reasonable number of steps. Try breaking it into smaller requests."

    @staticmethod
    def _should_hold(t: Tool, args: dict):
        """Night-time sends and purchases wait (Settings → Wellbeing)."""
        if t.name in ("send_held", "drop_held"):
            return None
        try:
            from .wellbeing import should_hold
            return should_hold(t.name, args) if context.store else None
        except Exception as e:
            print(f"[wellbeing] {e}")
            return None

    def _resume(self, session, text, p: Pending, approved: bool) -> str:
        if context.store and getattr(p, "log_id", None):
            context.store.finish(p.log_id, "approved" if approved else "declined")
        if approved:
            result = self._run_tool(session, p.tool, p.args)
        else:
            result = "The user declined. Do not run it; acknowledge briefly."
        p.messages.append({"role": "tool", "tool_call_id": p.call_id, "content": result[:6000]})
        for cid in p.skipped_ids:
            p.messages.append({"role": "tool", "tool_call_id": cid, "content": "Not run (was waiting for confirmation)."})
        p.messages.append({"role": "user", "content": text})
        return self._loop(session, text, p.messages, p.tools, p.prefer_smart)

    def _run_tool(self, session: str, t: Tool, args: dict) -> str:
        print(f"[tool] {t.name} {args}")
        store = context.store
        aid = None
        if store:
            aid = store.log("tool", session, t.name.replace("_", " "), json.dumps(args, ensure_ascii=False)[:1000],
                            status="running")
        self._tools_used.append(t.name)
        started = time.perf_counter()
        result = t.run(args)
        ms = int((time.perf_counter() - started) * 1000)
        print(f"[tool] {t.name} took {ms} ms")
        if store and aid:
            ok = not str(result).startswith("ERROR")
            detail = json.dumps({"args": args, "result": str(result)[:1500]}, ensure_ascii=False, default=str)
            store.finish(aid, "ok" if ok else "error", ms, detail)
        return result

    @staticmethod
    def _confirmation_question(t: Tool, args: dict) -> str:
        pretty = ", ".join(f"{k}: {str(v)[:120]}" for k, v in args.items())
        label = t.name.replace("_", " ")
        return f"Just to confirm — {label} ({pretty}). Shall I go ahead? Say yes or no."

    def _remember(self, session, user_text, answer) -> None:
        h = self.histories.setdefault(session, [])
        h += [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}]
        keep = int(self.cfg.assistant.get("max_history_turns", 12)) * 2
        del h[:-keep]

    def _system_prompt(self, session: str, user_text: str = "") -> str:
        a = self.cfg.assistant
        now = dt.datetime.now(ZoneInfo(a.timezone))
        channel = "spoken aloud through the PC speakers" if session == "voice" else "sent as a Telegram message"
        about = context.store.context_for(user_text)[:2500] if context.store else ""
        books = matching_playbooks(user_text)
        playbook_text = "".join(f"\n\nPLAYBOOK '{b.name}' — follow these steps:\n{b.body[:3000]}" for b in books)
        return f"""You are {a.name}, {a.owner}'s personal AI assistant running locally on their PC.
Current date/time: {now:%A %d %B %Y, %H:%M} ({a.timezone}).{f" Home city: {a.city}." if a.get("city") else ""}
Your reply will be {channel}. Keep it short, natural and conversational — one to three sentences,
no markdown, no bullet lists, no emojis, unless the user asks for detail.

Rules:
- Use the tools to act. Never pretend you did something or invent results; if a tool errors, say so plainly.
- Answer with the actual information, in words. Never reply with just a link or "click here" — read out what
  the tool found. For weather always use get_weather and say its sentence.
- For risky actions (sending email, deleting or moving files, shell commands) just call the tool;
  the system will ask the user to confirm.
- Dates: pass natural phrases like "tomorrow 3pm" or ISO times; the tools understand both.
- File paths: use full paths or paths inside Documents, Desktop, Downloads or the workspace folder.
- If a request is ambiguous, ask one short question.
- You have a permanent memory. It learns automatically after each conversation; use the remember tool when
  {a.owner} explicitly asks you to remember something, and correct_memory when you are corrected.

- The dashboard shows the weather, PC stats, screen time and headlines by itself when you use those tools. For other
  information the user wants to SEE (lists, figures, comparisons, briefings), also call show_on_screen.
- For "what should I do / I'm stuck / brain dump" use the focus tools: one thing at a time, kindly, never guilt.{self._style()}

What you remember that may be relevant ({a.owner}'s memories and notes):
{about or "(nothing saved yet)"}{playbook_text}"""

    @staticmethod
    def _style() -> str:
        try:
            from .wellbeing import style_hint
            return style_hint()
        except Exception:
            return ""
