"""v2.33: cinematic mode — looks, 3D photo shots, brand kits, highlighted captions, cuts on the beat."""
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from nova import cinema, context, ffmpeg

SMALL = {"landscape": (640, 360), "vertical": (360, 640), "square": (360, 360)}


@pytest.fixture()
def cine(nova, monkeypatch):
    cfg, tmp = nova
    cfg["media"]["brands_file"] = str(tmp / "brands.json")
    monkeypatch.setattr(cinema, "SIZES", SMALL)
    monkeypatch.setattr(cinema, "depth_model", lambda: None)        # no 66 MB download in tests
    return cfg, tmp


def _photo(path, colour):
    img = Image.new("RGB", (800, 600), colour)
    ImageDraw.Draw(img).rectangle((300, 150, 500, 600), fill=(30, 30, 30))
    img.save(path)
    return path


def _click_track(path, bpm=120, seconds=12):
    rate = 22050
    x = np.zeros(rate * seconds, np.float32)
    for k in np.arange(0.25, seconds - 0.5, 60 / bpm):
        i, n = int(k * rate), int(0.08 * rate)
        x[i:i + n] += np.sin(2 * np.pi * 90 * np.arange(n) / rate) * np.exp(-np.arange(n) / rate * 40)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x.clip(-1, 1) * 28000).astype(np.int16).tobytes())
    return path


def test_looks_and_finish_filters():
    assert cinema.look_name("Teal and Orange") == "teal_orange"
    assert cinema.look_name("something moody please") == "moody"
    assert cinema.look_name("") == "teal_orange" and cinema.look_name("nonsense") == "teal_orange"
    wide, tall = cinema.finish_filter((1920, 1080)), cinema.finish_filter((1080, 1920), grain=False)
    assert "drawbox" in wide and "noise" in wide and "h=138" in wide          # 2.39:1 bars on a 16:9 frame
    assert "noise" not in tall and "vignette" in tall
    assert cinema.finish_filter((1080, 1920), bars=False, grain=False, vignette=False) == "null"


def test_brand_kits_are_saved_and_found(cine):
    assert cinema.brand("") is None
    kit = cinema.save_brand("Harbour Homes", handle="@harbourhomes", colour="navy", accent="#E8B84B")
    assert kit["colour"] == "#101a3a" and kit["accent"] == "#e8b84b"
    cinema.save_brand("Riverbend", accent="teal")
    assert cinema.brand("")["name"] == "Harbour Homes"                        # the first kit is the default
    assert cinema.brand("riverbend")["accent"] == "#00a0a0"
    assert cinema.brand("harbour")["handle"] == "@harbourhomes"
    assert cinema.brand("none") is None and cinema.brand("no such brand") is None
    cinema.save_brand("Harbour Homes", tagline="Find your place")             # only what you give changes
    assert cinema.brand("harbour homes")["handle"] == "@harbourhomes"
    assert cinema.rgb_of("#fff") == (255, 255, 255) and cinema.rgb_of("rubbish", "#000000") == (0, 0, 0)


def test_cuts_land_on_the_beat(tmp_path):
    grid = cinema.beat_grid(_click_track(tmp_path / "click.wav", bpm=120))
    assert grid and abs(60 / grid[0] - 120) < 1.5 and abs(grid[1] - 0.25) < 0.06
    lengths = cinema.cut_lengths(4, 3.0, grid, first_cut_after=2.0)
    t = 2.0
    for seen in lengths:
        t += seen
        assert abs(((t - grid[1]) / grid[0]) - round((t - grid[1]) / grid[0])) < 0.02     # on a beat
    assert cinema.cut_lengths(3, 2.0, None) == [2.0, 2.0, 2.0]
    assert sum(cinema.cut_lengths(2, 2.0, None, min_total=9)) >= 9                           # room for the voice
    noise = tmp_path / "noise.wav"
    with wave.open(str(noise), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes((np.random.default_rng(1).standard_normal(22050 * 10) * 3000).astype(np.int16).tobytes())
    assert cinema.beat_grid(noise) is None


def test_caption_words_and_groups():
    words = cinema.spread_words("Wake up to the ocean. Every morning.", 4.0)
    assert len(words) == 7 and words[0][0] > 0 and words[-1][1] <= 4.0
    groups = cinema.word_groups(words)
    assert [len(g) for g in groups] == [3, 2, 2]                               # three words, broken at the full stop
    sprite = cinema._caption_sprite(["WAKE", "UP", "TO"], 1, (360, 640), (255, 200, 0))
    rgb, alpha, x, y = sprite
    assert alpha.max() == 1.0 and (rgb[..., 2] < 60).any() and y > 300          # an accent-coloured word, low down


def test_photo_becomes_a_moving_shot(cine):
    pytest.importorskip("cv2")
    _, tmp = cine
    out = tmp / "shot.mp4"
    info = cinema.photo_shot(_photo(tmp / "p.jpg", (120, 170, 230)), out, (360, 640), 1.0, "orbit_right", "moody")
    assert info == {"depth": False, "move": "orbit_right", "seconds": 1.0}
    p = cinema.probe(out)
    assert (p["w"], p["h"]) == (360, 640) and abs(p["duration"] - 1.0) < 0.1
    frames = list(cinema.frames(out, (360, 640)))
    assert len(frames) == 30 and np.abs(frames[0].astype(int) - frames[-1].astype(int)).mean() > 1    # it moves


def test_cinematic_reel_with_brand_voice_and_music(cine):
    pytest.importorskip("cv2")
    _, tmp = cine
    from nova.skills.cinematic import brand_kits, cinematic_reel, save_brand_kit
    folder = tmp / "files" / "shoot"
    folder.mkdir()
    _photo(folder / "a.jpg", (200, 120, 90))
    _photo(folder / "b.jpg", (90, 140, 200))
    logo = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse((20, 20, 180, 180), fill=(232, 184, 75, 255))
    logo.save(tmp / "files" / "logo.png")
    music = _click_track(tmp / "files" / "music.wav", seconds=20)
    assert "(none saved yet)" in brand_kits()
    out = cinematic_reel(str(folder), title="Sea view flat")
    assert "Cinematic reel ready" in out and "No brand kit is saved yet" in out
    assert "default kit" in save_brand_kit("Harbour Homes", handle="@harbourhomes", colour="navy", accent="gold",
                                           logo_path=str(tmp / "files" / "logo.png"), call_to_action="Book a viewing")
    assert "Harbour Homes (default)" in brand_kits()
    out = cinematic_reel(str(folder), title="Sea view flat", subtitle="2 bed", narration="Wake up to the ocean.",
                         music_path=str(music), look="warm film")
    assert "Harbour Homes branding" in out and "cuts on the beat" in out and "highlighted captions" in out
    assert "Warm film look" in out
    video = max(Path(cine[0]["media"]["output_dir"]).glob("Sea_view_flat_*.mp4"), key=lambda f: f.stat().st_mtime)
    info = cinema.probe(video)
    assert (info["w"], info["h"]) == (360, 640) and info["audio"] and info["duration"] > 8     # intro + 2 shots + outro
    assert video.with_suffix(".jpg").exists()
    assert not list(Path(cine[0]["media"]["output_dir"]).glob("reel_*"))                        # work folder cleaned up
    assert "ERROR" in cinematic_reel(str(tmp / "files" / "nothing-here"))


def test_cinematic_look_on_a_video(cine):
    _, tmp = cine
    from nova.skills.cinematic import cinematic_look
    src = tmp / "files" / "clip.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=1", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=1", "-pix_fmt", "yuv420p", "-shortest", src])
    out = cinematic_look(str(src), look="noir", slow_motion=0.5)
    assert "Noir look applied" in out
    video = next(Path(cine[0]["media"]["output_dir"]).glob("clip_noir_*.mp4"))
    info = cinema.probe(video)
    assert abs(info["duration"] - 2.0) < 0.3 and info["audio"]                 # half speed, sound kept
    frame = next(cinema.frames(video, (640, 360)))
    assert frame[:40].max() < 30                                               # the widescreen bar
    mid = frame[150:210, 200:440].astype(int)
    assert np.abs(mid[..., 0] - mid[..., 2]).mean() < 12                       # black and white
    assert "ERROR" in cinematic_look(str(tmp / "files" / "missing.mp4"))


def test_captions_light_up_the_spoken_word(cine):
    _, tmp = cine
    src, out = tmp / "talk.mp4", tmp / "talk_caps.mp4"
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=0x202020:size=360x640:rate=30:duration=2", "-f", "lavfi", "-i",
                "sine=frequency=300:duration=2", "-pix_fmt", "yuv420p", "-shortest", src])
    n = cinema.burn_captions(src, out, [(0.1, 0.6, "hello"), (0.6, 1.2, "there"), (1.2, 1.8, "world")],
                             accent=(255, 200, 0))
    assert n == 1 and cinema.probe(out)["audio"]
    frames = list(cinema.frames(out, (360, 640)))
    yellow = [int(((f[..., 0] > 200) & (f[..., 1] > 150) & (f[..., 2] < 90)).sum()) for f in frames]
    white = [int((f.min(axis=2) > 220).sum()) for f in frames]
    assert yellow[10] > 50 and white[10] > 50 and yellow[0] == 0               # a caption with one word lit
    xs = [np.where(((f[..., 0] > 200) & (f[..., 2] < 90)).any(axis=0))[0].mean() for f in (frames[10], frames[45])]
    assert xs[1] > xs[0] + 20                                                  # the highlight moved along


def test_make_video_takes_a_look(nova):
    from nova.skills.media import make_video
    assert "Video ready" in make_video("test topic", slides=2, format="vertical", look="teal_orange")
    assert len(list(Path(nova[0]["media"]["output_dir"]).glob("*.mp4"))) == 1


def test_cinematic_tools_are_offered_for_the_right_words(nova):
    from nova.tools import select_tools
    names = {t.name for t in select_tools("make a cinematic reel from my photos")}
    assert {"cinematic_reel", "cinematic_look", "photo_to_3d_shot", "save_brand_kit", "brand_kits"} <= names
    assert context.cfg is not None
