"""Nightly dreaming: consolidation, connections, journal, encrypted backup/restore and scheduling."""
import datetime as dt
import json

import numpy as np
import pytest

from nova import context
from nova.dreaming import Dreamer, backup, decrypt, encrypt, restore


def _vec(*xs):
    v = np.zeros(8, dtype=np.float32)
    for i, x in enumerate(xs):
        v[i] = x
    return v


def _mem(text, vec=None, kind="fact", importance=2, uses=0, created="2026-09-01T10:00:00"):
    s = context.store
    with s.lock:
        mid = s.db.execute("INSERT INTO memories(kind,text,created,updated,source,importance,uses,embedding) "
                           "VALUES(?,?,?,?,?,?,?,?)", (kind, text, created, created, "test", importance, uses,
                                                        None if vec is None else vec.tobytes())).lastrowid
        s.db.commit()
    return mid


@pytest.fixture()
def llm(nova, monkeypatch):
    calls = []

    def complete(prompt, system="", prefer_smart=True, temperature=0.5):
        calls.append(prompt)
        if "Merge them into ONE" in prompt:
            return "Pieter's revenue target is R27 million at 17% GP across all vendors."
        if "reviewing" in prompt and "second brain" in prompt:
            return json.dumps({"connections": [{"pair": 1, "insight": "The Botswana reseller list fits the Avaya "
                                                                       "growth plan — start there."}]})
        if "journal entry" in prompt:
            return "A busy day of QBR prep.\n\n## Done\n- Built the Avaya deck"
        return '{"memories": []}'
    monkeypatch.setattr(context.llm, "complete", complete)
    pushed = []
    monkeypatch.setattr(context, "push", lambda text, files=None: pushed.append(text))
    return calls, pushed


def test_consolidate_merges_duplicates_and_keeps_history(nova, llm):
    s = context.store
    a = _mem("Pieter's revenue target is R27m", _vec(1, 0.02), importance=3, uses=4)
    b = _mem("Revenue target: R27 million, 17% GP", _vec(1, 0.05), uses=2, created="2026-09-20T10:00:00")
    c = _mem("Pieter lives in Roodepoort", _vec(0, 1))
    d1 = _mem("Anri is Pieter's wife")
    d2 = _mem("anri is pieter's wife!")                       # identical apart from case/punctuation
    s.set_tracking(f"memory:{b}", status="doing", note="check Q4")
    merged = Dreamer().consolidate()
    assert len(merged) == 2
    live = {r["text"]: dict(r) for r in s.db.execute("SELECT * FROM memories WHERE superseded_by IS NULL")}
    assert "Pieter's revenue target is R27 million at 17% GP across all vendors." in live
    assert "Pieter lives in Roodepoort" in live and len(live) == 3
    new = live["Pieter's revenue target is R27 million at 17% GP across all vendors."]
    assert new["importance"] == 3 and new["uses"] == 6
    assert s.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 6       # nothing deleted
    assert s.get_tracking(f"memory:{new['id']}")["note"] == "check Q4"
    assert {a, b} <= {r[0] for r in s.db.execute("SELECT id FROM memories WHERE superseded_by=?", (new["id"],))}
    assert c and d1 and d2


def test_connections_between_memory_and_note(nova, llm):
    s = context.store
    _mem("Avaya growth plan: add 3 new resellers in Botswana", _vec(1, 1, 0), kind="goal")
    _mem("Pieter lives in Roodepoort", _vec(0, 0, 1))
    note = context.store.vault / "Botswana resellers.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("Potential resellers in Gaborone and Francistown")
    with s.lock:
        s.db.execute("INSERT INTO chunks(path,idx,text,mtime,embedding) VALUES(?,?,?,?,?)",
                     (str(note), 0, note.read_text(), 0, _vec(1, 0.1, 0.6).tobytes()))
        s.db.commit()
    links = Dreamer().connect()
    assert len(links) == 1 and "Botswana" in links[0]["insight"]
    assert {links[0]["a"].split(":")[0], links[0]["b"].split(":")[0]} == {"memory", "note"}


def test_journal_of_the_day(nova, llm):
    s = context.store
    t = s.begin_turn("voice", "Build the Avaya QBR deck")
    s.log("reply", "voice", "Done, deck saved", "Done, deck saved")
    s.end_turn(t, "done", 900)
    today = dt.date.today()
    path, summary = Dreamer().journal(today)
    text = open(path, encoding="utf-8").read()
    assert text.split("\n\n", 1)[1].startswith(f"# Journal — {today:%A}") and "Avaya deck" in text
    assert "Build the Avaya QBR deck" in llm[0][-1]
    assert Dreamer().journal(today - dt.timedelta(days=30)) is None          # quiet day -> no journal


def test_encrypt_roundtrip_and_wrong_passphrase():
    blob = encrypt(b"secret brain", "correct horse")
    assert blob.startswith(b"NOVABK1") and b"secret brain" not in blob
    assert decrypt(blob, "correct horse") == b"secret brain"
    with pytest.raises(ValueError, match="Wrong passphrase"):
        decrypt(blob, "wrong")
    with pytest.raises(ValueError, match="isn't a Nova backup"):
        decrypt(b"hello", "x")


def test_backup_creates_passphrase_keeps_last_n_and_restores(nova, llm, monkeypatch, tmp_path):
    from nova import settings
    env = {}
    monkeypatch.setattr(settings, "read_env", lambda: dict(env))
    monkeypatch.setattr(settings, "write_env", lambda upd: env.update({k: v for k, v in upd.items() if v}))
    monkeypatch.delenv("NOVA_BACKUP_PASSPHRASE", raising=False)
    _mem("Pieter's favourite braai spot is Hartbeespoort")
    note = context.store.vault / "Ideas.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("Nova should dream")
    dest = tmp_path / "backups"
    first = backup(dest, keep=2)
    assert first["new_passphrase"] and env["NOVA_BACKUP_PASSPHRASE"]
    import time
    for _ in range(2):
        time.sleep(1.05)
        info = backup(dest, keep=2)
        assert not info["new_passphrase"]
    assert len(list(dest.glob("nova-backup-*.nova"))) == 2
    out = restore(info["path"], env["NOVA_BACKUP_PASSPHRASE"], target=tmp_path / "r")
    assert (out / "brain" / "Ideas.md").read_text() == "Nova should dream"
    import sqlite3
    db = sqlite3.connect(out / "data" / "nova.db")
    assert "Hartbeespoort" in db.execute("SELECT text FROM memories").fetchone()[0]
    with pytest.raises(ValueError):
        restore(info["path"], "nope", target=tmp_path / "r2")


def test_full_dream_report_and_schedule(nova, llm, monkeypatch, tmp_path):
    from nova import dreaming
    calls, pushed = llm
    nova[0]["dreaming"] = {"backup": False, "at": "02:30"}
    _mem("Target R27m", _vec(1, 0.01))
    _mem("Target is R27 million", _vec(1, 0.02))
    d = Dreamer()
    out = d.dream(dt.date.today())
    assert out["status"] == "done" and out["merged"] == 1
    note = open(out["note"], encoding="utf-8").read()
    assert note.startswith("[LABEL: DOMAIN: PERSONAL]\n[LABEL: STATUS: COMPLETED]\n\n# Dream") and "Memories merged" in note
    assert pushed[-1].startswith("🌙 While you slept I merged 1 duplicate memory")
    assert d.last()["stats"]["merged"] == 1

    # scheduling: due after 02:30 when not yet dreamt tonight
    night = dt.datetime.now().replace(hour=3, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    assert d.due(at=night)
    assert not d.due(at=night.replace(hour=1))
    # catch-up when overdue and Nova has been running a while (and it's quiet)
    monkeypatch.setattr(dreaming, "_cfg", lambda: {"at": "02:30", "catch_up_after_minutes": 10})
    later = (dt.datetime.now() + dt.timedelta(days=2)).replace(hour=9)
    assert not d.due(at=later, started_at=__import__("time").time())            # just started
    assert d.due(at=later, started_at=__import__("time").time() - 900)


def test_dream_tools(nova, llm, monkeypatch):
    from nova.tools import REGISTRY, select_tools
    names = {t.name for t in select_tools("back up everything and tell me what you did last night while i slept")}
    assert {"backup_now", "last_dream"} <= names
    assert "haven't dreamt" in REGISTRY["last_dream"].run({})
