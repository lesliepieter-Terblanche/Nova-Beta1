"""A business's own mailbox, connected directly: its email address and password, nothing forwarded to Gmail.

Nova reads the inbox over IMAP (without marking anything as read) and sends approved replies over SMTP, so they
come from the business's own address and land in its Sent folder. The mail servers are worked out from the address
(the usual `mail.yourdomain`, or the provider the domain's mail goes to); you can also type them in.

The password is kept in Nova's .env file on this PC, never in the database and never sent back to the browser.
Google and Microsoft mailboxes need an *app password* (Microsoft often allows no password sign-in at all).
"""
from __future__ import annotations

import datetime as dt
import email
import email.policy
import html
import imaplib
import os
import re
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

from . import context

TIMEOUT = 12
DAYS = 7                 # how far back the inbox is read
PEEK = 40000             # bytes of a message that are fetched: enough for the text, not the attachments
_SCHEMA = """
CREATE TABLE IF NOT EXISTS biz_mailbox(biz TEXT PRIMARY KEY, email TEXT, user TEXT, imap_host TEXT,
  imap_port INTEGER DEFAULT 993, smtp_host TEXT DEFAULT '', smtp_port INTEGER DEFAULT 465, connected TEXT,
  last_ok TEXT DEFAULT '', error TEXT DEFAULT '');
"""
# who runs the domain's mail (from its MX record, or the address itself) → (IMAP host, SMTP host, SMTP port)
PROVIDERS = [
    (r"google|gmail", ("imap.gmail.com", "smtp.gmail.com", 465)),
    (r"outlook|office365|hotmail|live\.com|microsoft", ("outlook.office365.com", "smtp.office365.com", 587)),
    (r"zoho", ("imap.zoho.com", "smtp.zoho.com", 465)),
    (r"icloud|me\.com", ("imap.mail.me.com", "smtp.mail.me.com", 587)),
    (r"yahoo", ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465)),
]
APP_PASSWORD = re.compile(r"google|gmail|outlook|office365|icloud|me\.com|yahoo")
ROBOT = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?|notifications?)[\w.+-]*@", re.I)
FIELDS = "MESSAGE-ID FROM REPLY-TO SUBJECT AUTO-SUBMITTED PRECEDENCE LIST-UNSUBSCRIBE LIST-ID"


class MailError(Exception):
    """Something the user can act on, in plain words."""


# ── storage ───────────────────────────────────────────────
def _db():
    s = context.store
    if s is None:
        raise RuntimeError("Nova's memory isn't open")
    if not getattr(s, "_mailbox_ready", False):
        with s.lock:
            s.db.executescript(_SCHEMA)
            s.db.commit()
        s._mailbox_ready = True
    return s


def _q(sql: str, args: tuple = ()) -> list[dict]:
    s = _db()
    with s.lock:
        return [dict(r) for r in s.db.execute(sql, args)]


def _x(sql: str, args: tuple = ()) -> None:
    s = _db()
    with s.lock:
        s.db.execute(sql, args)
        s.db.commit()


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def env_key(biz: str) -> str:
    return "BIZ_MAIL_" + re.sub(r"[^A-Z0-9]+", "_", biz.upper()).strip("_")


def password(biz: str) -> str:
    return os.environ.get(env_key(biz), "")


def box(biz: str) -> dict | None:
    """The saved connection for a business (no password in it), or None."""
    rows = _q("SELECT * FROM biz_mailbox WHERE biz=?", (biz,))
    return rows[0] if rows and password(biz) else None


def can_send(biz: str) -> bool:
    m = box(biz)
    return bool(m and m["smtp_host"])


def status(biz: str) -> dict:
    """What the Business dashboard shows. Never the password."""
    m = box(biz)
    if not m:
        return {"connected": False}
    return {"connected": True, "email": m["email"], "user": m["user"], "imap_host": m["imap_host"],
            "smtp_host": m["smtp_host"], "can_send": bool(m["smtp_host"]), "last_ok": m["last_ok"], "error": m["error"]}


def disconnect(biz: str) -> None:
    from . import settings
    _x("DELETE FROM biz_mailbox WHERE biz=?", (biz,))
    settings.write_env({env_key(biz): None})


# ── finding the servers ───────────────────────────────────
def mx_hosts(domain: str) -> list[str]:
    """Where the domain's mail goes, asked from the PC's own DNS (nslookup). Empty when that isn't possible."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(["nslookup", "-type=mx", domain], capture_output=True, text=True, timeout=8,
                             creationflags=flags).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [h.rstrip(".").lower() for h in re.findall(r"mail exchanger\s*=\s*(?:\d+\s+)?(\S+)", out)]


def servers(address: str, mx: list[str] | None = None) -> list[tuple[str, str, int]]:
    """The (IMAP host, SMTP host, SMTP port) combinations worth trying for an address, most likely first."""
    domain = address.rsplit("@", 1)[-1].lower()
    mx = mx_hosts(domain) if mx is None else mx
    out = []
    for pattern, hosts in PROVIDERS:
        if re.search(pattern, " ".join([domain] + mx)):
            out.append(hosts)
    if not out:                                       # an ordinary web host: mail.yourdomain almost everywhere
        out += [(f"mail.{domain}", f"mail.{domain}", 465), (f"imap.{domain}", f"smtp.{domain}", 465)]
        out += [(h, h, 465) for h in mx[:2]]
        out.append((domain, domain, 465))
    seen, uniq = set(), []
    for o in out:
        if o not in seen:
            seen.add(o)
            uniq.append(o)
    return uniq


def _imap(host: str, port: int, user: str, pw: str) -> imaplib.IMAP4_SSL:
    c = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
    try:
        c.login(user, pw)
    except Exception:
        try:
            c.shutdown()
        except Exception:
            pass
        raise
    return c


def _smtp(host: str, port: int, user: str, pw: str) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if port == 465:
        c = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx)
    else:
        c = smtplib.SMTP(host, port, timeout=TIMEOUT)
        c.starttls(context=ctx)
    try:
        c.login(user, pw)
    except Exception:
        try:
            c.close()
        except Exception:
            pass
        raise
    return c


def _refused(address: str, mx: list[str]) -> str:
    msg = "The mailbox refused that username and password — check them (the same ones you use for webmail)."
    if APP_PASSWORD.search(" ".join([address.lower()] + mx)):
        msg += (" This is a Google, Microsoft, Apple or Yahoo mailbox: those need an 'app password' made in the "
                "account's security settings, not your normal password.")
    return msg


def connect(biz: str, address: str, pw: str, user: str = "", imap_host: str = "", smtp_host: str = "") -> dict:
    """Try the address and password against its mail servers and, when reading works, save the connection.
    Returns {"ok", "message"}. Sending is tested too; a mailbox that can be read but not sent from is still saved."""
    from . import settings
    address, user, pw = address.strip().lower(), user.strip(), pw.strip() if pw else ""
    old = box(biz)
    pw = pw or (password(biz) if old and old["email"] == address else "")
    if not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", address):
        raise MailError(f"'{address}' doesn't look like an email address")
    if not pw:
        raise MailError("Type the mailbox's password too")
    user = user or address
    domain = address.rsplit("@", 1)[-1]
    mx = mx_hosts(domain)
    tries = servers(address, mx)
    if imap_host.strip():
        h = imap_host.strip().lower()
        tries = [(h, (smtp_host.strip().lower() or h), 465)]
    found, why = None, ""
    for ih, sh, sp in tries:
        try:
            _imap(ih, 993, user, pw).logout()
            found = (ih, sh, sp)
            break
        except imaplib.IMAP4.error:
            raise MailError(_refused(address, mx)) from None
        except (OSError, ssl.SSLError) as e:
            why = f"{ih}: {e}"
    if not found:
        raise MailError("I couldn't reach the mail server for " + domain + " (tried " + ", ".join(t[0] for t in tries)
                        + "). Open 'Advanced' and type the incoming and outgoing server names from your email host."
                        + (f" Last answer — {why}" if why else ""))
    ih, sh, sp = found
    if smtp_host.strip():
        sh = smtp_host.strip().lower()
    send_ok, send_why = "", ""
    for host, port in [(sh, sp), (sh, 587 if sp == 465 else 465)] + ([(ih, 465), (ih, 587)] if ih != sh else []):
        try:
            _smtp(host, port, user, pw).quit()
            send_ok = (host, port)
            break
        except (OSError, ssl.SSLError, smtplib.SMTPException) as e:
            send_why = f"{host}:{port}: {e}"
    settings.write_env({env_key(biz): pw})
    _x("INSERT OR REPLACE INTO biz_mailbox(biz,email,user,imap_host,imap_port,smtp_host,smtp_port,connected,last_ok,error) "
       "VALUES(?,?,?,?,?,?,?,?,?,?)",
       (biz, address, user, ih, 993, send_ok[0] if send_ok else "", send_ok[1] if send_ok else 465,
        old["connected"] if old and old["email"] == address else now(), now(),
        "" if send_ok else ("can't send yet — " + send_why)[:300]))
    if send_ok:
        return {"ok": True, "message": f"Connected to {address}. I read its enquiries and send approved replies from it."}
    return {"ok": True, "message": f"Connected to {address} for reading, but I can't send from it yet ({send_why[:160]}). "
                                   "Open 'Advanced' and type the outgoing server name from your email host."}


# ── reading ───────────────────────────────────────────────
def _text(msg) -> str:
    """The readable text of a message: its plain part, or its web part with the tags taken out."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        body = part.get_content()
    except (LookupError, ValueError, KeyError):
        raw = part.get_payload(decode=True) or b""
        body = raw.decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        body = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", body)
        body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", body)
        body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in body.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _parse(raw: bytes):
    return email.message_from_bytes(raw, policy=email.policy.default)


def _machine(msg) -> bool:
    """Newsletters, bounces and other automatic mail: not enquiries."""
    auto = str(msg.get("Auto-Submitted", "") or "").strip().lower()
    if auto and auto != "no":
        return True
    if str(msg.get("Precedence", "") or "").strip().lower() in ("bulk", "list", "junk"):
        return True
    if msg.get("List-Unsubscribe") or msg.get("List-Id"):
        return True
    sender = parseaddr(str(msg.get("From", "") or ""))[1]
    return bool(ROBOT.match(sender)) and not msg.get("Reply-To")       # a website form may send as no-reply


def _pairs(data) -> list[tuple[str, bytes]]:
    """imaplib's FETCH answer → [(everything around the literal, the literal)]. Some servers put the flags after
    the message text, so what follows it is kept with it."""
    out = []
    for d in data or []:
        if isinstance(d, tuple) and len(d) >= 2:
            out.append([d[0].decode("ascii", "replace"), d[1]])
        elif isinstance(d, bytes) and out:
            out[-1][0] += " " + d.decode("ascii", "replace")
    return [(a, b) for a, b in out]


def fetch(biz: str, known=lambda ref: False, limit: int = 40) -> list[dict]:
    """New enquiries in a business's inbox: mail from the last week that is unread, or arrived since the mailbox
    was connected. Nothing is marked as read. `known(ref)` says which ones are logged already.
    Returns [{"id", "from", "subject", "snippet"}]; "id" is "mbox:<the message's own id>"."""
    m = box(biz)
    if not m:
        return []
    try:
        c = _imap(m["imap_host"], m["imap_port"] or 993, m["user"], password(biz))
    except Exception as e:
        _x("UPDATE biz_mailbox SET error=? WHERE biz=?", (f"couldn't sign in — {e}"[:300], biz))
        raise MailError(f"I couldn't sign in to {m['email']}: {e}") from None
    out = []
    try:
        c.select("INBOX", readonly=True)
        since = (dt.date.today() - dt.timedelta(days=DAYS)).strftime("%d-%b-%Y")
        _, data = c.uid("search", None, "SINCE", since)
        uids = (data[0] or b"").split()[-limit:]
        if uids:
            connected = dt.datetime.fromisoformat(m["connected"]) if m["connected"] else dt.datetime.now()
            _, heads = c.uid("fetch", b",".join(uids), f"(FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS ({FIELDS})])")
            want = []
            for meta, raw in _pairs(heads):
                uid = re.search(r"UID (\d+)", meta)
                h = _parse(raw)
                mid = str(h.get("Message-ID", "") or "").strip()
                ref = "mbox:" + (mid or f"{biz}/{uid.group(1) if uid else ''}")
                flags = [f.decode().lower() for f in imaplib.ParseFlags(meta.encode())]
                when = imaplib.Internaldate2tuple(meta.encode())
                arrived = dt.datetime(*when[:6]) if when else connected
                if not uid or "\\answered" in flags or "\\deleted" in flags or "\\draft" in flags:
                    continue
                if "\\seen" in flags and arrived < connected:
                    continue                            # old mail you have already read stays yours
                if _machine(h) or known(ref):
                    continue
                want.append((uid.group(1), ref))
            for uid, ref in want:
                _, full = c.uid("fetch", uid, f"(BODY.PEEK[]<0.{PEEK}>)")
                pairs = _pairs(full)
                if not pairs:
                    continue
                msg = _parse(pairs[0][1])
                sender = str(msg.get("Reply-To") or msg.get("From") or "")
                out.append({"id": ref, "from": sender, "subject": str(msg.get("Subject", "") or "").strip(),
                            "snippet": _text(msg)[:1500]})
        _x("UPDATE biz_mailbox SET last_ok=?, error=CASE WHEN smtp_host='' THEN error ELSE '' END WHERE biz=?", (now(), biz))
    finally:
        try:
            c.logout()
        except Exception:
            pass
    return out


# ── sending ───────────────────────────────────────────────
def _one_line(s: str) -> str:
    return re.sub(r"[\r\n]+", " ", s or "").strip()


def _file_sent(m: dict, pw: str, raw: bytes) -> None:
    """Put a copy in the mailbox's Sent folder, so it shows in webmail and on the phone. Best effort."""
    try:
        c = _imap(m["imap_host"], m["imap_port"] or 993, m["user"], pw)
    except Exception:
        return
    try:
        _, boxes = c.list()
        names = []
        for b in boxes or []:
            line = b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)
            got = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line.strip())
            if got:
                names.append((r"\Sent" in got.group("flags"), got.group("name").strip().strip('"')))
        folder = next((n for flagged, n in names if flagged), None) or next(
            (n for _, n in names if n.lower() in ("sent", "inbox.sent", "sent items", "sent messages", "inbox.sent items")), None)
        if folder:
            c.append(f'"{folder}"', r"(\Seen)", imaplib.Time2Internaldate(dt.datetime.now().astimezone()), raw)
    except Exception as e:
        print(f"[mailbox] sent, but not copied to the Sent folder: {e}")
    finally:
        try:
            c.logout()
        except Exception:
            pass


def send(biz: str, to: str, subject: str, body: str, in_reply_to: str = "", name: str = "") -> str:
    """Send an email from a business's own address. Returns a sentence; raises MailError when it can't."""
    m = box(biz)
    if not m:
        raise MailError("that business's mailbox isn't connected (Business dashboard → Connections)")
    if not m["smtp_host"]:
        raise MailError(f"I can read {m['email']} but can't send from it yet — set the outgoing server under "
                        "Connections → Advanced on the Business dashboard")
    to = parseaddr(_one_line(to))[1]
    if not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", to):
        raise MailError("that isn't an email address I can send to")
    msg = EmailMessage()
    msg["From"] = formataddr((_one_line(name), m["email"])) if name else m["email"]
    msg["To"] = to
    msg["Subject"] = _one_line(subject) or "(no subject)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=m["email"].rsplit("@", 1)[-1])
    ref = _one_line(in_reply_to)
    if ref.startswith("<") and ref.endswith(">"):
        msg["In-Reply-To"] = ref
        msg["References"] = ref
    msg.set_content(body.strip() + "\n")
    pw = password(biz)
    try:
        c = _smtp(m["smtp_host"], m["smtp_port"] or 465, m["user"], pw)
        try:
            c.send_message(msg)
        finally:
            try:
                c.quit()
            except Exception:
                pass
    except (OSError, ssl.SSLError, smtplib.SMTPException) as e:
        _x("UPDATE biz_mailbox SET error=? WHERE biz=?", (f"couldn't send — {e}"[:300], biz))
        raise MailError(f"the mail server wouldn't send it: {e}") from None
    _x("UPDATE biz_mailbox SET last_ok=?, error='' WHERE biz=?", (now(), biz))
    _file_sent(m, pw, msg.as_bytes())
    return f"Email sent to {to} from {m['email']}."
