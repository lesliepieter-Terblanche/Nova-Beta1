"""v2.35: Nova reviews her own week and drafts playbooks for jobs you keep asking for — used only once approved."""
import datetime as dt
import json

import pytest

from nova import context, evolve, extensions


@pytest.fixture()
def ev(nova, monkeypatch):
    cfg, tmp = nova
    monkeypatch.setattr(evolve, "_state_path", lambda: tmp / "evolve.json")
    monkeypatch.setattr(evolve, "mine_dir", lambda: tmp / "playbooks" / "mine")
    monkeypatch.setattr(evolve, "resolve", lambda p: tmp / p)
    real = extensions.load_playbooks
    monkeypatch.setattr(extensions, "load_playbooks", lambda folder=None: real(tmp / "playbooks"))
    extensions.load_playbooks()
    yield cfg, tmp
    real()


def _turn(text, days_ago=0, tools=(), status="done", reply="Done.", seconds=3, tool_error=False, session="voice"):
    s = context.store
    tid = s.begin_turn(session, text)
    for t in tools:
        aid = s.log("tool", session, t.replace("_", " "), "{}", status="running")
        s.finish(aid, "error" if tool_error else "ok", 100)
    s.log("reply", session, reply[:200], reply, status="error" if status == "error" else "ok")
    s.end_turn(tid, status, int(seconds * 1000))
    when = (dt.datetime.now() - dt.timedelta(days=days_ago, minutes=5)).isoformat(timespec="seconds")
    with s.lock:
        s.db.execute("UPDATE activity SET ts=? WHERE id=? OR turn=?", (when, tid, tid))
        s.db.commit()
    return tid


def _week():
    for d, who in ((1, "Northwind"), (3, "Globex"), (5, "Initech"), (6, "Umbrella")):
        _turn(f"prepare the partner QBR pack for {who}", d, ["recall", "write_file", "gmail_draft"],
              reply=f"The QBR pack for {who} is drafted.")
    _turn("what's the weather", 1, ["get_weather"], reply="Sunny, 24 degrees.")
    _turn("post this reel to instagram", 2, [], reply="I can't post to Instagram yet — no tool for it.")
    _turn("convert the tender pdf to word", 2, ["convert_document"], status="error",
          reply="Sorry, something went wrong: file is locked", tool_error=True)
    _turn("summarise the 90 page tender", 4, ["read_file"], seconds=75, reply="Here is the summary.")
    _turn("Step 2 of 4 — research: find sources", 1, ["web_search"], session="mission:7")        # not a request of yours
    context.store.add_memory("Always quote prices in rands", "preference")


def test_the_week_is_sorted_into_what_went_well_and_what_did_not(ev):
    _week()
    f = evolve.struggles(7)
    assert f["requests"] == 8 and f["ok"] == 6                                   # the mission step isn't counted
    assert [t["text"] for t in f["failed"]] == ["convert the tender pdf to word"]
    assert [t["text"] for t in f["couldnt"]] == ["post this reel to instagram"]
    assert [t["text"] for t in f["slow"]] == ["summarise the 90 page tender"]
    assert f["corrections"] == ["Always quote prices in rands"]
    assert len(f["repeats"]) == 1 and len(f["repeats"][0]["turns"]) == 4
    assert f["repeats"][0]["tools"] == ["recall", "write_file", "gmail_draft"]
    assert {"partner", "qbr", "pack", "prepare"} <= set(f["repeats"][0]["key"])
    text = evolve.facts_text(f)
    assert 'FAILED: "convert the tender pdf to word"' in text and "COULDN'T DO" in text and "ASKED 4 TIMES" in text


def test_self_review_without_a_model_still_says_it_plainly(ev):
    _, tmp = ev
    _week()

    class Down:
        def complete(self, *a, **k):
            raise RuntimeError("all models failed")
    r = evolve.review(7, llm=Down())
    assert "## Went well" in r["text"] and "6 of 8 requests handled without trouble" in r["text"]
    assert '"convert the tender pdf to word" — it failed' in r["text"]
    assert '"post this reel to instagram" — I couldn\'t do it' in r["text"]
    assert "it took 75 seconds" in r["text"] and "Write a playbook for" in r["text"]
    assert (tmp / "workspace" / "evolve").glob("self_review_*.md").__next__().read_text().startswith("# How my week went")


class Writer:
    """A model that answers the review and the playbook draft."""
    def __init__(self, draft):
        self.draft, self.prompts = draft, []

    def complete(self, prompt, system="", prefer_smart=True, temperature=0.5):
        self.prompts.append(prompt)
        if "Reply with JSON only" in prompt:
            return json.dumps(self.draft)
        return "## Went well\n- QBR packs.\n\n## I struggled with\n- Instagram.\n\n## What I'd change\n- A playbook."


GOOD = {"name": "Partner QBR pack", "description": "Prepare a partner's QBR pack and draft the email.",
        "triggers": ["partner qbr pack", "qbr pack for", "send them everything"],
        "steps": ["1. recall what you know about the partner named in the request.", "write_file the pack.",
                  "gmail_draft it to the partner — never send.", "Tell Friend in one sentence what was drafted."]}


def test_a_repeated_job_becomes_a_draft_then_a_playbook_only_after_a_yes(ev):
    _, tmp = ev
    _week()
    llm = Writer(GOOD)
    text = evolve.weekly(llm=llm)
    assert text.startswith("🌱 How I did this week") and "## I struggled with" in text
    assert "📘 Playbooks I've drafted" in text and "“partner-qbr-pack” (asked 4 times" in text
    assert "prepare the partner QBR pack for Northwind" in llm.prompts[1] and "recall, write_file, gmail_draft" in llm.prompts[1]
    [d] = evolve.pending()
    assert d["triggers"] == ["partner qbr pack", "qbr pack for"]                 # the phrase nobody said is dropped
    assert d["groups"] == ["brain", "files", "google"] or set(d["groups"]) >= {"files"}
    assert not (tmp / "playbooks" / "mine").exists()                              # nothing is in use yet
    assert not extensions.matching_playbooks("prepare the partner qbr pack for Acme")
    assert evolve.weekly(llm=llm).count("partner-qbr-pack") == 1                  # not drafted twice
    assert len(evolve.state()["drafts"]) == 1

    from nova.skills.evolve import approve_playbook, playbook_drafts
    from nova.taxonomy import bottlenecks
    assert "Waiting for your yes" in playbook_drafts()
    assert any(b["text"] == "Playbook draft: partner-qbr-pack" for b in bottlenecks())     # on the dashboard too
    assert "ERROR" in approve_playbook("something else entirely")
    out = approve_playbook("qbr")
    assert "is now one of my playbooks" in out and '"partner qbr pack"' in out
    book = (tmp / "playbooks" / "mine" / "partner-qbr-pack.md").read_text()
    assert "written_by: Nova" in book and "1. recall what you know" in book and "\n2. write_file the pack." in book
    [hit] = extensions.matching_playbooks("please prepare the partner QBR pack for Acme")
    assert hit.name == "partner-qbr-pack" and "gmail_draft it to the partner" in hit.body
    assert not evolve.pending() and "Approved and in use: partner-qbr-pack" in playbook_drafts()
    assert not any(b["text"].startswith("Playbook draft") for b in bottlenecks())
    assert evolve.repeated_jobs(30) == []                                         # it has a playbook now


def test_a_dropped_draft_is_never_proposed_again(ev):
    _week()
    from nova.skills.evolve import drop_playbook_draft
    evolve.weekly(llm=Writer(GOOD))
    assert "I won't suggest a playbook for that again" in drop_playbook_draft("")
    assert not evolve.pending() and evolve.repeated_jobs(30) == []
    assert "Playbooks I've drafted" not in evolve.weekly(llm=Writer(GOOD))
    assert "ERROR" in drop_playbook_draft("qbr")


def test_no_model_no_guessing(ev):
    """Without a model the triggers come from the words you really used; a job with no common phrase gets no draft."""
    _week()
    job = evolve.repeated_jobs(30)[0]
    d = evolve.draft_playbook(job, llm=Writer({"triggers": ["make a pack"], "steps": []}))     # a useless answer
    assert d["triggers"] and all(sum(t in x["text"].lower() for x in job["turns"]) >= 3 for t in d["triggers"])
    assert d["steps"][0] == "Use recall — take the details from the request." and len(d["steps"]) == 4
    assert evolve._common_phrases(["book the boardroom", "email the partner", "print the deck"]) == []


def test_once_or_on_one_day_is_not_a_pattern(ev):
    _turn("tidy my downloads folder", 1, ["list_folder", "move_path"])
    _turn("tidy my downloads folder", 1, ["list_folder", "move_path"])
    _turn("tidy my downloads folder", 1, ["list_folder", "move_path"])              # three times, but all in one day
    _turn("what's the time in Lusaka", 2, [])
    _turn("what's the time in Lusaka", 3, [])
    _turn("what's the time in Lusaka", 4, [])                                       # no steps: nothing to write down
    assert evolve.repeated_jobs(30) == []
    assert evolve.weekly(llm=Writer(GOOD)).count("📘") == 0


def test_weekly_review_can_be_switched_off_and_tools_are_offered(ev):
    cfg, _ = ev
    _week()
    cfg["evolve"] = {"enabled": False}
    assert evolve.weekly(llm=Writer(GOOD)) == ""
    cfg["evolve"] = {"draft_playbooks": False}
    assert "📘" not in evolve.weekly(llm=Writer(GOOD)) and not evolve.pending()
    from nova.tools import select_tools
    assert "self_review" in {t.name for t in select_tools("how did you do this week?")}
    assert "approve_playbook" in {t.name for t in select_tools("approve the playbook qbr")}
    from nova.agent import needs_yes
    from nova.tools import REGISTRY
    assert not needs_yes(REGISTRY["drop_playbook_draft"], {})
