"""Remote control from your phone via a private Telegram bot.

- Text or voice notes in, text (+ voice note in your ElevenLabs voice) out.
- Send it files/photos: they land in workspace/inbox and Nova is told about them.
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
                "/new – fresh conversation   /status – PC status   /id – your ID")

    async def cmd_new(self, update: Update, _):
        if await self._guard(update):
            self.agent.reset(self._session(update))
            await update.effective_message.reply_text("Fresh start. (Long-term memory is kept.)")

    async def cmd_status(self, update: Update, _):
        if await self._guard(update):
            await self._ask(update, "Give me a short PC status report.", voice=False)

    async def on_text(self, update: Update, _):
        if await self._guard(update):
            await self._ask(update, update.effective_message.text, voice=False)

    async def on_voice(self, update: Update, _):
        if not await self._guard(update):
            return
        msg = update.effective_message
        tg_file = await (msg.voice or msg.audio).get_file()
        with tempfile.TemporaryDirectory() as td:
            src, wav = Path(td) / "in.ogg", Path(td) / "in.wav"
            await tg_file.download_to_drive(src)
            await asyncio.to_thread(ffmpeg.to_wav16k, src, wav)
            text = await asyncio.to_thread(self.speech.transcribe, str(wav))
        if not text:
            await msg.reply_text("I couldn't make out that voice note.")
            return
        await msg.reply_text(f"🗣 {text}")
        await self._ask(update, text, voice=bool(self.cfg.telegram.get("voice_replies", True)))

    async def on_file(self, update: Update, _):
        if not await self._guard(update):
            return
        msg = update.effective_message
        if msg.photo:
            f, name = await msg.photo[-1].get_file(), f"photo_{msg.message_id}.jpg"
        else:
            doc = msg.document or msg.video
            f, name = await doc.get_file(), (getattr(doc, "file_name", None) or f"file_{msg.message_id}")
        dest = self.inbox / name
        await f.download_to_drive(dest)
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

    async def _send(self, chat_id: int, text: str, files: list[str], voice: bool = False):
        bot = self.app.bot
        for i in range(0, max(len(text), 1), 4000):
            await bot.send_message(chat_id, text[i:i + 4000] or "Done.")
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
                    await bot.send_photo(chat_id, fh, caption=p.name)
                elif p.suffix.lower() in (".mp4", ".mov") and p.stat().st_size < 50e6:
                    await bot.send_video(chat_id, fh, caption=p.name)
                else:
                    await bot.send_document(chat_id, fh, filename=p.name)

    # ── push from anywhere (reminders, voice follow-ups) ──
    def push(self, text: str, files: list[str]) -> None:
        if not (self.app and self.loop and self.allowed):
            return
        for uid in self.allowed:
            asyncio.run_coroutine_threadsafe(self._send(uid, text, files), self.loop)

    async def _post_init(self, app: Application):
        self.loop = asyncio.get_running_loop()

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
        a.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        a.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO, self.on_file))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        if not self.allowed:
            print("[telegram] No allowed_user_ids yet. Message your bot /id, add the number to config.yaml, restart.")
        print("[telegram] bot running.")
        a.run_polling(drop_pending_updates=True, stop_signals=None)
