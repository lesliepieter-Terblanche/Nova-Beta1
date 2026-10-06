"""v2.38.1: Nova times her own start-up and says what was slow."""
from nova import startup


def test_startup_report_names_the_slow_stages(tmp_path, monkeypatch):
    assert startup.report([]) == "I have no start-up timings yet — they are kept from the next start."
    rows = [{"stage": "program loaded", "seconds": 3.2, "at": 3.2}, {"stage": "memory opened", "seconds": 0.4, "at": 3.6},
            {"stage": "skills loaded", "seconds": 1.1, "at": 6.0}, {"stage": "phone link (Tailscale)", "seconds": 15.0, "at": 22.0},
            {"stage": "AI model loaded", "seconds": 31.0, "at": 37.0}, {"stage": "listening for the wake word", "seconds": 9.0, "at": 33.0}]
    said = startup.report(rows)
    assert said.startswith("Start-up: the window was up in 22 s; everything (AI model and voice included) was ready after 37 s.")
    assert "Slowest: AI model loaded 31 s; phone link (Tailscale) 15 s; listening for the wake word 9 s; program loaded 3 s." in said
    assert "Why: the local AI model is loaded from disk into the graphics card" in said
    monkeypatch.setattr(startup, "_marks", [])
    monkeypatch.setattr(startup, "_last", {"main": startup.T0})
    startup.mark("", "voice")                                  # only starts the clock
    startup.mark("memory opened")
    startup.mark("listening for the wake word", "voice")
    assert [r["stage"] for r in startup.stages()] == ["memory opened", "listening for the wake word"]
    startup.save(tmp_path / "data" / "startup.json")
    assert [r["stage"] for r in startup.last(tmp_path / "data" / "startup.json")] == ["memory opened", "listening for the wake word"]
    assert startup.last(tmp_path / "none.json") == []
