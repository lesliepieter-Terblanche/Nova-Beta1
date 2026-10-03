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
    turn: int | None = None           # the request that asked the question — closed once you answer
    created: float = field(default_factory=time.time)


# Requests that must never be improvised: they go straight to the tool, without asking the model.
DIRECT = [
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:set ?up|connect(?: to)?|re-?connect(?: to)?|pair|link)"
                r" (?:my|the) (?:phone|cell ?phone|galaxy|android(?: phone)?|s\d\d(?: ?(?:plus|ultra|\+))?)"
                r"(?: (?:again|now|please|to (?:you|nova|the pc|my pc|the computer)))*[.!]?$", re.I), "phone_setup"),
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:ring|find) my "
                r"(?:phone|cell ?phone|galaxy)(?: please| now)?[.!?]?$", re.I), "phone_ring"),
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:locate|track|where(?:'s| is)) my "
                r"(?:phone|cell ?phone|galaxy)(?: please| now| right now)?[.!?]?$", re.I), "phone_locate"),
    (re.compile(r"^(?:hey |ok |please |no[,!]? ?|oops[,!]? ?)?(?:nova[,!]? )?(?:please )?(?:undo|revert|take (?:that|it) back|"
                r"put (?:that|it) back)(?: (?:that|it|this|the last (?:thing|one|action|change)|what you (?:just )?did))?"
                r"(?: please| now)?[.!]?$", re.I), "undo_last"),
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:show|open|mirror|display) (?:me )?(?:my|the) "
                r"(?:phone|phone'?s|galaxy|cell ?phone)(?:'s)? ?(?:screen)?(?: on (?:the|my) (?:pc|computer|screen))?"
                r"(?: please| now)?[.!]?$", re.I), "phone_show_screen"),
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:close|hide|stop showing) (?:my|the) "
                r"(?:phone|phone'?s)(?:'s)? ?screen(?: please| now)?[.!]?$", re.I), "phone_hide_screen"),
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:what(?:'s| is) the|read (?:me )?the|get the|give me the)"
                r" (?:one[- ]time |verification |login |otp )?(?:code|otp|pin)(?: (?:from|on) my phone)?(?: please)?[.!?]?$",
                re.I), "phone_code"),
    (re.compile(r"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(answer|decline|reject|hang up|end)"
                r"(?: (?:the|my|that|this))?(?: (?:call|phone))?(?: please| now)?[.!]?$", re.I), "phone_call_control"),
]
DIRECT_ARGS = {"phone_call_control": lambda m: {"action": {"reject": "decline", "end": "hang_up", "hang up": "hang_up"}.get(
    m.group(1).lower(), m.group(1).lower())}}


# "whatsapp Sam I'm running late" / "send Sam a WhatsApp saying …" → one call to the phone, no improvising.
MSG_APP = r"(whats ?app|sms|telegram|signal)"
MSG_LEAD = re.compile(rf"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(?:send (?:an? )?)?{MSG_APP}(?: message)?(?: to)? (.+)$", re.I | re.S)
MSG_MID = re.compile(rf"^(?:hey |ok |please )?(?:nova[,!]? )?(?:please )?(send|write|draft) (.+?) (?:an? )?{MSG_APP}(?: message)?"
                     r"(?:\s*[:,-]\s*| saying | that says | to say | that )(.+)$", re.I | re.S)
MSG_SPLIT = re.compile(r"^(.+?)(?:\s*:\s*|\s+-\s+|,\s*| saying | that says | to say | and say | that | message )(.+)$", re.I | re.S)


def message_request(text: str, llm=None) -> dict | None:
    """Arguments for phone_send_message when the sentence is plainly 'message this person this text'."""
    t = text.strip()
    if "?" in t[:-1] or len(t) > 600:
        return None
    m = MSG_MID.match(t)
    if m:
        return {"to": m.group(2).strip(), "text": m.group(4).strip().strip('"“”'), "send": m.group(1).lower() == "send",
                "app": m.group(3).lower().replace(" ", "")}
    m = MSG_LEAD.match(t)
    if not m:
        return None
    app, rest = m.group(1).lower().replace(" ", ""), m.group(2).strip()
    if re.match(r"^(is|isn'?t|are|was|not|settings?|web|status|app|apps|doesn'?t|won'?t|has|have|notifications?|calls?|"
                r"groups?|business|keeps?|on|in|and|or|messages?)\b", rest, re.I) or len(rest.split()) < 2:
        return None                              # talking ABOUT the app, not messaging someone
    q = re.match(r'^(.+?)\s*["“](.+)["”]\s*$', rest, re.S)
    sp = q or MSG_SPLIT.match(rest)
    if sp and len(sp.group(1).split()) <= 4:
        return {"to": sp.group(1).strip(" ,:"), "text": sp.group(2).strip().strip('"“”'), "app": app, "send": True}
    if llm is None:
        return None
    try:                                          # "whatsapp karen smith i love you": where does the name end?
        raw = llm.complete('Split this into who the message is for and the message itself. JSON only: '
                           '{"to": "<the person\'s name>", "text": "<the message, word for word>"}\n\n' + rest,
                           prefer_smart=False, temperature=0)
        d = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        to, body = str(d.get("to", "")).strip(), str(d.get("text", "")).strip()
        if to and body and to.lower() in rest.lower() and len(to.split()) <= 4:
            return {"to": to, "text": body, "app": app, "send": True}
    except Exception:
        pass
    return None


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
            context.begin_turn(session)
            self._tools_used = []
            self._model_ms = 0
            self._rounds = 0
            self._gave_up = False
            self._answers = None              # the earlier request this one answers with a yes/no
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
                status = "error" if failed or self._gave_up else "waiting" if waiting else "done"
                if waiting and self.pending[session].turn is None:
                    self.pending[session].turn = turn
                if self._answers and not waiting:          # the command that asked is finished now too
                    store.set_turn_status(self._answers, "error" if status == "error" else "done")
                elif self._answers:                        # …or it asked a second question
                    store.set_turn_status(self._answers, "done")
                store.end_turn(turn, status, total)
                store.set_status("idle")
                try:                                       # tag the project this was for: in progress
                    from . import autotag
                    asked = store.turn_text(self._answers) if self._answers else ""    # "yes" answers that request
                    autotag.after_turn(self._answers if asked else turn, asked or text, list(self._tools_used), status)
                except Exception as e:
                    print(f"[autotag] {e}")
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
        p = self.pending.pop(session, None)
        if p and context.store:
            if p.log_id:
                context.store.finish(p.log_id, "dropped")
            if p.turn:
                context.store.set_turn_status(p.turn, "cancelled")

    # ── internals ──────────────────────────────────────────
    def _handle(self, text: str, session: str, force_smart: bool = False) -> str:
        if session in self.pending:
            p = self.pending.pop(session)
            if YES.match(text):
                return self._resume(session, text, p, approved=True)
            if NO.match(text):
                return self._resume(session, text, p, approved=False)
            # Anything else: treat as a new request and drop the pending action.
            if context.store:
                if p.log_id:
                    context.store.finish(p.log_id, "dropped")
                if p.turn:
                    context.store.set_turn_status(p.turn, "cancelled")

        try:                                             # a saved phone routine by name: "driving mode"
            from . import phone_routines
            routine = phone_routines.find(text) if "phone_run_routine" in REGISTRY else ""
        except Exception:
            routine = ""
        if routine:
            out = self._run_tool(session, REGISTRY["phone_run_routine"], {"name": routine})
            self._remember(session, text, out)
            return out

        if "phone_send_message" in REGISTRY:
            msg = message_request(text, self.llm)
            if msg:
                out = self._run_tool(session, REGISTRY["phone_send_message"], msg)
                self._remember(session, text, out)
                return out

        for pattern, name in DIRECT:
            t = REGISTRY.get(name)
            hit = pattern.match(text) if t is not None else None
            if hit:
                out = self._run_tool(session, t, DIRECT_ARGS[name](hit) if name in DIRECT_ARGS else {})
                self._remember(session, text, out)
                return out

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
        self._gave_up = True
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
        self._answers = p.turn
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
- Never give {a.owner} commands, scripts, code or file paths to run or type themselves (PowerShell, cmd, adb…) and
  never invent program names or paths. Do it with a tool; if no tool can do it, say plainly that you can't yet.
- Anything about the phone (connect, set up, status, apps, screen) is done ONLY with the phone_ tools.
- To message someone from the phone (WhatsApp, SMS, Telegram, email…) or reply to them: ONE call to
  phone_send_message. To read what someone said: phone_messages. Any other multi-step phone job: ONE call to
  do_on_phone with the whole task — never tap through it yourself with phone_open_app / phone_read_screen.
  A message for LATER is phone_schedule_message. A one-time code is phone_code (never say or write the digits).
- You have a permanent memory. It learns automatically after each conversation; use the remember tool when
  {a.owner} explicitly asks you to remember something, and correct_memory when you are corrected.

- The dashboard shows the weather, PC stats, screen time and headlines by itself when you use those tools. For other
  information the user wants to SEE (lists, figures, comparisons, briefings), also call show_on_screen.
- For "what should I do / I'm stuck / brain dump" use the focus tools: one thing at a time, kindly, never guilt.
- When {a.owner} corrects HOW you did something (too long, wrong tone, wrong format, "always…", "never…"), fix it and
  save the rule with remember(kind="preference") so you get it right next time.
- A photo of a slip, receipt or invoice goes to add_slip. "Undo that" is undo_last.{self._persona()}{self._style()}{self._learned()}

What you remember that may be relevant ({a.owner}'s memories and notes):
{about or "(nothing saved yet)"}{playbook_text}"""

    @staticmethod
    def _style() -> str:
        try:
            from .wellbeing import style_hint
            return style_hint()
        except Exception:
            return ""

    def _persona(self) -> str:
        """Nova's standing personality (Settings → General). Witty unless changed."""
        a = self.cfg.assistant
        style = str(a.get("personality", "witty") or "witty").lower()
        if style == "professional":
            return "\nPersonality: businesslike and to the point. No jokes, no swearing."
        if style == "minimal":
            return "\nPersonality: as few words as possible. No jokes, no swearing."
        if style == "warm":
            return "\nPersonality: friendly and encouraging, a light touch of humour now and then. No swearing."
        swear = (" An occasional mild swear word (damn, bloody, hell, crap, bugger, shit) is welcome for emphasis or "
                 f"sympathy — sparingly, never aimed at {a.owner}, never slurs or anything crude."
                 if a.get("swearing", True) else " No swearing.")
        return (f"\nPersonality: quick, dry wit — a sharp friend who happens to run {a.owner}'s life. Drop in a joke, a "
                "tease or a wry aside now and then (about one reply in three), never forced and never instead of the "
                f"answer.{swear} Keep ALL humour and swearing out of anything written for other people (messages, "
                f"emails, documents, reports), out of yes/no confirmations, and out of moments when {a.owner} sounds "
                "stressed, upset or unwell — then be plain and kind.")

    @staticmethod
    def _learned() -> str:
        try:
            from . import lessons
            prefs = lessons.preferences(8)
            return ("\n\nHow the user wants things done (learned from their corrections — always follow):\n"
                    + "\n".join(f"- {p}" for p in prefs)) if prefs else ""
        except Exception:
            return ""
