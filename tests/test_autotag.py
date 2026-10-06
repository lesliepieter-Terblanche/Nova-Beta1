"""v2.32: Nova tags commands and projects herself (in progress / waiting on you / completed) and the dashboard
shows the work in progress as a flow."""
import sqlite3
import time

from nova import autotag, context
from nova.agent import Agent
from nova.dashboard.server import Dashboard
from nova.llm import LLMReply, ToolCall
from nova.store import Store
from nova.tools import REGISTRY


def _projects():
    s = context.store
    s.add_memory("Harbour Homes is a property sales and rental website", "project")
    s.add_memory("Riverbend is a fishing app for anglers", "project")
    return {r["text"].split()[0]: f"memory:{r['id']}" for r in s.db.execute("SELECT id, text FROM memories")}


def _node(d, nid):
    return next(n for n in d.graph()["nodes"] if n["id"] == nid)


def test_command_tags_follow_the_work(nova):
    assert autotag.tag_of_turn("running") == "IN-PROGRESS"
    assert autotag.tag_of_turn("waiting") == "WAITING-ON-USER"
    assert autotag.tag_of_turn("error") == "WAITING-ON-USER"
    assert autotag.tag_of_turn("done") == autotag.tag_of_turn("") == autotag.tag_of_turn("cancelled") == "COMPLETED"


def test_a_project_goes_in_progress_when_nova_works_on_it(nova):
    cfg, tmp = nova
    ids = _projects()
    s, agent = context.store, Agent(cfg, context.llm)
    context.llm.queue = [LLMReply("It's a fishing app.")]
    agent.handle("what is Riverbend again?", "voice")                     # only talking about it
    assert s.get_tracking(ids["Riverbend"])["status"] == ""
    f = tmp / "files" / "plan.txt"
    f.write_text("launch plan")
    context.llm.queue = [LLMReply("", [ToolCall("a", "read_file", {"path": str(f)})]), LLMReply("It says: launch plan.")]
    agent.handle("read the Riverbend plan.txt", "voice")                  # only looking something up
    assert s.get_tracking(ids["Riverbend"])["status"] == ""
    context.llm.queue = [LLMReply("", [ToolCall("b", "write_file", {"path": str(tmp / "files" / "r.txt"), "content": "x"})]),
                         LLMReply("Saved.")]
    agent.handle("write the Riverbend launch notes to r.txt", "voice")    # real work
    t = s.get_tracking(ids["Riverbend"])
    assert t["status"] == "doing" and t["auto"] == 1
    assert s.get_tracking(ids["Harbour"])["status"] == ""
    assert _node(Dashboard(cfg, agent), ids["Riverbend"])["status"] == "IN-PROGRESS"


def test_your_own_status_wins_except_backlog(nova):
    ids = _projects()
    s = context.store
    s.set_tracking(ids["Harbour"], status="done")                         # you said it's finished
    assert not s.auto_status(ids["Harbour"], "doing")
    assert s.get_tracking(ids["Harbour"])["status"] == "done"
    s.set_tracking(ids["Riverbend"], status="todo")                       # backlog = not started yet
    assert s.auto_status(ids["Riverbend"], "doing")
    assert s.get_tracking(ids["Riverbend"]) ["auto"] == 1
    s.set_tracking(ids["Riverbend"], status="waiting")                    # …and a hand-set tag clears "auto"
    assert s.get_tracking(ids["Riverbend"])["auto"] == 0
    assert not s.auto_status(ids["Riverbend"], "doing")


def test_waiting_command_is_red_on_its_project_and_closes_when_answered(nova):
    cfg, tmp = nova
    ids = _projects()
    junk = tmp / "files" / "junk.txt"
    junk.write_text("x")
    s, agent = context.store, Agent(cfg, context.llm)
    d = Dashboard(cfg, agent)
    context.llm.queue = [LLMReply("", [ToolCall("b", "delete_path", {"path": str(junk)})])]
    agent.handle("delete junk.txt from the Harbour Homes folder", "t")
    asked = s.db.execute("SELECT id, status FROM activity WHERE kind='user'").fetchone()
    assert asked["status"] == "waiting"
    g = d.graph()
    by = {n["id"]: n for n in g["nodes"]}
    assert by[f"turn:{asked['id']}"]["status"] == "WAITING-ON-USER"
    assert by[ids["Harbour"]]["status"] == "WAITING-ON-USER" and by[ids["Harbour"]]["live"]
    assert s.get_tracking(ids["Harbour"])["status"] == ""                 # live only — nothing saved yet
    b = [x for x in g["bottlenecks"] if x["id"] == f"turn:{asked['id']}"]
    assert b and b[0]["why"] == "say yes or no" and len(g["bottlenecks"]) == 1
    lane = autotag.flow()["lanes"][0]
    assert lane["tag"] == "WAITING-ON-USER" and lane["project"]["id"] == ids["Harbour"]
    assert [n["state"] for n in lane["nodes"] if n["kind"] == "tool"] == ["waiting"]

    context.llm.queue = [LLMReply("Deleted.")]
    agent.handle("yes", "t")
    d._org_cache = None
    assert s.db.execute("SELECT status FROM activity WHERE id=?", (asked["id"],)).fetchone()["status"] == "done"
    assert _node(d, f"turn:{asked['id']}")["status"] == "COMPLETED"
    assert s.get_tracking(ids["Harbour"])["status"] == "doing"            # she did the work → in progress
    assert not d.graph()["bottlenecks"]


def test_a_dropped_question_is_closed(nova):
    cfg, tmp = nova
    junk = tmp / "files" / "junk.txt"
    junk.write_text("x")
    s, agent = context.store, Agent(cfg, context.llm)
    context.llm.queue = [LLMReply("", [ToolCall("b", "delete_path", {"path": str(junk)})])]
    agent.handle("delete junk.txt", "t")
    context.llm.queue = [LLMReply("Sunny.")]
    agent.handle("what's the weather", "t")                               # neither yes nor no
    rows = [r["status"] for r in s.db.execute("SELECT status FROM activity WHERE kind='user' ORDER BY id")]
    assert rows == ["cancelled", "done"]
    assert s.db.execute("SELECT status FROM activity WHERE kind='tool'").fetchone()["status"] == "dropped"
    assert junk.exists()


def test_failed_and_stale_commands(nova):
    cfg, _ = nova
    s, agent = context.store, Agent(cfg, context.llm)
    context.llm.queue = []                                                # the model falls over
    agent.handle("do the impossible", "voice")
    tid = s.db.execute("SELECT id, status FROM activity WHERE kind='user'").fetchone()
    assert tid["status"] == "error"
    d = Dashboard(cfg, agent)
    g = d.graph()
    assert _node(d, f"turn:{tid['id']}")["status"] == "WAITING-ON-USER"
    assert [b["why"] for b in g["bottlenecks"]] == ["it failed — ask again, or mark it completed"]
    s.db.execute("UPDATE activity SET ts='2020-01-01T00:00:00' WHERE id=?", (tid["id"],))   # a day later: history
    assert not d.graph()["bottlenecks"]
    s.set_tracking(f"turn:{tid['id']}", status="done")                    # or you close it yourself
    assert _node(d, f"turn:{tid['id']}")["status"] == "COMPLETED"

    s.begin_turn("voice", "was busy when the PC restarted")
    s.current_turn = None
    assert s.close_stale() == 1
    assert s.db.execute("SELECT status FROM activity ORDER BY id DESC LIMIT 1").fetchone()["status"] == "error"


def test_the_flow_shows_each_step(nova):
    cfg, tmp = nova
    _projects()
    f = tmp / "files" / "plan.txt"
    f.write_text("Q4 plan")
    agent = Agent(cfg, context.llm)
    assert autotag.flow() == {"busy": False, "lanes": []}
    seen = {}

    def peek(messages):                                                   # while Nova is still working on it
        seen.update(autotag.flow())
        return LLMReply("It's the Q4 plan.")

    context.llm.queue = [LLMReply("", [ToolCall("a", "read_file", {"path": str(f)})]), peek]
    agent.handle("read the Riverbend plan.txt for me", "voice")
    lane = seen["lanes"][0]
    assert seen["busy"] and lane["tag"] == "IN-PROGRESS" and lane["project"]["title"] == "Riverbend"
    assert [(n["kind"], n["state"]) for n in lane["nodes"]] == [("you", "done"), ("think", "done"), ("tool", "done"),
                                                              ("reply", "running")]
    assert lane["nodes"][2]["sub"] == "plan.txt" and "Q4 plan" in lane["nodes"][2]["result"]
    after = autotag.flow()                                                # idle: how the last command went
    assert not after["busy"] and after["lanes"][0]["tag"] == "COMPLETED" and not after["lanes"][0]["live"]
    assert after["lanes"][0]["nodes"][-1]["sub"] == "It's the Q4 plan."


def test_status_by_voice(nova):
    ids = _projects()
    s = context.store
    assert "set_status" in REGISTRY and "work_status" in REGISTRY
    assert "completed" in REGISTRY["set_status"].run({"item": "the Harbour Homes project", "status": "completed"})
    t = s.get_tracking(ids["Harbour"])
    assert t["status"] == "done" and t["auto"] == 0                       # you said so: Nova won't change it
    assert REGISTRY["set_status"].run({"item": "Harbour Homes", "status": "someday maybe"}).startswith("ERROR")
    assert "couldn't find" in REGISTRY["set_status"].run({"item": "Zeppelin", "status": "done"})
    REGISTRY["set_status"].run({"item": "riverbend", "status": "in progress"})
    out = REGISTRY["work_status"].run({})
    assert "In progress: Riverbend" in out and "Nothing is waiting on you" in out and "1 completed" in out


def test_older_databases_get_the_auto_column(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE tracking(item TEXT PRIMARY KEY, status TEXT DEFAULT '', pinned INTEGER DEFAULT 0, "
               "note TEXT DEFAULT '', updated TEXT)")
    db.execute("INSERT INTO tracking(item, status) VALUES('memory:1', 'doing')")
    db.commit()
    db.close()
    s = Store(tmp_path / "old.db")
    assert s.get_tracking("memory:1")["auto"] == 0
    assert not s.auto_status("memory:1", "waiting")                       # set before the upgrade = yours
    time.sleep(0)


def test_a_command_is_never_left_on_none(nova):
    """v2.32.1: opening a request showed "None" in Track this — Nova's own tag is shown instead."""
    cfg, _ = nova
    s, agent = context.store, Agent(cfg, context.llm)
    d = Dashboard(cfg, agent)
    context.llm.queue = [LLMReply("Four.")]
    agent.handle("what is two plus two?", "voice")
    tid = s.db.execute("SELECT MAX(id) i FROM activity WHERE kind='user'").fetchone()["i"]
    t = d.turn(tid)["tracking"]
    assert t["status"] == "done" and t["auto"] == 1                        # completed, tagged by Nova
    assert s.get_tracking(f"turn:{tid}")["status"] == ""                    # nothing saved on your behalf
    n = _node(d, f"turn:{tid}")
    assert n["tag"] == "done" and n["status"] == "COMPLETED" and n["fresh"]
    row = next(i for i in d.topic("action")["items"] if i["id"] == f"turn:{tid}")
    assert row["tag"] == "done" and row["auto"] and row["track"] == ""
    assert autotag.turn_tracking(tid, "running")["status"] == "doing"
    assert autotag.turn_tracking(tid, "waiting")["status"] == autotag.turn_tracking(tid, "error")["status"] == "waiting"
    s.set_tracking(f"turn:{tid}", status="todo")                           # your own tag wins
    t = d.turn(tid)["tracking"]
    assert t["status"] == "todo" and not t["auto"]
    assert _node(d, f"turn:{tid}")["tag"] == "todo"
