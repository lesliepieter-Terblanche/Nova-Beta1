"""Google Workspace: Gmail, Calendar, Drive, Docs, Sheets and Tasks (free Google APIs).

One-time setup: see README -> "Connect Google". Then run  python main.py --google-login
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import re
from email.mime.text import MIMEText
from pathlib import Path

from .. import context
from ..config import resolve
from ..tools import register_group, tool
from .system import parse_when

register_group("google", ["email", "e-mail", "mail", "inbox", "gmail", "unread", "reply", "calendar", "meeting",
                          "appointment", "event", "schedule", "diary", "free time", "busy", "drive", "google doc",
                          "doc ", "docs", "sheet", "spreadsheet", "task", "to-do", "todo", "to do"])

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/tasks",
]
_services: dict = {}


def login(interactive: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    g = context.cfg.google
    token, secret = resolve(g.token_file), resolve(g.credentials_file)
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("Google isn't connected yet. Run 'python main.py --google-login' once.")
        if not secret.exists():
            raise RuntimeError(f"Missing {secret}. See README -> Connect Google.")
        from google_auth_oauthlib.flow import InstalledAppFlow
        creds = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES).run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def svc(name: str, version: str):
    key = f"{name}:{version}"
    if key not in _services:
        from googleapiclient.discovery import build
        _services[key] = build(name, version, credentials=login(interactive=False), cache_discovery=False)
    return _services[key]


def _tz() -> str:
    return context.cfg.assistant.timezone


# ── Gmail ─────────────────────────────────────────────────
def _header(msg, name):
    return next((h["value"] for h in msg["payload"].get("headers", []) if h["name"].lower() == name.lower()), "")


def _body(payload) -> str:
    if payload.get("body", {}).get("data") and payload.get("mimeType", "").startswith("text/plain"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "ignore")
    for part in payload.get("parts", []) or []:
        text = _body(part)
        if text:
            return text
    if payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "ignore")
        return re.sub(r"<[^>]+>", " ", html)
    return ""


@tool(group="google")
def gmail_search(query: str = "is:unread in:inbox", max_results: int = 5) -> list:
    """Search Gmail. Uses Gmail search syntax.
    Args:
        query: e.g. "is:unread", "from:juniper newer_than:7d", "subject:invoice"
        max_results: how many emails
    """
    g = svc("gmail", "v1")
    ids = g.users().messages().list(userId="me", q=query, maxResults=max_results).execute().get("messages", [])
    out = []
    for m in ids:
        msg = g.users().messages().get(userId="me", id=m["id"], format="metadata",
                                       metadataHeaders=["From", "Subject", "Date"]).execute()
        out.append({"id": m["id"], "from": _header(msg, "From"), "subject": _header(msg, "Subject"),
                    "date": _header(msg, "Date"), "snippet": msg.get("snippet", "")[:200]})
    return out or "No matching emails."


@tool(group="google")
def gmail_read(message_id: str) -> str:
    """Read the full text of an email.
    Args:
        message_id: id from gmail_search
    """
    msg = svc("gmail", "v1").users().messages().get(userId="me", id=message_id, format="full").execute()
    context.record("email", _header(msg, "Subject") or "(no subject)",
                   f"https://mail.google.com/mail/u/0/#all/{message_id}", _header(msg, "From"))
    return (f"From: {_header(msg, 'From')}\nTo: {_header(msg, 'To')}\nDate: {_header(msg, 'Date')}\n"
            f"Subject: {_header(msg, 'Subject')}\n\n{_body(msg['payload']).strip()[:6000]}")


def _raw(to, subject, body, thread_headers=None):
    m = MIMEText(body)
    m["to"], m["subject"] = to, subject
    for k, v in (thread_headers or {}).items():
        m[k] = v
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


@tool(group="google", confirm=True)
def gmail_send(to: str, subject: str, body: str) -> str:
    """Send an email.
    Args:
        to: recipient email address
        subject: subject line
        body: the email text
    """
    sent = svc("gmail", "v1").users().messages().send(userId="me", body={"raw": _raw(to, subject, body)}).execute()
    context.record("email", f"Sent: {subject}", f"https://mail.google.com/mail/u/0/#sent/{sent['id']}", to)
    return f"Email sent to {to}."


@tool(group="google")
def gmail_draft(to: str, subject: str, body: str) -> str:
    """Save an email as a draft (not sent) so the user can review it in Gmail.
    Args:
        to: recipient email address
        subject: subject line
        body: the email text
    """
    d = svc("gmail", "v1").users().drafts().create(userId="me", body={"message": {"raw": _raw(to, subject, body)}}).execute()
    context.record("email", f"Draft: {subject}", "https://mail.google.com/mail/u/0/#drafts", to)
    return f"Draft saved (id {d['id']})."


@tool(group="google", confirm=True)
def gmail_reply(message_id: str, body: str) -> str:
    """Reply to an email.
    Args:
        message_id: id of the email to reply to
        body: reply text
    """
    g = svc("gmail", "v1")
    orig = g.users().messages().get(userId="me", id=message_id, format="metadata",
                                    metadataHeaders=["From", "Subject", "Message-ID", "Reply-To"]).execute()
    to = _header(orig, "Reply-To") or _header(orig, "From")
    subj = _header(orig, "Subject")
    subj = subj if subj.lower().startswith("re:") else f"Re: {subj}"
    mid = _header(orig, "Message-ID")
    raw = _raw(to, subj, body, {"In-Reply-To": mid, "References": mid})
    g.users().messages().send(userId="me", body={"raw": raw, "threadId": orig["threadId"]}).execute()
    return f"Replied to {to}."


@tool(group="google")
def gmail_archive(message_id: str) -> str:
    """Archive an email (remove from inbox) and mark it read.
    Args:
        message_id: email id
    """
    svc("gmail", "v1").users().messages().modify(userId="me", id=message_id,
                                                 body={"removeLabelIds": ["INBOX", "UNREAD"]}).execute()
    return "Archived."


# ── Calendar ──────────────────────────────────────────────
@tool(group="google")
def calendar_events(when: str = "today", days: int = 1) -> list:
    """List calendar events.
    Args:
        when: start day, e.g. "today", "tomorrow", "next Monday"
        days: how many days to include
    """
    start = parse_when(when).replace(hour=0, minute=0, second=0, microsecond=0) if when not in ("now",) else parse_when("now")
    end = start + dt.timedelta(days=days)
    items = svc("calendar", "v3").events().list(calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
                                               singleEvents=True, orderBy="startTime").execute().get("items", [])
    return [{"id": e["id"], "title": e.get("summary", "(no title)"),
             "start": e["start"].get("dateTime", e["start"].get("date")),
             "end": e["end"].get("dateTime", e["end"].get("date")), "where": e.get("location", "")}
            for e in items] or "Nothing on the calendar."


@tool(group="google")
def calendar_add(title: str, start: str, duration_minutes: int = 60, location: str = "", description: str = "") -> str:
    """Add an event to the calendar (no invitations are sent).
    Args:
        title: event title
        start: when, e.g. "tomorrow 10am" or "2026-10-02 14:00"
        duration_minutes: length in minutes
        location: optional place
        description: optional notes
    """
    s = parse_when(start)
    e = s + dt.timedelta(minutes=duration_minutes)
    ev = svc("calendar", "v3").events().insert(calendarId="primary", body={
        "summary": title, "location": location, "description": description,
        "start": {"dateTime": s.isoformat(), "timeZone": _tz()}, "end": {"dateTime": e.isoformat(), "timeZone": _tz()},
    }).execute()
    context.record("event", title, ev.get("htmlLink", ""), f"{s:%a %d %b %H:%M}")
    return f"Added '{title}' on {s:%a %d %b at %H:%M}."


@tool(group="google", confirm=True)
def calendar_invite(title: str, start: str, attendees: str, duration_minutes: int = 60, add_meet_link: bool = True) -> str:
    """Create a meeting and send invitations to other people.
    Args:
        title: meeting title
        start: when
        attendees: comma-separated email addresses
        duration_minutes: length
        add_meet_link: include a Google Meet link
    """
    s = parse_when(start)
    e = s + dt.timedelta(minutes=duration_minutes)
    body = {"summary": title, "start": {"dateTime": s.isoformat(), "timeZone": _tz()},
            "end": {"dateTime": e.isoformat(), "timeZone": _tz()},
            "attendees": [{"email": a.strip()} for a in attendees.split(",") if a.strip()]}
    if add_meet_link:
        body["conferenceData"] = {"createRequest": {"requestId": f"nova{int(s.timestamp())}"}}
    ev = svc("calendar", "v3").events().insert(calendarId="primary", body=body, sendUpdates="all",
                                               conferenceDataVersion=1).execute()
    context.record("event", title, ev.get("htmlLink", ""), attendees)
    return f"Invites sent for '{title}' on {s:%a %d %b at %H:%M}." + (f" Meet: {ev.get('hangoutLink')}" if ev.get("hangoutLink") else "")


@tool(group="google", confirm=True)
def calendar_delete(event_id: str) -> str:
    """Delete a calendar event.
    Args:
        event_id: id from calendar_events
    """
    svc("calendar", "v3").events().delete(calendarId="primary", eventId=event_id).execute()
    return "Event deleted."


# ── Drive / Docs / Sheets ─────────────────────────────────
@tool(group="google")
def drive_search(query: str, max_results: int = 10) -> list:
    """Find files in Google Drive by name or content.
    Args:
        query: words to look for
        max_results: how many
    """
    q = query.replace("'", "\\'")
    files = svc("drive", "v3").files().list(
        q=f"(name contains '{q}' or fullText contains '{q}') and trashed=false", pageSize=max_results,
        fields="files(id,name,mimeType,modifiedTime,webViewLink)", orderBy="modifiedTime desc").execute().get("files", [])
    return files or "Nothing found in Drive."


@tool(group="google")
def drive_download(file_id: str) -> str:
    """Download a Drive file to the PC (Google Docs/Sheets/Slides are exported as Word/Excel/PowerPoint).
    Args:
        file_id: id from drive_search
    """
    from googleapiclient.http import MediaIoBaseDownload
    d = svc("drive", "v3")
    meta = d.files().get(fileId=file_id, fields="name,mimeType").execute()
    exports = {
        "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
        "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    }
    name = meta["name"]
    if meta["mimeType"] in exports:
        mime, ext = exports[meta["mimeType"]]
        req, name = d.files().export_media(fileId=file_id, mimeType=mime), name + ext
    else:
        req = d.files().get_media(fileId=file_id)
    out = resolve("workspace/downloads") / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.FileIO(out, "wb") as fh:
        dl, done = MediaIoBaseDownload(fh, req), False
        while not done:
            _, done = dl.next_chunk()
    context.record("file", name, out, "from Google Drive")
    return f"Downloaded to {out}"


@tool(group="google")
def drive_upload(path: str) -> str:
    """Upload a file from the PC to Google Drive.
    Args:
        path: full path to the file
    """
    from googleapiclient.http import MediaFileUpload
    p = Path(path).expanduser()
    f = svc("drive", "v3").files().create(body={"name": p.name}, media_body=MediaFileUpload(str(p), resumable=True),
                                          fields="id,webViewLink").execute()
    context.record("file", p.name, f["webViewLink"], "uploaded to Drive")
    return f"Uploaded. Link: {f['webViewLink']}"


@tool(group="google")
def docs_create(title: str, content: str) -> str:
    """Create a Google Doc with the given text.
    Args:
        title: document title
        content: the text of the document
    """
    doc = svc("docs", "v1").documents().create(body={"title": title}).execute()
    svc("docs", "v1").documents().batchUpdate(documentId=doc["documentId"], body={
        "requests": [{"insertText": {"location": {"index": 1}, "text": content}}]}).execute()
    url = f"https://docs.google.com/document/d/{doc['documentId']}/edit"
    context.record("doc", title, url, content[:200])
    return f"Created '{title}': {url}"


@tool(group="google")
def docs_read(document_id: str) -> str:
    """Read a Google Doc's text.
    Args:
        document_id: the id (from drive_search or the doc URL)
    """
    doc = svc("docs", "v1").documents().get(documentId=document_id).execute()
    parts = []
    for el in doc.get("body", {}).get("content", []):
        for pe in el.get("paragraph", {}).get("elements", []):
            parts.append(pe.get("textRun", {}).get("content", ""))
    return "".join(parts)[:8000]


@tool(group="google")
def sheets_read(spreadsheet_id: str, cell_range: str = "A1:Z100") -> list:
    """Read cells from a Google Sheet.
    Args:
        spreadsheet_id: the id (from drive_search or the sheet URL)
        cell_range: e.g. "Sheet1!A1:F50"
    """
    return svc("sheets", "v4").spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=cell_range).execute().get("values", [])


@tool(group="google")
def sheets_append_row(spreadsheet_id: str, values: list[str], sheet: str = "Sheet1") -> str:
    """Add a row to the bottom of a Google Sheet.
    Args:
        spreadsheet_id: the id
        values: the cell values, in column order
        sheet: tab name
    """
    svc("sheets", "v4").spreadsheets().values().append(spreadsheetId=spreadsheet_id, range=f"{sheet}!A1",
                                                     valueInputOption="USER_ENTERED", body={"values": [values]}).execute()
    return "Row added."


@tool(group="google")
def sheets_create(title: str, header: list[str]) -> str:
    """Create a new Google Sheet with a header row.
    Args:
        title: sheet title
        header: column names
    """
    s = svc("sheets", "v4").spreadsheets().create(body={"properties": {"title": title}}).execute()
    sheets_append_row(s["spreadsheetId"], header)
    context.record("doc", title, s["spreadsheetUrl"], "Google Sheet")
    return f"Created: {s['spreadsheetUrl']} (id {s['spreadsheetId']})"


# ── Tasks ─────────────────────────────────────────────────
@tool(group="google")
def tasks_list() -> list:
    """List open Google Tasks (to-dos)."""
    items = svc("tasks", "v1").tasks().list(tasklist="@default", showCompleted=False).execute().get("items", [])
    return [{"id": t["id"], "title": t["title"], "due": t.get("due", "")[:10]} for t in items] or "No open tasks."


@tool(group="google")
def tasks_add(title: str, due: str = "", notes: str = "") -> str:
    """Add a to-do to Google Tasks.
    Args:
        title: the task
        due: optional due date, e.g. "Friday"
        notes: optional details
    """
    body = {"title": title, "notes": notes}
    if due:
        body["due"] = parse_when(due).strftime("%Y-%m-%dT00:00:00.000Z")
    svc("tasks", "v1").tasks().insert(tasklist="@default", body=body).execute()
    return f"Added task: {title}"


@tool(group="google")
def tasks_complete(task_id: str) -> str:
    """Mark a Google Task as done.
    Args:
        task_id: id from tasks_list
    """
    svc("tasks", "v1").tasks().patch(tasklist="@default", task=task_id, body={"status": "completed"}).execute()
    return "Marked done."
