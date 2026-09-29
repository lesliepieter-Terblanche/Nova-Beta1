"""The agent loop: user text in -> tools -> reply out.

Risky tools (send email, delete, run shell...) pause and ask for a yes/no;
the next message from the same session answers it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from . import context
from .extensions import matching_playbooks
from .store import learn_from_turn
from .tools import REGISTRY, Tool, select_tools

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


class Agent:
    def __init__(self, cfg, llm):
        self.cfg, self.llm = cfg, llm
        self.histories: dict[str, list] = {}
        self.pending: dict[str, Pending] = {}
        self.lock = threading.Lock()      # one request at a time (one GPU)

    # ── public ─────────────────────────────────────────────
    def handle(self, text: str, session: str = "voice") -> Reply:
        store = context.store
        with self.lock:
            context.begin_turn()
            self._tools_used = []
            if store:
                store.set_status("thinking")
                store.log("user", session, text[:200], text)
            try:
                out = self._handle(text.strip(), session)
            except Exception as e:
                out = f"Sorry, something went wrong: {e}"
            files = context.attachments()
            if store:
                store.log("reply", session, out[:200], out)
                store.set_status("idle")
                # Learn in the background so the reply isn't delayed.
                if self.cfg.brain.get("learn_automatically", True):
                    threading.Thread(target=learn_from_turn, daemon=True, args=(
                        store, self.llm, self.cfg.assistant.owner, text, out, list(self._tools_used))).start()
            return Reply(out, files)

    def reset(self, session: str) -> None:
        self.histories.pop(session, None)
        self.pending.pop(session, None)

    # ── internals ──────────────────────────────────────────
    def _handle(self, text: str, session: str) -> str:
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
        prefer_smart = self.llm.should_escalate(text)
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
            reply = self.llm.chat(messages, schemas, prefer_smart=smart)
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
                if t is None:
                    result = f"ERROR: no tool named {call.name}"
                elif t.confirm:
                    later = [c.id for c in reply.tool_calls[i + 1:]]
                    self.pending[session] = Pending(t, call.arguments, call.id, messages, tools, smart, later)
                    return self._confirmation_question(t, call.arguments)
                else:
                    result = self._run_tool(session, t, call.arguments)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result[:6000]})
        return "I couldn't finish that in a reasonable number of steps. Try breaking it into smaller requests."

    def _resume(self, session, text, p: Pending, approved: bool) -> str:
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
        if context.store:
            context.store.log("tool", session, t.name.replace("_", " "), json.dumps(args, ensure_ascii=False)[:1000])
        self._tools_used.append(t.name)
        return t.run(args)

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
- For risky actions (sending email, deleting or moving files, shell commands) just call the tool;
  the system will ask the user to confirm.
- Dates: pass natural phrases like "tomorrow 3pm" or ISO times; the tools understand both.
- File paths: use full paths or paths inside Documents, Desktop, Downloads or the workspace folder.
- If a request is ambiguous, ask one short question.
- You have a permanent memory. It learns automatically after each conversation; use the remember tool when
  {a.owner} explicitly asks you to remember something, and correct_memory when you are corrected.

What you remember that may be relevant ({a.owner}'s memories and notes):
{about or "(nothing saved yet)"}{playbook_text}"""
