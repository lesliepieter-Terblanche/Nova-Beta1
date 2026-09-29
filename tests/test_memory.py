from nova import context
from nova.skills.memory import correct_memory, recall, remember, write_note
from nova.store import learn_from_turn


def test_remember_dedupe_and_recall(nova):
    assert "Remembered" in remember("Revenue target is R27 million", "goal", True)
    assert "Already knew" in remember("Revenue target is R27 million", "goal")
    assert "R27 million" in recall("revenue target")


def test_corrections_keep_history(nova):
    remember("Thandi works at Axiz", "person")
    correct_memory(1, "Thandi works at Datacentrix")
    rows = context.store.db.execute("SELECT id, text, superseded_by FROM memories ORDER BY id").fetchall()
    assert rows[0]["superseded_by"] == 2 and "Datacentrix" in rows[1]["text"]


def test_notes_are_indexed(nova):
    write_note("Launch plan", "Facebook ads and SEO for the property site", "Projects")
    hits = context.store.search_notes("property site SEO")
    assert hits and "Launch plan" in hits[0][1]["path"]


def test_learning_supersedes(nova):
    remember("Thandi is the contact at Axiz", "person")

    class L:
        def complete(self, prompt, **kw):
            return '{"memories":[{"kind":"person","text":"Thandi moved to Datacentrix","importance":2,"replaces":1}]}'
    learn_from_turn(context.store, L(), "Pieter", "Thandi from Axiz moved to Datacentrix", "Noted.", [])
    live = [r["text"] for r in context.store.db.execute("SELECT text FROM memories WHERE superseded_by IS NULL")]
    assert live == ["Thandi moved to Datacentrix"]
