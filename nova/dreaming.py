"""Nightly dreaming: while you sleep, Nova tidies its memory, connects ideas, writes the day's journal and backs up.

  1. Consolidate — near-duplicate memories are merged into one clear memory (old versions kept as history).
  2. Connect     — finds related-but-separate memories and notes and writes down why they might belong together.
  3. Journal     — a short journal of the day: what you asked, what got done, decisions, open loops.
  4. Back up     — an encrypted backup of the brain, notes, settings and keys (last 7 kept; optional Google Drive).
  5. Report      — a "Dream" note plus a quiet Telegram summary (night messages don't buzz).

Runs every night at dreaming.at (default 02:30). If the PC was off, it catches up a few minutes after Nova starts.
Restore a backup:  python -m nova.dreaming restore backups\\nova-backup-….nova  [--apply]
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

from . import context
from .config import resolve

ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = """CREATE TABLE IF NOT EXISTS dreams(
  id INTEGER PRIMARY KEY, day TEXT, started TEXT, finished TEXT, status TEXT, stats TEXT, note TEXT, backup TEXT)"""
MAGIC = b"NOVABK1"

MERGE_PROMPT = """These memories about {owner} say (almost) the same thing. Merge them into ONE clear sentence in the
third person that keeps every specific detail. If they disagree, the newest one wins.
{items}
Reply with the merged sentence only."""
CONNECT_PROMPT = """You are reviewing {owner}'s second brain overnight. Below are pairs of memories/notes that are
related but stored separately. Pick up to 3 pairs where noticing the connection could genuinely help {owner}
(an opportunity, a risk, a follow-up, a pattern). Skip obvious or trivial ones.
{pairs}
Reply with JSON only: {{"connections": [{{"pair": 1, "insight": "one or two sentences"}}]}}"""
JOURNAL_PROMPT = """Write {owner}'s journal entry for {day} from what his assistant saw today. Warm but brief, first person
plural is fine ("we"). Sections: a 2–3 sentence summary, "Done", "Decisions & things learned", "Open loops".
Only use the facts below; skip empty sections.

Requests and results:
{turns}

Made / learned:
{made}

Open loops:
{loops}"""


def now() -> dt.datetime:
    return dt.datetime.now()


def _cfg() -> dict:
    return dict(((context.cfg or {}).get("dreaming") or {}))


# ── encryption ────────────────────────────────────────────
def _key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def encrypt(data: bytes, passphrase: str) -> bytes:
    from cryptography.fernet import Fernet
    salt = os.urandom(16)
    return MAGIC + salt + Fernet(_key(passphrase, salt)).encrypt(data)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken
    if not blob.startswith(MAGIC):
        raise ValueError("That isn't a Nova backup file.")
    salt, token = blob[len(MAGIC):len(MAGIC) + 16], blob[len(MAGIC) + 16:]
    try:
        return Fernet(_key(passphrase, salt)).decrypt(token)
    except InvalidToken:
        raise ValueError("Wrong passphrase (or the file is damaged).") from None


def passphrase(create: bool = True) -> tuple[str, bool]:
    """The backup passphrase from .env; creates one the first time. Returns (passphrase, newly_created)."""
    from . import settings
    p = settings.read_env().get("NOVA_BACKUP_PASSPHRASE", "").strip() or os.environ.get("NOVA_BACKUP_PASSPHRASE", "")
    if p or not create:
        return p, False
    words = "-".join(secrets.token_hex(3) for _ in range(5))
    settings.write_env({"NOVA_BACKUP_PASSPHRASE": words})
    os.environ["NOVA_BACKUP_PASSPHRASE"] = words
    return words, True


# ── backup / restore ──────────────────────────────────────
def backup(dest_dir: Path | None = None, keep: int | None = None) -> dict:
    c = _cfg()
    dest = dest_dir or resolve(c.get("backup_dir", "backups"))
    dest.mkdir(parents=True, exist_ok=True)
    keep = int(keep or c.get("backup_keep", 7))
    pw, created = passphrase()
    buf = io.BytesIO()
    files = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        db = resolve(context.cfg.brain.db_file) if context.cfg else resolve("data/nova.db")
        if db.exists():
            with tempfile.TemporaryDirectory() as td:          # consistent copy while Nova is running
                tmp = Path(td) / "nova.db"
                src = sqlite3.connect(db)
                out = sqlite3.connect(tmp)
                src.backup(out)
                out.close()
                src.close()
                z.write(tmp, "data/nova.db")
                files += 1
        vault = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
        for f in vault.rglob("*") if vault.exists() else []:
            if f.is_file() and f.stat().st_size < 50_000_000:
                z.write(f, "brain/" + f.relative_to(vault).as_posix())
                files += 1
        for name in ("config.yaml", ".env", "roadmap.yaml"):
            if (ROOT / name).exists():
                z.write(ROOT / name, name)
                files += 1
        sec = ROOT / "secrets"
        for f in sec.rglob("*") if sec.exists() else []:
            if f.is_file():
                z.write(f, "secrets/" + f.relative_to(sec).as_posix())
                files += 1
        z.writestr("NOVA-BACKUP.txt", f"Nova backup {now():%Y-%m-%d %H:%M}. Restore with:\n"
                                      "python -m nova.dreaming restore <this file> --apply\n")
    out = dest / f"nova-backup-{now():%Y%m%d-%H%M%S}.nova"
    out.write_bytes(encrypt(buf.getvalue(), pw))
    old = sorted(dest.glob("nova-backup-*.nova"))
    for f in old[:-keep]:
        f.unlink(missing_ok=True)
    info = {"path": str(out), "size_mb": round(out.stat().st_size / 1e6, 2), "files": files, "new_passphrase": created}
    if c.get("drive_backup", False):
        try:
            info["drive"] = _upload_drive(out, keep)
        except Exception as e:
            info["drive_error"] = str(e)
    return info


def _upload_drive(path: Path, keep: int) -> str:
    from googleapiclient.http import MediaFileUpload

    from .skills.google_ws import svc
    drive = svc("drive", "v3")
    q = "name='Nova Backups' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    found = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    folder = found[0]["id"] if found else drive.files().create(
        body={"name": "Nova Backups", "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()["id"]
    drive.files().create(body={"name": path.name, "parents": [folder]},
                         media_body=MediaFileUpload(str(path), resumable=True), fields="id").execute()
    files = drive.files().list(q=f"'{folder}' in parents and trashed=false", orderBy="name",
                               fields="files(id,name)").execute().get("files", [])
    for f in files[:-keep]:
        drive.files().delete(fileId=f["id"]).execute()
    return "Google Drive › Nova Backups"


def restore(path: str | Path, pw: str, apply: bool = False, target: Path | None = None) -> Path:
    """Decrypt a backup. Without apply: unpack into restore/<name>/ to look at. With apply: put files back
    (stop Nova first — the current files are moved to restore/before-<time>/)."""
    data = decrypt(Path(path).read_bytes(), pw)
    root = target or ROOT
    out = root / "restore" / Path(path).stem
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(out)
        if not apply:
            return out
        stash = root / "restore" / f"before-{now():%Y%m%d-%H%M%S}"
        brain_dir = Path((context.cfg.brain.vault_dir if context.cfg else "brain"))
        db_rel = Path((context.cfg.brain.db_file if context.cfg else "data/nova.db"))
        for name in z.namelist():
            if name == "NOVA-BACKUP.txt" or name.endswith("/"):
                continue
            rel = Path(name)
            if rel.parts[0] == "brain":
                dest = root / brain_dir / Path(*rel.parts[1:])
            elif name == "data/nova.db":
                dest = root / db_rel
            else:
                dest = root / rel
            if dest.exists():
                (stash / rel).parent.mkdir(parents=True, exist_ok=True)
                dest.replace(stash / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            (out / rel).replace(dest) if (out / rel).exists() else None
    return root


# ── the dream ─────────────────────────────────────────────
class Dreamer:
    def __init__(self):
        self.running = False
        self.step = ""
        self._thread: threading.Thread | None = None

    def _db(self):
        s = context.store
        with s.lock:
            s.db.execute(_SCHEMA)
        return s

    def last(self) -> dict | None:
        s = self._db()
        with s.lock:
            r = s.db.execute("SELECT * FROM dreams ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["stats"] = json.loads(d["stats"] or "{}")
        return d

    # 1. consolidate
    def consolidate(self, limit: int = 15) -> list[dict]:
        s = context.store
        with s.lock:
            rows = s.db.execute("SELECT id,kind,text,created,importance,uses,embedding FROM memories "
                                "WHERE superseded_by IS NULL ORDER BY id").fetchall()
        n = len(rows)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        norm = [re.sub(r"\W+", " ", r["text"].lower()).strip() for r in rows]
        vecs = [np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else None for r in rows]
        idx = [i for i, v in enumerate(vecs) if v is not None]
        if len(idx) > 1:
            dims = {}
            for i in idx:
                dims.setdefault(len(vecs[i]), []).append(i)
            for group in dims.values():
                if len(group) < 2:
                    continue
                mat = np.stack([vecs[i] for i in group])
                mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
                sim = mat @ mat.T
                for a in range(len(group)):
                    for b in range(a + 1, len(group)):
                        if sim[a, b] >= float(_cfg().get("merge_similarity", 0.92)):
                            parent[find(group[a])] = find(group[b])
        seen = {}
        for i, t in enumerate(norm):
            if t in seen:
                parent[find(i)] = find(seen[t])
            else:
                seen[t] = i
        clusters = {}
        for i in range(n):
            clusters.setdefault(find(i), []).append(i)
        merged = []
        owner = context.cfg.assistant.owner if context.cfg else "the user"
        for members in [m for m in clusters.values() if len(m) > 1][:limit]:
            group = [rows[i] for i in members]
            keeper = max(group, key=lambda r: (r["importance"], r["uses"], r["id"]))
            others = [r for r in group if r["id"] != keeper["id"]]
            if len({norm[i] for i in members}) == 1:
                new_id, text = keeper["id"], keeper["text"]
            else:
                items = "\n".join(f"- ({r['created'][:10]}) {r['text']}" for r in sorted(group, key=lambda r: r["id"]))
                text = context.llm.complete(MERGE_PROMPT.format(owner=owner, items=items), prefer_smart=True,
                                            temperature=0.1).strip().strip('"').split("\n")[0][:500]
                if len(text) < 8:
                    continue
                s.supersede(keeper["id"], text, keeper["kind"], turn=0)
                with s.lock:
                    new_id = s.db.execute("SELECT superseded_by FROM memories WHERE id=?",
                                          (keeper["id"],)).fetchone()[0]
            with s.lock:
                uses = sum(r["uses"] for r in group)
                imp = max(r["importance"] for r in group)
                s.db.execute("UPDATE memories SET uses=?, importance=? WHERE id=?", (uses, imp, new_id))
                for r in others:
                    s.db.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, r["id"]))
                    s.db.execute("UPDATE OR IGNORE tracking SET item=? WHERE item=?",
                                 (f"memory:{new_id}", f"memory:{r['id']}"))
                s.db.commit()
            merged.append({"into": new_id, "text": text, "from": [r["text"] for r in group]})
        return merged

    # 2. connect
    def connect(self, max_pairs: int = 12) -> list[dict]:
        s = context.store
        with s.lock:
            mems = s.db.execute("SELECT id,kind,text,embedding FROM memories WHERE superseded_by IS NULL "
                                "AND embedding IS NOT NULL").fetchall()
            notes = s.db.execute("SELECT path, text, embedding FROM chunks WHERE idx=0 AND embedding IS NOT NULL").fetchall()
        items = [(f"memory:{m['id']}", m["kind"], m["text"], np.frombuffer(m["embedding"], dtype=np.float32)) for m in mems]
        items += [(f"note:{c['path']}", "note", f"{Path(c['path']).stem}: {c['text'][:300]}",
                   np.frombuffer(c["embedding"], dtype=np.float32)) for c in notes]
        if len(items) < 3:
            return []
        dim = max({len(i[3]) for i in items}, key=lambda d: sum(len(i[3]) == d for i in items))
        items = [i for i in items if len(i[3]) == dim]
        mat = np.stack([i[3] for i in items])
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
        sim = mat @ mat.T
        cands = []
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                if 0.55 <= sim[a, b] < 0.86 and (items[a][1] != items[b][1] or items[a][1] == "note"):
                    cands.append((float(sim[a, b]), a, b))
        cands.sort(reverse=True)
        cands = cands[:max_pairs]
        if not cands:
            return []
        owner = context.cfg.assistant.owner if context.cfg else "the user"
        pairs = "\n".join(f"{k + 1}. A: {items[a][2][:300]}\n   B: {items[b][2][:300]}" for k, (_, a, b) in enumerate(cands))
        raw = context.llm.complete(CONNECT_PROMPT.format(owner=owner, pairs=pairs), prefer_smart=True, temperature=0.4)
        m = re.search(r"\{.*\}", raw or "", re.S)
        out = []
        for c in (json.loads(m.group(0)).get("connections", []) if m else [])[:3]:
            k = int(c.get("pair", 0)) - 1
            if 0 <= k < len(cands) and c.get("insight"):
                _, a, b = cands[k]
                out.append({"a": items[a][0], "a_text": items[a][2], "b": items[b][0], "b_text": items[b][2],
                            "insight": str(c["insight"]).strip()})
        return out

    # 3. journal
    def journal(self, day: dt.date) -> tuple[str, str] | None:
        s = context.store
        start, end = f"{day.isoformat()}T00:00:00", f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00"
        with s.lock:
            turns = s.db.execute("SELECT a.title, a.status, a.session, (SELECT b.detail FROM activity b WHERE "
                                 "b.turn=a.id AND b.kind='reply' LIMIT 1) reply FROM activity a WHERE a.kind='user' "
                                 "AND a.turn=a.id AND a.ts>=? AND a.ts<? AND a.session NOT LIKE 'mission:%' ORDER BY a.id",
                                 (start, end)).fetchall()
            made = s.db.execute("SELECT kind, title FROM activity WHERE kind IN ('memory','artifact','mission','watch') "
                                "AND ts>=? AND ts<? ORDER BY id", (start, end)).fetchall()
            loops = s.db.execute("SELECT item, status, note FROM tracking WHERE status IN ('todo','doing','waiting')"
                                 ).fetchall()
            waiting = s.db.execute("SELECT title FROM activity WHERE kind='tool' AND status='waiting' AND ts>=? AND ts<?",
                                   (start, end)).fetchall()
        if not turns and not made:
            return None
        def title_of(item: str) -> str:
            kind, _, key = item.partition(":")
            table = {"memory": ("memories", "text"), "artifact": ("artifacts", "title"),
                     "turn": ("activity", "title"), "mission": ("missions", "title")}.get(kind)
            if kind == "note":
                return Path(key).stem
            if table and key.isdigit():
                with s.lock:
                    r = s.db.execute(f"SELECT {table[1]} FROM {table[0]} WHERE id=?", (int(key),)).fetchone()
                return r[0] if r else item
            return item
        t = "\n".join(f"- ({r['session']}) {r['title']}" + (f" → {(r['reply'] or '')[:200]}" if r["reply"] else "")
                      + (" [failed]" if r["status"] == "error" else "") for r in turns[:60]) or "(none)"
        mk = "\n".join(f"- {r['kind']}: {r['title']}" for r in made[:60]) or "(none)"
        lp = "\n".join(f"- [{r['status']}] {title_of(r['item'])}" + (f" — {r['note']}" if r["note"] else "")
                       for r in loops[:20])
        lp += "".join(f"\n- waiting for a yes/no: {w['title']}" for w in waiting)
        owner = context.cfg.assistant.owner if context.cfg else "the user"
        text = context.llm.complete(JOURNAL_PROMPT.format(owner=owner, day=f"{day:%A %d %B %Y}", turns=t, made=mk,
                                                          loops=lp or "(none)"), prefer_smart=True, temperature=0.5)
        vault = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
        path = vault / "Journal" / f"{day.isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Journal — {day:%A %d %B %Y}\n\n{text.strip()}\n", encoding="utf-8")
        s.index_note(path)
        return str(path), text.strip().split("\n\n")[0][:400]

    # all together
    def dream(self, day: dt.date | None = None) -> dict:
        if self.running:
            return {"status": "already dreaming"}
        s = self._db()
        c = _cfg()
        self.running = True
        started = now()
        day = day or (started - dt.timedelta(hours=6)).date()       # 02:30 dreams about "yesterday"
        with s.lock:
            did = s.db.execute("INSERT INTO dreams(day, started, status) VALUES(?,?, 'running')",
                               (day.isoformat(), started.isoformat(timespec="seconds"))).lastrowid
            s.db.commit()
        s.log("dream", "dreaming", "🌙 Dreaming…", "", turn=0)
        stats, merged, links, journal, bk, errors = {}, [], [], None, None, []
        steps = [("consolidate", "Tidying memories"), ("connect", "Connecting ideas"), ("people", "Updating people cards"),
                 ("journal", "Writing the journal"), ("backup", "Backing up")]
        people_n, digest = 0, None
        for key, label in steps:
            if not c.get(key, True):
                continue
            self.step = label
            try:
                if key == "consolidate":
                    merged = self.consolidate()
                elif key == "connect":
                    links = self.connect()
                elif key == "people":
                    from . import people as _people
                    people_n = _people.catch_up()
                elif key == "journal":
                    journal = self.journal(day)
                elif key == "backup":
                    bk = backup()
            except Exception as e:
                errors.append(f"{label}: {e}")
                print(f"[dream] {label} failed: {e}")
        if c.get("weekly_digest", True) and started.weekday() == 6:        # Sunday night → the week in review
            self.step = "Writing the weekly digest"
            try:
                from .answers import digest as _digest
                digest = _digest(7)
            except Exception as e:
                errors.append(f"Weekly digest: {e}")
        stats = {"merged": len(merged), "connections": len(links), "journal": bool(journal), "people": people_n,
                 "backup_mb": bk["size_mb"] if bk else None, "errors": errors}
        note = self._report(day, merged, links, journal, bk, errors)
        with s.lock:
            s.db.execute("UPDATE dreams SET finished=?, status=?, stats=?, note=?, backup=? WHERE id=?",
                         (now().isoformat(timespec="seconds"), "done" if not errors else "partly",
                          json.dumps(stats), note, bk["path"] if bk else None, did))
            s.db.commit()
        parts = []
        if merged:
            parts.append(f"merged {len(merged)} duplicate memor{'y' if len(merged) == 1 else 'ies'}")
        if links:
            parts.append(f"found {len(links)} connection{'s' if len(links) > 1 else ''}")
        if people_n:
            parts.append(f"updated {people_n} people card{'s' if people_n > 1 else ''}")
        if journal:
            parts.append("wrote yesterday's journal")
        if bk:
            parts.append(f"backed up ({bk['size_mb']} MB)")
        summary = ("While you slept I " + ", ".join(parts) + ".") if parts else "Quiet night — nothing to tidy."
        if links:
            summary += " 💡 " + links[0]["insight"]
        if errors:
            summary += f" ({len(errors)} step{'s' if len(errors) > 1 else ''} had a problem — see the dream note.)"
        s.log("dream", "dreaming", "🌙 " + summary[:180], summary, f"note:{note}" if note else "", turn=0)
        msg = "🌙 " + summary
        if bk and bk.get("new_passphrase"):
            msg += ("\n\n🔑 Your backups are encrypted with this passphrase — save it in a password manager, you need it"
                    f" to restore:\n{passphrase(create=False)[0]}")
        context.push(msg, [])
        if digest:
            context.push(digest, [])
        self.running, self.step = False, ""
        return {"status": "done", "summary": summary, "note": note, **stats}

    def _report(self, day, merged, links, journal, bk, errors) -> str:
        vault = resolve(context.cfg.brain.vault_dir) if context.cfg else resolve("brain")
        path = vault / "Dreams" / f"{day.isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Dream — night after {day:%A %d %B %Y}", ""]
        if links:
            lines += ["## 💡 Connections", ""]
            for ln in links:
                a = f"[[{Path(ln['a'][5:]).stem}]]" if ln["a"].startswith("note:") else ln["a_text"]
                b = f"[[{Path(ln['b'][5:]).stem}]]" if ln["b"].startswith("note:") else ln["b_text"]
                lines += [f"- **{ln['insight']}**", f"  - {a}", f"  - {b}"]
            lines.append("")
        if merged:
            lines += ["## 🧹 Memories merged", ""]
            for m in merged:
                lines += [f"- {m['text']}"] + [f"  - was: {t}" for t in m["from"]]
            lines.append("")
        if journal:
            lines += ["## 📓 Journal", "", f"[[{Path(journal[0]).stem}]] — {journal[1]}", ""]
        if bk:
            lines += ["## 🔐 Backup", "", f"- {Path(bk['path']).name} · {bk['size_mb']} MB · {bk['files']} files"
                      + (f" · {bk['drive']}" if bk.get("drive") else "")
                      + (f" · Drive failed: {bk['drive_error']}" if bk.get("drive_error") else ""), ""]
        if errors:
            lines += ["## ⚠️ Problems", ""] + [f"- {e}" for e in errors] + [""]
        path.write_text("\n".join(lines), encoding="utf-8")
        try:
            context.store.index_note(path)
        except Exception:
            pass
        return str(path)

    # scheduling
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dreaming")
        self._thread.start()

    def due(self, at: dt.datetime | None = None, started_at: float | None = None) -> bool:
        c = _cfg()
        if not c.get("enabled", True) or self.running:
            return False
        at = at or now()
        hh, mm = map(int, str(c.get("at", "02:30")).split(":"))
        last = self.last()
        last_start = dt.datetime.fromisoformat(last["started"]) if last else None
        scheduled = at.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at < scheduled:
            scheduled -= dt.timedelta(days=1)                 # the most recent dream time
        if last_start and last_start >= scheduled:
            return False                                      # already dreamt since then
        if at - scheduled <= dt.timedelta(hours=2):
            return True                                       # on time
        # catch up: the PC was off at dream time — dream once Nova has been running (and quiet) a while
        warm = started_at is not None and time.time() - started_at > float(c.get("catch_up_after_minutes", 10)) * 60
        return warm and (context.store is None or context.store.status in ("idle", "away"))

    def _loop(self) -> None:
        began = time.time()
        while True:
            try:
                if self.due(started_at=began):
                    self.dream()
            except Exception as e:
                print(f"[dream] {e}")
                self.running = False
            time.sleep(60)


_dreamer: Dreamer | None = None


def dreamer() -> Dreamer:
    global _dreamer
    if _dreamer is None:
        _dreamer = Dreamer()
    return _dreamer


def _cli(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "restore":
        import getpass

        from .config import load_config
        context.cfg = load_config()
        pw = os.environ.get("NOVA_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
        try:
            out = restore(argv[1], pw, apply="--apply" in argv)
        except ValueError as e:
            print(e)
            return 1
        print(f"Restored into {out}" if "--apply" in argv else f"Unpacked into {out} (add --apply to put it back)")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
