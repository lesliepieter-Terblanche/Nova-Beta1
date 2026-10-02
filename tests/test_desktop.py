"""PC hands: the step-by-step 'do it for me' loop, safety stops and app opening (desktop faked — no Windows here)."""
import json

from nova.skills import desktop, system


class FakeDesk:
    def __init__(self):
        self.log = []
        self.screen = {"title": "Outlook", "controls": [
            {"id": 1, "type": "Button", "name": "New Email"}, {"id": 2, "type": "Edit", "name": "To"},
            {"id": 3, "type": "Edit", "name": "Subject"}, {"id": 4, "type": "Button", "name": "Send"},
            {"id": 5, "type": "Button", "name": "Attach File"}]}

    def windows(self):
        return [{"title": "Outlook", "active": True}, {"title": "Nova — Second Brain", "active": False}]

    def read(self, title=""):
        return self.screen["title"], self.screen["controls"]

    def click(self, cid, double=False):
        name = next(c["name"] for c in self.screen["controls"] if c["id"] == cid)
        self.log.append(("click", name))
        return name

    def type(self, text, cid=None):
        self.log.append(("type", text, cid))

    def keys(self, combo):
        self.log.append(("keys", combo))

    def scroll(self, direction="down", amount=5):
        self.log.append(("scroll", direction))

    def window_action(self, title, action):
        self.log.append(("window", title, action))
        return title

    def click_xy(self, x, y, double=False):
        self.log.append(("xy", x, y))

    def screenshot(self):
        return "shot.png", 1920, 1080

    def cursor(self):
        return (500, 500)


class ScriptLLM:
    def __init__(self, actions):
        self.actions = list(actions)
        self.prompts = []

    def complete(self, prompt, **kw):
        self.prompts.append(prompt)
        return json.dumps(self.actions.pop(0))

    def see(self, path, q):
        return "An email window with a Send button."


def test_task_runs_step_by_step(nova):
    d = FakeDesk()
    llm = ScriptLLM([{"do": "click", "id": 1}, {"do": "type", "text": "sam@northwind.co.za", "id": 2},
                     {"do": "type", "text": "Mist pricing", "id": 3}, {"do": "keys", "keys": "ctrl+s"},
                     {"do": "done", "summary": "Drafted the pricing email to Sam and saved it."}])
    out = desktop.run_task("draft an email to Sam about Mist pricing", d=d, llm=llm)
    assert out == "Done: Drafted the pricing email to Sam and saved it."
    assert d.log == [("click", "New Email"), ("type", "sam@northwind.co.za", 2), ("type", "Mist pricing", 3), ("keys", "ctrl+s")]
    assert "5. Button 'Attach File'" in llm.prompts[0] and "Clicked 'New Email'" in llm.prompts[1]


def test_stops_before_risky_clicks_unless_allowed(nova):
    d = FakeDesk()
    out = desktop.run_task("send the mail", d=d, llm=ScriptLLM([{"do": "click", "id": 4}]))
    assert "stopped before clicking 'Send'" in out and d.log == []
    out = desktop.run_task("send the mail", allow="send", d=d,
                           llm=ScriptLLM([{"do": "click", "id": 4}, {"do": "done", "summary": "Sent."}]))
    assert out == "Done: Sent." and d.log == [("click", "Send")]
    out = desktop.run_task("send it", d=FakeDesk(), llm=ScriptLLM([{"do": "keys", "keys": "ctrl + enter"}]))
    assert "send" in out and "stopped" in out                       # Outlook's send shortcut counts too


def test_stop_and_corner_and_limits(nova):
    d = FakeDesk()
    desktop._stop.set()
    orig = desktop._stop.clear
    desktop._stop.clear = lambda: None                              # simulate Esc pressed right away
    try:
        assert desktop.run_task("anything", d=d, llm=ScriptLLM([])).startswith("Stopped — you took over")
    finally:
        desktop._stop.clear = orig
        desktop._stop.clear()
    d.cursor = lambda: (0, 0)
    assert desktop.run_task("anything", d=d, llm=ScriptLLM([])).startswith("Stopped — mouse thrown to the corner")
    d2 = FakeDesk()
    out = desktop.run_task("loop", d=d2, llm=ScriptLLM([{"do": "scroll", "direction": "down"}] * 3), max_steps=3)
    assert out.startswith("I ran out of steps") and len(d2.log) == 3
    assert "I need you" in desktop.run_task("x", d=FakeDesk(), llm=ScriptLLM([{"do": "ask", "question": "Which file?"}]))


def test_look_feeds_back(nova):
    llm = ScriptLLM([{"do": "look", "question": "Is there a Send button?"}, {"do": "done", "summary": "Checked."}])
    desktop.run_task("check", d=FakeDesk(), llm=llm)
    assert "What you saw last: An email window" in llm.prompts[1]


def test_open_app_names_and_start_menu(nova, tmp_path, monkeypatch):
    assert system.APPS["webcam"] == "microsoft.windows.camera:" and system.APPS["downloads"] == "shell:Downloads"
    sm = tmp_path / "Microsoft/Windows/Start Menu/Programs/Avaya"
    sm.mkdir(parents=True)
    (sm / "Avaya Workplace.lnk").write_text("x")
    (sm / "Uninstall Avaya Workplace.lnk").write_text("x")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "none"))
    assert system._start_menu_app("avaya workplace").endswith("Avaya Workplace.lnk")
    assert system._start_menu_app("nonexistent thing") is None


def test_tools_selected_and_windows_only(nova):
    from nova.tools import REGISTRY, select_tools
    names = {t.name for t in select_tools("open outlook and click new email for me")}
    assert {"do_on_pc", "read_window", "click_control"} <= names
    if not desktop.WINDOWS:
        assert REGISTRY["list_windows"].run({}).startswith("ERROR: PC hands only work on Windows")
