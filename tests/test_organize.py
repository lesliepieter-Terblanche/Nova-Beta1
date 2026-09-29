"""Sorting the 2nd brain into projects."""
import numpy as np

from nova import context
from nova.dashboard.organize import keywords, organize, palette, short_title

OWNER = "Pieter Terblanche"
P = [
    {"id": "memory:1", "text": "Nova is Pieter's local-first voice agent project with a 3D dashboard", "created": "2026-09-01"},
    {"id": "memory:2", "text": "TrueHome (truehome.co.za) is a South African property platform", "created": "2026-09-02"},
    {"id": "memory:3", "text": "Juniper Q4 partner enablement plan for SADC resellers", "created": "2026-09-03"},
    {"id": "memory:4", "text": "Nova upgrade: Gesture control — steer Nova with hand gestures", "created": "2026-09-29",
     "source": "roadmap:gesture-control"},
    {"id": "memory:5", "text": "Avaya growth plan: add 3 new resellers in Botswana", "created": "2026-09-04"},
]


def test_keywords_and_titles():
    assert keywords(P[0]["text"], OWNER) == ["nova"]
    assert keywords(P[1]["text"], OWNER) == ["truehome"]
    assert keywords("Service the XUV500 at Mahindra Roodepoort", OWNER)[:2] == ["xuv500", "mahindra"]
    assert short_title(P[1]["text"]) == "TrueHome"
    assert short_title(P[3]["text"]) == "Nova upgrade: Gesture control"
    assert len(set(palette(12))) == 12


def test_items_go_to_the_right_project_and_subprojects_nest():
    items = [
        {"id": "memory:10", "text": "Pieter wants TrueHome listings to beat Property24 on price"},
        {"id": "memory:11", "text": "Nova should use ElevenLabs for its voice"},
        {"id": "note:/v/Botswana resellers.md", "text": "Botswana resellers: Gaborone partners for Avaya"},
        {"id": "artifact:3", "text": "Juniper QBR deck"},
        {"id": "memory:12", "text": "Pieter lives in Roodepoort"},
    ]
    r = organize(P, items, owner=OWNER)
    assert r["item_project"] == {"memory:10": "memory:2", "memory:11": "memory:1",
                                 "note:/v/Botswana resellers.md": "memory:5", "artifact:3": "memory:3"}
    assert r["parents"] == {"memory:4": "memory:1"}
    assert r["why"]["memory:10"] == "name 'truehome'"


def test_meaning_links_without_names_and_manual_wins():
    projects = [{"id": "memory:1", "text": "Nova voice agent", "vec": np.array([1, 0, 0], dtype=np.float32)},
                {"id": "memory:2", "text": "TrueHome property site", "vec": np.array([0, 1, 0], dtype=np.float32)}]
    items = [{"id": "memory:9", "text": "rental listings need photos", "vec": np.array([0.1, 0.95, 0], dtype=np.float32)},
             {"id": "memory:8", "text": "Nova wake word", "vec": None},
             {"id": "memory:7", "text": "unrelated", "vec": np.array([0, 0, 1], dtype=np.float32)}]
    r = organize(projects, items, manual={"memory:8": "memory:2", "memory:7": ""})
    assert r["item_project"]["memory:9"] == "memory:2" and r["why"]["memory:9"].startswith("meaning")
    assert r["item_project"]["memory:8"] == "memory:2" and r["why"]["memory:8"] == "you linked it"
    assert "memory:7" not in r["item_project"] and r["why"]["memory:7"] == "you removed it"


def test_dashboard_graph_clusters_and_manual_link(nova):
    from nova.dashboard.server import Dashboard
    nova[0]["assistant"]["owner"] = "Pieter"
    s = context.store
    for text, kind in [("Nova is Pieter's voice agent project", "project"),
                       ("TrueHome is a property platform", "project"),
                       ("TrueHome needs a rental calculator", "goal"),
                       ("Pieter lives in Roodepoort", "fact")]:
        s.add_memory(text, kind)
    ids = {r["text"]: r["id"] for r in s.db.execute("SELECT id, text FROM memories")}
    d = Dashboard(nova[0], None)
    g = d.graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    th, calc, home = (f"memory:{ids[t]}" for t in ("TrueHome is a property platform", "TrueHome needs a rental calculator",
                                                   "Pieter lives in Roodepoort"))
    nova_p = f"memory:{ids['Nova is Pieter' + chr(39) + 's voice agent project']}"
    assert nodes[th]["isProject"] and nodes[th]["short"] == "TrueHome" and nodes[th]["pcolor"].startswith("#")
    assert nodes[calc]["project"] == th and "project" not in nodes[home]
    assert any(ln["type"] == "project" and ln["source"] == th and ln["target"] == calc for ln in g["links"])
    item = d.item(calc)
    assert item["project"]["id"] == th and item["project"]["why"] == "name 'truehome'"
    assert {p["id"] for p in item["projects"]} == {th, nova_p}
    proj = d.item(th)
    assert [x["id"] for x in proj["linked"]["Memories"]] == [calc]

    # you move it to Nova by hand — it stays there
    assert d.set_link(calc, nova_p)["project"] == nova_p
    assert {n["id"]: n for n in d.graph()["nodes"]}[calc]["project"] == nova_p
    assert d.set_link(calc, "")["project"] == ""                      # no project
    assert d.set_link(calc, None)["project"] == th                    # back to automatic
    assert d.item(calc)["project"]["why"] == "name 'truehome'"


def test_layout_setting_defaults_to_projects(nova):
    from nova import settings
    assert settings.theme()["layout"] in ("projects", "categories", "web")
