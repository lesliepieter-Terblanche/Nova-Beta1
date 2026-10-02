"""Filing things into the 2nd brain: links, documents, photos, voice notes and messages.

Used by Telegram forwarding ("forward anything to the bot and it's filed") and by "save this to my brain".
Every item becomes a note in brain/Inbox (or another folder) with a short summary, the full text, where it came
from, the people in it (their cards are created / updated) and the project it belongs to.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import context, people
from .config import resolve

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
TEXT_EXT = {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json", ".html", ".htm", ".rtf", ".xml", ".log", ".eml",
            ".pptx", ".xls", ".msg", ".epub"}
AUDIO_EXT = {".ogg", ".oga", ".mp3", ".m4a", ".wav", ".opus", ".flac", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

SUMMARY_PROMPT = """You file things into a personal knowledge base for {owner}. Read this {what} and reply with JSON only:
{{"title": "a short, specific title (max 8 words)", "summary": ["3 to 5 short bullet points with the key facts, names,
numbers and dates"], "type": "article|document|message|voice note|whiteboard|business card|receipt|screenshot|photo|other"}}

{what}:
{text}"""

IMAGE_PROMPT = """Look at this image carefully.
- If it is a BUSINESS CARD, reply with JSON only: {"business_card": {"name": "", "company": "", "role": "", "email": "",
  "phone": "", "website": ""}}
- If it is a whiteboard, handwritten notes, a slide, a document, a receipt or a screenshot, first write "TYPE: <which>",
  then transcribe ALL the text exactly, keeping the structure (lists, arrows as ->, tables as rows).
- Otherwise write "TYPE: photo" and describe what is in it in detail (people, place, objects, any text)."""


def _vault() -> Path:
    v = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
    v.mkdir(parents=True, exist_ok=True)
    return v


def _slug(title: str) -> str:
    return re.sub(r"[^\w\- ,&]", "", title).strip()[:70] or "Untitled"


def _json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def summarise(text: str, what: str = "text", default_title: str = "") -> dict:
    """{"title", "summary": [bullets], "type"} — falls back gracefully when no model is available."""
    owner = (context.cfg.assistant.owner if context.cfg else "") or "the user"
    data = {}
    if context.llm and len(text.strip()) > 40:
        try:
            data = _json(context.llm.complete(SUMMARY_PROMPT.format(owner=owner, what=what, text=text[:12000]),
                                              prefer_smart=True, temperature=0.2))
        except Exception as e:
            print(f"[inbox] summary failed: {e}")
    bullets = data.get("summary") or []
    if isinstance(bullets, str):
        bullets = [b.strip("-• ").strip() for b in bullets.splitlines() if b.strip()]
    first = re.sub(r"\s+", " ", text.strip().split("\n")[0])[:60]
    return {"title": str(data.get("title") or default_title or first or what.title()).strip()[:80],
            "summary": [str(b) for b in bullets][:6], "type": str(data.get("type") or what)}


def _project_for(text: str) -> str:
    """The short name of the project this belongs to (same sorting as the dashboard), or ''."""
    try:
        import numpy as np

        from .dashboard.organize import organize
        s = context.store
        with s.lock:
            projs = s.db.execute("SELECT id,text,created,source,embedding FROM memories WHERE superseded_by IS NULL "
                                 "AND kind='project'").fetchall()
        if not projs:
            return ""
        vec = s.embed([text[:2000]])[0]
        projects = [{"id": f"memory:{p['id']}", "text": p["text"], "created": p["created"], "source": p["source"],
                     "vec": np.frombuffer(p["embedding"], dtype=np.float32) if p["embedding"] else None} for p in projs]
        owner = context.cfg.assistant.owner if context.cfg else ""
        res = organize(projects, [{"id": "new", "text": text[:4000], "vec": vec}], owner=owner)
        pid = res["item_project"].get("new")
        return res["short"].get(pid, "") if pid else ""
    except Exception as e:
        print(f"[inbox] project match failed: {e}")
        return ""


def _save(title: str, text: str, info: dict, source: str, folder: str = "Inbox", origin: str = "",
          attachment: Path | None = None, note: str = "") -> dict:
    vault = _vault()
    stamp = dt.datetime.now()
    path = vault / folder / f"{stamp:%Y-%m-%d} {_slug(title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    i = 2
    while path.exists():
        path = path.with_name(f"{stamp:%Y-%m-%d} {_slug(title)} ({i}).md")
        i += 1
    lines = [f"# {title}", "", f"_Filed {stamp:%Y-%m-%d %H:%M} · {info.get('type', 'item')} · via {source}_"]
    if origin:
        lines += [f"Source: {origin}"]
    lines.append("")
    if note:
        lines += [f"> {note}", ""]
    if attachment:
        rel = attachment.relative_to(vault).as_posix()
        lines += [f"![{attachment.name}]({rel.replace(' ', '%20')})", ""]
    if info.get("summary"):
        lines += ["## Summary", *[f"- {b}" for b in info["summary"]], ""]
    who = info.get("people_names") or []
    if who:
        lines += ["## People", *[f"- {n}" for n in who], ""]
    if info.get("project"):
        lines += [f"Project: {info['project']}", ""]
    lines += ["## Content", "", text.strip()[:60000], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    s = context.store
    s.index_note(path)
    context.record("note", title, path, f"filed via {source}")
    s.log("memory", source, f"📥 Filed: {title}", "\n".join(info.get("summary") or [])[:1500], f"note:{path}", turn=0)
    return {"title": title, "path": str(path), "summary": info.get("summary") or [], "type": info.get("type", ""),
            "people": who, "project": info.get("project", ""), "id": f"note:{path}"}


def _finish(text: str, what: str, source: str, folder: str, origin: str = "", attachment: Path | None = None,
            note: str = "", title: str = "", from_who: str = "", default_title: str = "") -> dict:
    info = summarise(text if not note else f"{note}\n\n{text}", what, default_title)
    if title:
        info["title"] = title
    if what in ("business card",):
        info["type"] = what
    ids = people.absorb(f"{from_who}\n{text[:6000]}" if from_who else text[:6000], source=source,
                        contact=dt.datetime.now().isoformat(timespec="seconds") if from_who else None)
    info["people_names"] = [p["name"] for p in (people.get(i) for i in dict.fromkeys(ids)) if p]
    info["project"] = _project_for(f"{info['title']} {note} {text[:3000]}")
    return _save(info["title"], text, info, source, folder, origin, attachment, note)


# ── the kinds of things you can file ──────────────────────
def file_text(text: str, source: str = "telegram", note: str = "", from_who: str = "", folder: str = "Inbox") -> dict:
    head = f"From: {from_who}\n\n" if from_who else ""
    return _finish(head + text, "message", source, folder, note=note, from_who=from_who)


def file_url(url: str, source: str = "telegram", note: str = "", folder: str = "Inbox") -> dict:
    from .skills.web import fetch_text
    try:
        text, page_title = fetch_text(url)
    except Exception as e:
        text, page_title = f"(Couldn't read the page: {e})", ""
    if "youtube.com/" in url or "youtu.be/" in url:
        text = (text or "") + "\n\n(YouTube video — ask Nova to summarise it for the transcript.)"
    return _finish(text or "", "web page", source, folder, origin=url, note=note,
                   title=(page_title or "")[:80] if page_title and len(text or "") < 200 else "")


def file_image(path: str | Path, source: str = "telegram", note: str = "", folder: str = "Inbox") -> dict:
    src = Path(path)
    att_dir = _vault() / folder / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    dest = att_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{src.name}"
    shutil.copy2(src, dest)
    seen = ""
    try:
        seen = context.llm.see(str(dest), IMAGE_PROMPT) if context.llm else ""
    except Exception as e:
        seen = f"(Couldn't look at the image: {e})"
    card = _json(seen).get("business_card") if "business_card" in (seen or "") else None
    if isinstance(card, dict) and card.get("name"):
        pid, created = people.upsert(card["name"], card.get("company", ""), card.get("role", ""),
                                     card.get("email", ""), card.get("phone", ""),
                                     f"Business card{' — ' + card['website'] if card.get('website') else ''}",
                                     source=source, contact=dt.datetime.now().isoformat(timespec="seconds"))
        text = "\n".join(f"{k.title()}: {v}" for k, v in card.items() if v)
        info = {"title": f"Business card — {card['name']}", "type": "business card",
                "summary": [f"{card['name']}" + (f", {card.get('role')}" if card.get("role") else "")
                            + (f" at {card.get('company')}" if card.get("company") else ""),
                            *(x for x in (card.get("email"), card.get("phone")) if x)],
                "people_names": [card["name"]], "project": _project_for(f"{text} {note}")}
        out = _save(info["title"], text, info, source, folder, attachment=dest, note=note)
        out["person"] = {"id": pid, "created": created}
        return out
    m = re.match(r"\s*TYPE:\s*([^\n]+)\n?(.*)", seen or "", re.S)
    what, body = (m.group(1).strip().lower(), m.group(2).strip()) if m else ("photo", (seen or "").strip())
    return _finish(body or "(no text found)", what if what in ("whiteboard", "receipt", "screenshot", "slide",
                                                              "document", "handwritten notes") else "photo",
                   source, folder, attachment=dest, note=note)


def file_document(path: str | Path, source: str = "telegram", note: str = "", folder: str = "Inbox") -> dict:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in IMAGE_EXT:
        return file_image(p, source, note, folder)
    if ext in AUDIO_EXT:
        text = context.speech.transcribe(str(p)) if context.speech else ""
        return file_voice(text, source, note, folder)
    if ext in VIDEO_EXT:                                   # keep the video, file what is said in it
        try:
            said = context.speech.transcribe(str(p)) if context.speech else ""
        except Exception as e:
            said = ""
            print(f"[inbox] couldn't transcribe {p.name}: {e}")
        kept = resolve("workspace/inbox") / p.name
        if p.resolve() != kept.resolve():
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, kept)
        text = said or f"(A video, {p.stat().st_size // (1024 * 1024)} MB — no speech found in it.)"
        return _finish(text, "video", source, folder, origin=str(kept), note=note, title="" if said else p.stem)
    if ext in TEXT_EXT:
        try:
            from .skills.files import extract_text
            text = extract_text(p)
        except Exception as e:
            text = f"(Couldn't read the text of {p.name}: {e})"
    else:
        text = f"(A {ext.lstrip('.').upper() or 'binary'} file, {p.stat().st_size // 1024} KB — saved, not readable as text.)"
    kept = resolve("workspace/inbox") / p.name
    if p.resolve() != kept.resolve():
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, kept)
    return _finish(text or "(empty)", "document", source, folder, origin=str(kept), note=note,
                   title=p.stem if not text.strip() else "", default_title=p.stem)


def file_voice(transcript: str, source: str = "telegram", note: str = "", folder: str = "Inbox",
               from_who: str = "") -> dict:
    return _finish(transcript or "(no speech found)", "voice note", source, folder, note=note, from_who=from_who)


def reply_text(r: dict) -> str:
    """The message Nova sends back after filing."""
    out = [f"📥 Filed in your brain: {r['title']}"]
    out += [f"• {b}" for b in r["summary"][:4]]
    if r.get("person"):
        out.append(f"👤 {'New card' if r['person']['created'] else 'Updated card'}: {r['people'][0]}")
    elif r["people"]:
        out.append("👤 People: " + ", ".join(r["people"][:6]))
    if r["project"]:
        out.append(f"📁 Project: {r['project']}")
    return "\n".join(out)
