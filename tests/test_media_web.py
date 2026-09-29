from pathlib import Path

from PIL import Image, ImageDraw


def test_make_video(nova):
    from nova.skills.media import make_video
    out = make_video("test topic", slides=2, format="vertical")
    assert "Video ready" in out
    assert list(Path(nova[0]["media"]["output_dir"]).glob("*.mp4"))


def test_make_ad(nova):
    _, tmp = nova
    img = Image.new("RGB", (600, 500), (230, 225, 215))
    ImageDraw.Draw(img).rectangle((250, 100, 350, 450), fill=(20, 20, 20))
    photo = tmp / "files" / "flask.jpg"
    img.save(photo)
    from nova.skills.camera_ads import make_ad
    assert "Ads ready" in make_ad(price="R349", brand="Test", image_path=str(photo))


def test_build_website(nova, monkeypatch):
    from nova.skills import web
    monkeypatch.setattr(web.webbrowser, "open", lambda *a, **k: None)
    web.build_website("demo", "a test site")
    html = (Path(nova[0]["web"]["sites_dir"]) / "demo" / "index.html").read_text()
    assert html.startswith("<!doctype html>")
