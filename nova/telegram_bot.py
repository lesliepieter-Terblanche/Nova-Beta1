"""Remote control from your phone via a private Telegram bot.

- Text or voice notes in, text (+ voice note in your ElevenLabs voice) out.
- Forward it anything — a message, a link, a PDF, a photo of a whiteboard or business card, a voice note — and it's
  filed in the 2nd brain (summary, people cards, project). /save <text or link> does the same; so does replying /save
  to any message. A file sent WITH a caption is treated as a request ("turn this into a slide") instead.
- Only Telegram user IDs listed in config.yaml can use it.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import context, ffmpeg
from .config import resolve


class TelegramBot:
    def __init__(self, cfg, agent, speech):
        self.cfg, self.agent, self.speech = cfg, agent, speech
        self.allowed = {int(x) for x in (cfg.telegram.get("allowed_user_ids") or [])}
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.app: Application | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.inbox = resolve("workspace/inbox")
        self.inbox.mkdir(parents=True, exist_ok=True)

    # ── security ──────────────────────────────────────────
    async def _guard(self, update: Update) -> bool:
        uid = update.effective_user.id if update.effective_user else None
        if uid in self.allowed:
            msg = update.effective_message
            if msg and msg.date and self.is_stale(msg.date):
                await msg.reply_text("⏸ I was offline when you sent this, so I didn't act on it. Send it again if you "
                                     "still need it.")
                return False
            return True
        await update.effective_message.reply_text(
            f"Not authorised. Your Telegram ID is {uid}. Add it to telegram.allowed_user_ids in config.yaml and restart Nova.")
        return False

    # ── handlers ──────────────────────────────────────────
    async def cmd_id(self, update: Update, _: ContextTypes.DEFAULT_TYPE):
        await update.effective_message.reply_text(f"Your Telegram user ID is {update.effective_user.id}")

    async def cmd_start(self, update: Update, _):
        if await self._guard(update):
            await update.effective_message.reply_text(
                f"Hi {self.cfg.assistant.owner}, {self.cfg.assistant.name} here. Type or send a voice note.\n"
                "Forward me anything (links, PDFs, photos, business cards, voice notes) and I'll file it in your brain.\n"
                "/save – file text or a replied-to message   /new – fresh conversation   /status – PC status   /id – your ID")

    async def cmd_new(self, update: Update, _):
        if await self._guard(update):
            self.agent.reset(self._session(update))
            await update.effective_message.reply_text("Fresh start. (Long-term memory is kept.)")

    async def cmd_status(self, update: Update, _):
        if await self._guard(update):
            await self._ask(update, "Give me a short PC status report.", voice=False)

    async def on_text(self, update: Update, _):
        if not await self._guard(update):
            return
        msg = update.effective_message
        text = msg.text or ""
        if self._filing() and (self._forwarded(msg) or self.only_links(text)):
            await self._file(update, "text", text=text, from_who=self._forwarded(msg))
            return
        await self._ask(update, text, voice=False)

    async def cmd_save(self, update: Update, ctx):
        """/save <text or link>, or reply /save to any message (text, link, photo, file, voice note)."""
        if not await self._guard(update):
            return
        msg = update.effective_message
        arg = " ".join(getattr(ctx, "args", None) or []).strip()
        target = msg.reply_to_message
        if target and (target.photo or target.document or target.video or target.voice or target.audio):
            await self._file_media(update, target, note=arg)
        elif target and (target.text or target.caption):
            await self._file(update, "text", text=target.text or target.caption, note=arg,
                             from_who=self._forwarded(target))
        elif arg:
            await self._file(update, "text", text=arg)
        else:
            await msg.reply_text("Send /save followed by a link or some text, or reply /save to any message.")

    # ── filing into the brain ─────────────────────────────
    def _filing(self) -> bool:
        return bool(self.cfg.telegram.get("file_forwards", True))

    @staticmethod
    def _forwarded(msg) -> str:
        """'' if not forwarded, else who it came from (a name, a channel, or 'forwarded')."""
        fo = getattr(msg, "forward_origin", None)
        if not fo:
            return ""
        for attr in ("sender_user", "chat", "sender_chat"):
            o = getattr(fo, attr, None)
            if o is not None:
                return " ".join(x for x in (getattr(o, "first_name", None), getattr(o, "last_name", None)) if x) \
                    or getattr(o, "title", None) or "forwarded"
        return getattr(fo, "sender_user_name", None) or "forwarded"

    @staticmethod
    def only_links(text: str) -> bool:
        from .inbox import URL_RE
        t = (text or "").strip()
        return bool(t) and bool(URL_RE.search(t)) and not URL_RE.sub("", t).strip(" \n,;")

    async def _file(self, update: Update, kind: str, text: str = "", path: Path | None = None, note: str = "",
                    from_who: str = ""):
        from . import inbox
        chat = update.effective_chat
        await chat.send_action(ChatAction.TYPING)

        def work():
            if kind == "text":
                links = inbox.URL_RE.findall(text or "")
                if links and self.only_links(text):
                    return [inbox.file_url(u, "telegram", note) for u in dict.fromkeys(links)][:5]
                return [inbox.file_text(text, "telegram", note, from_who=from_who if from_who != "forwarded" else "")]
            if kind == "voice":
                return [inbox.file_voice(text, "telegram", note, from_who=from_who if from_who != "forwarded" else "")]
            return [inbox.file_document(path, "telegram", note)]
        try:
            results = await asyncio.to_thread(work)
        except Exception as e:
            await update.effective_message.reply_text(f"I couldn't file that: {e}")
            return
        await update.effective_message.reply_text("\n\n".join(inbox.reply_text(r) for r in results))

    async def _file_media(self, update: Update, msg, note: str = ""):
        if msg.voice or msg.audio:
            text = await self._transcribe(msg)
            await self._file(update, "voice", text=text, note=note, from_who=self._forwarded(msg))
            return
        dest = await self._download(msg)
        await self._file(update, "file", path=dest, note=note or (msg.caption or ""))

    async def _download(self, msg) -> Path:
        if msg.photo:
            f, name = await msg.photo[-1].get_file(), f"photo_{msg.message_id}.jpg"
        else:
            doc = msg.document or msg.video
            f, name = await doc.get_file(), (getattr(doc, "file_name", None) or f"file_{msg.message_id}")
        dest = self.inbox / name
        await f.download_to_drive(dest)
        return dest

    async def _transcribe(self, msg) -> str:
        tg_file = await (msg.voice or msg.audio).get_file()
        with tempfile.TemporaryDirectory() as td:
            src, wav = Path(td) / "in.ogg", Path(td) / "in.wav"
            await tg_file.download_to_drive(src)
            await asyncio.to_thread(ffmpeg.to_wav16k, src, wav)
            return await asyncio.to_thread(self.speech.transcribe, str(wav))

    async def on_voice(self, update: Update, _):
        if not await self._guard(update):
            return
        msg = update.effective_message
        if self._filing() and self._forwarded(msg):                 # someone else's voice note → file it
            await self._file_media(update, msg)
            return
        text = await self._transcribe(msg)
        if not text:
            await msg.reply_text("I couldn't make out that voice note.")
            return
        await msg.reply_text(f"🗣 {text}")
        await self._ask(update, text, voice=bool(self.cfg.telegram.get("voice_replies", True)))

    async def on_file(self, update: Update, _):
        if not await self._guard(update):
            return
        msg = update.effective_message
        if self._filing() and (self._forwarded(msg) or not msg.caption) and not msg.video:
            await self._file_media(update, msg)                      # no instructions → file it in the brain
            return
        dest = await self._download(msg)
        name = dest.name
        context.record("file", name, dest, "received via Telegram")
        caption = msg.caption or "Tell me briefly what this file is and ask what I want done with it."
        await self._ask(update, f"I've sent you a file, saved at {dest}. {caption}", voice=False)

    # ── core ──────────────────────────────────────────────
    @staticmethod
    def _session(update: Update) -> str:
        return f"tg:{update.effective_user.id}"

    async def _ask(self, update: Update, text: str, voice: bool):
        chat = update.effective_chat
        await chat.send_action(ChatAction.TYPING)
        reply = await asyncio.to_thread(self.agent.handle, text, self._session(update))
        await self._send(chat.id, reply.text, reply.files, voice)

    def quiet_now(self) -> bool:
        """Night-time messages arrive silently (no buzz) — e.g. mission and dream reports."""
        import datetime as dt
        q = self.cfg.telegram.get("quiet_hours") or ["22:00", "07:00"]
        now = dt.datetime.now().strftime("%H:%M")
        start, end = str(q[0]), str(q[1])
        return (start <= now or now < end) if start > end else (start <= now < end)

    async def _send(self, chat_id: int, text: str, files: list[str], voice: bool = False):
        bot = self.app.bot
        silent = self.quiet_now()
        for i in range(0, max(len(text), 1), 4000):
            await bot.send_message(chat_id, text[i:i + 4000] or "Done.", disable_notification=silent)
        if voice and text:
            try:
                with tempfile.TemporaryDirectory() as td:
                    wav, ogg = Path(td) / "r.wav", Path(td) / "r.ogg"
                    await asyncio.to_thread(self.speech.synth_wav, text[:1500], wav)
                    await asyncio.to_thread(ffmpeg.to_voice_note, wav, ogg)
                    with open(ogg, "rb") as fh:
                        await bot.send_voice(chat_id, fh)
            except Exception as e:
                print(f"[telegram] voice reply failed: {e}")
        for path in files:
            p = Path(path)
            if not p.exists():
                continue
            with open(p, "rb") as fh:
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    await bot.send_photo(chat_id, fh, caption=p.name, disable_notification=silent)
                elif p.suffix.lower() in (".mp4", ".mov") and p.stat().st_size < 50e6:
                    await bot.send_video(chat_id, fh, caption=p.name, disable_notification=silent)
                else:
                    await bot.send_document(chat_id, fh, filename=p.name, disable_notification=silent)

    # ── push from anywhere (reminders, voice follow-ups) ──
    def push(self, text: str, files: list[str]) -> None:
        if not (self.app and self.loop and self.allowed):
            return
        for uid in self.allowed:
            asyncio.run_coroutine_threadsafe(self._send(uid, text, files), self.loop)

    @staticmethod
    def is_stale(sent, max_age_min: float = 30) -> bool:
        """Messages sent while Nova was off are answered on start-up — unless they're too old to act on."""
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=dt.timezone.utc)
        return (now - sent).total_seconds() > max_age_min * 60

    async def _post_init(self, app: Application):
        self.loop = asyncio.get_running_loop()
        try:
            me = await app.bot.get_me()
            print(f"[telegram] connected as @{me.username}")
        except Exception as e:
            print(f"[telegram] couldn't reach Telegram yet: {e}")
        if self.cfg.telegram.get("announce_online", True):
            link = ""
            try:
                from .remote import status as ts_status
                url = (await asyncio.to_thread(ts_status))["url"]
                link = f"\n📱 Dashboard: {url}" if url else ""
            except Exception:
                pass
            for uid in self.allowed:
                try:
                    await app.bot.send_message(uid, f"🟢 {self.cfg.assistant.name} is online.{link}")
                except Exception as e:
                    print(f"[telegram] couldn't message {uid}: {e}")

    def say_goodbye(self) -> None:
        """Best effort '🔴 going offline' when Nova is stopped with Ctrl+C or restarted."""
        if self.cfg.telegram.get("announce_online", True) and self.app and self.loop:
            futs = [asyncio.run_coroutine_threadsafe(self.app.bot.send_message(uid, f"🔴 {self.cfg.assistant.name} is "
                                                                                   "going offline."), self.loop)
                    for uid in self.allowed]
            for f in futs:
                try:
                    f.result(timeout=4)
                except Exception:
                    pass

    def run(self) -> None:
        """Blocking. Run in the main thread."""
        if not self.token:
            print("[telegram] TELEGRAM_BOT_TOKEN missing in .env — Telegram disabled.")
            return
        asyncio.set_event_loop(asyncio.new_event_loop())      # runs in its own thread
        self.app = Application.builder().token(self.token).post_init(self._post_init).build()
        a = self.app
        a.add_handler(CommandHandler("id", self.cmd_id))
        a.add_handler(CommandHandler("start", self.cmd_start))
        a.add_handler(CommandHandler("new", self.cmd_new))
        a.add_handler(CommandHandler("status", self.cmd_status))
        a.add_handler(CommandHandler("save", self.cmd_save))
        a.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        a.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO, self.on_file))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        if not self.allowed:
            print("[telegram] No allowed_user_ids yet. Message your bot /id, add the number to config.yaml, restart.")
        print("[telegram] bot running.")
        a.run_polling(drop_pending_updates=False, stop_signals=None)    # pick up messages sent while Nova was off
