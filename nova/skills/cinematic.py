"""Cinematic mode: film-look reels from your own photos and clips, free and on this PC.

  "make a cinematic reel from the photos in Pictures/Harbour"   → 3D camera moves, grade, titles, music on the beat
  "give drone.mp4 the teal and orange look with widescreen bars" → the cinematic finish on any video
  "save a brand kit for Harbour Homes: navy and gold, @harbourhomes" → intro, lower third, watermark, outro

The engine is nova/cinema.py.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
from pathlib import Path

from .. import cinema, context, ffmpeg
from ..tools import register_group, tool
from .media import out_dir

register_group("cinematic", ["cinematic", "cinema", "film look", "movie look", "colour grade", "color grade",
                             "grading", "teal and orange", "widescreen", "letterbox", "film grain", "brand kit",
                             "branding", "my brand", "logo", "3d photo", "parallax", "camera move", "reel", "reels",
                             "intro sting", "outro", "lower third", "slow motion", "slow-mo", "premium video"])

LOOK_HELP = ", ".join(cinema.LOOKS)


def _files(spec: str) -> list[Path]:
    """The photos and videos meant: a folder, or files separated by semicolons / new lines."""
    from .files import safe
    out = []
    for part in [x.strip() for x in re.split(r"[;\n]", spec or "") if x.strip()]:
        p = safe(part)
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() in cinema.PHOTO + cinema.VIDEO)
        elif p.exists():
            out.append(p)
    return [f for f in out if f.suffix.lower() in cinema.PHOTO + cinema.VIDEO]


def _narration_words(text: str, wav: Path) -> list[tuple[float, float, str]]:
    """When each word of the narration is spoken: listened to if possible, else spread evenly."""
    words = text.split()
    dur = ffmpeg.duration(wav)
    heard = []
    try:
        heard = context.speech.transcribe_words(str(wav)) if hasattr(context.speech, "transcribe_words") else []
    except Exception as e:
        print(f"[cinematic] couldn't listen to the narration, spreading the words: {e}")
    if len(heard) < max(2, len(words) // 2):
        return cinema.spread_words(text, dur)
    out = []                                         # your own spelling, the heard timing
    for j, w in enumerate(words):
        k = min(len(heard) - 1, int(j * len(heard) / len(words)))
        k2 = min(len(heard) - 1, max(k, int((j + 1) * len(heard) / len(words)) - 1))
        out.append((heard[k][0], heard[k2][1], w))
    return out


@tool(group="cinematic")
def cinematic_reel(files: str, title: str = "", subtitle: str = "", narration: str = "", music_path: str = "",
                   brand: str = "", look: str = "", format: str = "vertical", seconds_per_shot: float = 3.0,
                   transition: str = "fade", call_to_action: str = "", widescreen_bars: bool = False) -> str:
    """Make a cinematic reel from photos and video clips: every photo becomes a 3D camera move (push-in, orbit,
    pull-out with real depth), clips are graded with a film look, cuts land on the beat of the music, and the brand
    kit adds an intro sting, lower third, watermark and outro. Free, made on this PC.
    Args:
        files: a folder of photos/clips, or the files separated by semicolons, in the order to show them
        title: title shown on screen (lower third; the intro headline if there's no brand kit)
        subtitle: smaller line under the title
        narration: optional voice-over text, spoken in Nova's voice with word-by-word highlighted captions
        music_path: optional music file; cuts are timed to its beat and it dips under the voice
        brand: name of a saved brand kit ("" = the default kit, "none" = no branding)
        look: colour grade — teal_orange, warm_film, moody, golden_hour, noir or clean ("" = the brand's look)
        format: vertical (Reels/TikTok/Shorts), landscape (YouTube) or square
        seconds_per_shot: roughly how long each shot stays on screen (1.5-8)
        transition: fade, dissolve, dip (to black), slide, wipe, zoom, circle or cut
        call_to_action: closing line on the outro, e.g. "Book a viewing today"
        widescreen_bars: black cinema bars top and bottom
    """
    media = _files(files)[:24]
    if not media:
        return "ERROR: I found no photos or videos there. Give me a folder, or files separated by semicolons."
    fmt = format if format in cinema.SIZES else "vertical"
    size = cinema.SIZES[fmt]
    kit = cinema.brand(brand)
    branded = kit is not None
    kit = kit or dict(cinema.BRAND_DEFAULTS)
    look = cinema.look_name(look or kit.get("look") or "teal_orange")
    per = max(1.5, min(8.0, float(seconds_per_shot or 3.0)))
    music = None
    if music_path:
        from .files import safe
        music = safe(music_path)
        if not music.exists():
            return f"ERROR: I can't find the music file {music_path}."
    work = Path(tempfile.mkdtemp(prefix="reel_", dir=out_dir()))
    try:
        tdur = 0.0 if cinema.TRANSITIONS.get(transition.lower(), "fade") == "cut" else 0.4
        clips: list[tuple[Path, float]] = []
        headline = kit["name"] if branded else title
        intro_len = 0.0
        if headline:
            intro_len = 1.8
            cinema.title_card(work / "intro.mp4", size, kit, headline,
                              (kit["tagline"] if branded else subtitle), intro_len + tdur)
            clips.append((work / "intro.mp4", intro_len + tdur))
        voice, voice_len, words = None, 0.0, []
        if narration.strip():
            voice = work / "voice.wav"
            context.speech.synth_wav(narration.strip(), voice)
            voice_len = ffmpeg.duration(voice)
            words = _narration_words(narration.strip(), voice)
        grid = cinema.beat_grid(music) if music else None
        lengths = cinema.cut_lengths(len(media), per, grid, first_cut_after=intro_len + tdur / 2,
                                     min_total=voice_len + 0.9 if voice else 0.0)
        cta = call_to_action or (kit["call_to_action"] if branded else "")
        outro = bool(cta or (branded and (kit["handle"] or kit["logo"])))
        depth_used = flat = 0
        for i, (f, seen) in enumerate(zip(media, lengths)):
            last = i == len(media) - 1 and not outro
            d = seen + (0 if last else tdur)
            shot = work / f"shot{i}.mp4"
            if f.suffix.lower() in cinema.PHOTO:
                info = cinema.photo_shot(f, shot, size, d, cinema.MOVES[i % len(cinema.MOVES)], look)
                depth_used += info["depth"]
                flat += not info["depth"]
            else:
                cinema.video_shot(f, shot, size, d, look)
            clips.append((shot, d))
        outro_len = 0.0
        if outro:
            outro_len = 2.4
            cinema.title_card(work / "outro.mp4", size, kit, cta or kit["name"] or title,
                              kit["tagline"] if cta and branded else "", outro_len, small=kit["handle"])
            clips.append((work / "outro.mp4", outro_len))
        total = sum(d for _, d in clips) - tdur * (len(clips) - 1)
        body_end = total - outro_len
        overlays = []
        if branded and title and body_end - intro_len > 3:
            lt = cinema.lower_third(work / "lower.png", size, kit, title, subtitle, clear_of_captions=bool(words))
            overlays.append((lt, intro_len + tdur + 0.3, min(body_end - 0.2, intro_len + tdur + 3.8)))
        if branded:
            wm = cinema.watermark(work / "mark.png", size, kit)
            if wm and body_end - intro_len > 1.5:
                overlays.append((wm, intro_len + tdur, body_end))
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        name = re.sub(r"[^\w]+", "_", title or kit["name"] or "cinematic_reel")[:50].strip("_") or "cinematic_reel"
        out = out_dir() / f"{name}_{stamp}.mp4"
        joined = work / "joined.mp4" if words else out
        voice_at = intro_len + tdur + 0.25
        cinema.assemble(clips, joined, size, transition, tdur, overlays, music, voice, voice_at,
                        bars=widescreen_bars, grain=True)
        if words:
            cinema.burn_captions(joined, out, [(a + voice_at, b + voice_at, w) for a, b, w in words],
                                 accent=cinema.rgb_of(kit["accent"], "#ffcd3c"), font_path=kit.get("font", ""),
                                 size=size)
        try:
            ffmpeg.run(["-ss", f"{intro_len + tdur + 0.8:.2f}", "-i", out, "-frames:v", "1", out.with_suffix(".jpg")])
        except Exception:
            pass
    finally:
        shutil.rmtree(work, ignore_errors=True)
    photos = sum(f.suffix.lower() in cinema.PHOTO for f in media)
    bits = [f"{len(media)} shots", f"{cinema.LOOKS[look][0].split(' — ')[0]} look"]
    if depth_used:
        bits.append(f"{depth_used} photo{'s' if depth_used != 1 else ''} with 3D camera moves")
    if grid:
        bits.append(f"cuts on the beat ({60 / grid[0]:.0f} bpm)")
    if words:
        bits.append("voice-over with highlighted captions")
    if branded:
        bits.append(f"{kit['name']} branding")
    context.record("video", title or kit["name"] or "Cinematic reel", out, ", ".join(bits))
    context.attach(out)
    note = ""
    if photos and flat:
        note = " The depth model couldn't be downloaded, so the photos moved without the 3D effect this time."
    if not branded and not brand:
        note += " No brand kit is saved yet — tell me your brand name, colours and logo and I'll add an intro and outro."
    return f"Cinematic reel ready: {out} ({total:.0f} seconds; {', '.join(bits)}).{note}"


@tool(group="cinematic")
def cinematic_look(path: str, look: str = "teal_orange", widescreen_bars: bool = True, film_grain: bool = True,
                   slow_motion: float = 1.0, steady: bool = False, brand: str = "none") -> str:
    """Give any video the cinematic finish: a film colour grade, soft vignette, film grain and widescreen bars —
    optionally slowed down and steadied. Use for phone footage, AI clips, stock clips or a finished video.
    Args:
        path: the video file
        look: teal_orange, warm_film, moody, golden_hour, noir or clean
        widescreen_bars: black cinema bars top and bottom
        film_grain: fine moving grain, like film
        slow_motion: playback speed, 1 = normal, 0.5 = half speed (0.25-2)
        steady: smooth out shaky handheld footage
        brand: brand kit whose logo/handle to add as a watermark ("none" = no watermark, "" = the default kit)
    """
    from .files import safe
    p = safe(path)
    if not p.exists() or p.suffix.lower() not in cinema.VIDEO:
        return f"ERROR: {path} isn't a video file I can find."
    key = cinema.look_name(look)
    out = out_dir() / f"{p.stem}_{key}_{dt.datetime.now():%H%M%S}.mp4"
    kit = cinema.brand(brand)
    work = Path(tempfile.mkdtemp(prefix="look_", dir=out_dir()))
    try:
        info = cinema.probe(p)
        logo = cinema.watermark(work / "mark.png", (info["w"], info["h"]), kit) if kit else None
        cinema.finish(p, out, key, widescreen_bars, film_grain, slow_motion, steady, logo)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    context.record("video", out.stem, out, f"cinematic finish: {key}")
    context.attach(out)
    return f"Done — {cinema.LOOKS[key][0].split(' — ')[0]} look applied: {out}"


@tool(group="cinematic")
def photo_to_3d_shot(photo: str, move: str = "push_in", seconds: float = 5.0, format: str = "vertical",
                     look: str = "") -> str:
    """Turn one still photo into a moving cinematic shot: Nova works out what is near and far in the picture and
    moves the camera through it with real parallax (the "3D photo" effect).
    Args:
        photo: the photo file
        move: push_in, pull_out, orbit_left, orbit_right or rise
        seconds: length of the shot (2-10)
        format: vertical, landscape or square
        look: optional colour grade — teal_orange, warm_film, moody, golden_hour, noir or clean
    """
    from .files import safe
    p = safe(photo)
    if not p.exists() or p.suffix.lower() not in cinema.PHOTO:
        return f"ERROR: {photo} isn't a photo I can find."
    size = cinema.SIZES.get(format, cinema.SIZES["vertical"])
    mv = re.sub(r"[^a-z]+", "_", move.lower()).strip("_")
    mv = mv if mv in cinema.MOVES else {"zoom_in": "push_in", "zoom_out": "pull_out", "left": "orbit_left",
                                        "right": "orbit_right", "up": "rise"}.get(mv, "push_in")
    out = out_dir() / f"{p.stem}_3d_{dt.datetime.now():%H%M%S}.mp4"
    info = cinema.photo_shot(p, out, size, max(2.0, min(10.0, float(seconds or 5))), mv,
                             cinema.look_name(look) if look else "")
    context.record("video", out.stem, out, f"3D photo shot: {mv}")
    context.attach(out)
    how = "with real depth" if info["depth"] else "without the 3D depth (the depth model couldn't be downloaded)"
    return f"Shot ready ({mv.replace('_', ' ')}, {info['seconds']:.0f} s, {how}): {out}"


@tool(group="cinematic")
def save_brand_kit(name: str, handle: str = "", tagline: str = "", colour: str = "", accent: str = "",
                   logo_path: str = "", look: str = "", call_to_action: str = "", make_default: bool = False) -> str:
    """Save (or update) a brand kit, used for the intro sting, lower third, watermark, caption highlight and outro
    of cinematic reels. Only the fields you give are changed.
    Args:
        name: the brand or project name, e.g. Harbour Homes
        handle: social handle or website shown on the outro, e.g. @harbourhomes
        tagline: short line under the name
        colour: main brand colour — a hex code like #101a3a or a colour name
        accent: accent colour for lines and highlighted caption words — hex or name
        logo_path: logo image file (PNG with a transparent background works best)
        look: the brand's usual colour grade — teal_orange, warm_film, moody, golden_hour, noir or clean
        call_to_action: usual closing line, e.g. "Book a viewing today"
        make_default: use this kit whenever no brand is named
    """
    logo = ""
    if logo_path:
        from .files import safe
        lp = safe(logo_path)
        if not lp.exists():
            return f"ERROR: I can't find the logo file {logo_path}."
        logo = str(lp)
    kit = cinema.save_brand(name, default=True if make_default else None, handle=handle, tagline=tagline,
                            colour=colour, accent=accent, logo=logo,
                            look=cinema.look_name(look) if look else "", call_to_action=call_to_action)
    is_default = cinema.brands().get("default") == name.strip().lower()
    return (f"Brand kit saved for {kit['name']}: colour {kit['colour']}, accent {kit['accent']}"
            + (f", handle {kit['handle']}" if kit["handle"] else "") + (", with logo" if kit["logo"] else ", no logo yet")
            + (f", {kit['look']} look" if kit["look"] else "") + (". It's the default kit." if is_default else "."))


@tool(group="cinematic")
def brand_kits() -> str:
    """List the saved brand kits and the cinematic looks available."""
    data = cinema.brands()
    kits = [f"- {k['name']}{' (default)' if key == data.get('default') else ''}: {k.get('colour')} / {k.get('accent')}"
            + (f", {k['handle']}" if k.get("handle") else "") + (", logo" if k.get("logo") else "")
            for key, k in data["kits"].items()]
    looks = "\n".join(f"- {k}: {v[0]}" for k, v in cinema.LOOKS.items())
    return ("Brand kits:\n" + ("\n".join(kits) if kits else "(none saved yet)")) + f"\n\nLooks:\n{looks}"
