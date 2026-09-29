"""Webcam + ad creation.

"Nova, take a photo of this and make an ad" ->
  webcam photo -> Gemini identifies the product -> background removed locally (rembg)
  -> copy written -> feed (1080x1080) + story (1080x1920) ad images + caption file
  -> optional short vertical ad video.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from pathlib import Path

from .. import context
from ..config import resolve
from ..tools import register_group, tool
from .media import build_video, draw_wrapped, font, gradient

register_group("camera", ["camera", "webcam", "photo of", "picture of", "snap", "look at this", "what is this",
                          "what am i holding", "advert", " ad ", " ad.", "ads", "advertis", "promo", "product",
                          "marketing", "listing", "sell this"])


def _ads_dir() -> Path:
    p = resolve("workspace/ads")
    p.mkdir(parents=True, exist_ok=True)
    return p


def capture(camera_index: int = 0, warmup: float = 1.2) -> Path:
    import cv2
    from ..camera import _hub
    out = resolve("workspace/camera") / f"cam_{dt.datetime.now():%Y%m%d_%H%M%S}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    if _hub is not None and _hub.running and _hub.snapshot(out):     # gestures/presence already have the camera
        context.record("image", out.name, out, "webcam photo")
        return out
    backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else 0
    cap = cv2.VideoCapture(camera_index, backend)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError("Couldn't open the webcam. Is another app using it?")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    end, frame = time.time() + warmup, None
    while time.time() < end:          # let auto-exposure settle
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    if frame is None:
        raise RuntimeError("The webcam returned no image.")
    cv2.imwrite(str(out), frame)
    context.record("image", out.name, out, "webcam photo")
    return out


@tool(group="camera")
def webcam_photo(send_to_phone: bool = False) -> str:
    """Take a photo with the webcam.
    Args:
        send_to_phone: also send it to the user's phone
    """
    p = capture()
    if send_to_phone:
        context.attach(p)
    return f"Photo saved: {p}"


@tool(group="camera")
def webcam_look(question: str = "What am I holding up? Describe it.") -> str:
    """Look through the webcam and answer a question about what's in front of it (uses Gemini vision).
    Args:
        question: what to find out
    """
    return context.llm.see(str(capture()), question)


def cutout(photo: Path):
    """Remove the background (rembg, local). Returns an RGBA image, or None if rembg isn't installed."""
    from PIL import Image
    try:
        from rembg import remove
    except ImportError:
        return None
    img = remove(Image.open(photo).convert("RGB"))
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def _dominant(img):
    small = img.convert("RGB").resize((40, 40))
    colors = sorted(small.getcolors(1600), reverse=True)
    for _, c in colors:
        if 40 < sum(c) / 3 < 220:
            return c
    return (90, 60, 160)


def compose_ad(photo: Path, product_rgba, copy: dict, size, out: Path, brand: str = ""):
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    base_img = product_rgba if product_rgba is not None else Image.open(photo).convert("RGBA")
    dom = _dominant(base_img)
    dark = tuple(max(0, int(c * 0.25)) for c in dom)
    rich = tuple(min(255, int(c * 1.1)) for c in dom)
    bg = gradient(size, dark, rich).convert("RGBA")
    d = ImageDraw.Draw(bg)
    margin = int(w * 0.07)
    vertical = h > w

    # copy first, so the product fills exactly the space left between text and button
    hf = font(int(w * (0.085 if vertical else 0.066)))
    sf = font(int(w * (0.04 if vertical else 0.032)), bold=False)
    y = int(h * (0.08 if vertical else 0.06))
    if brand:
        d.text((margin, y), brand.upper(), font=font(int(w * 0.03)), fill=(255, 255, 255, 200))
        y += int(w * 0.06)
    y = draw_wrapped(d, copy.get("headline", ""), (margin, y), hf, w - 2 * margin, spacing=6)
    y = draw_wrapped(d, copy.get("subline", ""), (margin, y + 10), sf, w - 2 * margin, fill=(235, 235, 245))

    cta = copy.get("cta", "Shop now")
    cf = font(int(w * 0.04))
    tw = d.textlength(cta, font=cf)
    bw, bh = tw + 110, int(cf.size * 2.1)
    bx, by = (w - bw) / 2, h - margin - bh - (int(h * 0.06) if vertical else 0)
    pf = font(int(w * 0.05))
    bottom_limit = by - (pf.size + 50 if copy.get("price") and vertical else 30)

    area_top = int(y + h * 0.03)
    area_h = max(120, int(bottom_limit - area_top))
    if product_rgba is not None:
        p = product_rgba.copy()
        scale = min((w * 0.78) / p.width, area_h / p.height)
        p = p.resize((max(1, int(p.width * scale)), max(1, int(p.height * scale))))
        px, py = (w - p.width) // 2, area_top + (area_h - p.height) // 2
        shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.ellipse((px + p.width * 0.1, py + p.height - 25, px + p.width * 0.9, py + p.height + 35), fill=(0, 0, 0, 150))
        bg = Image.alpha_composite(bg, shadow.filter(ImageFilter.GaussianBlur(25)))
        bg.alpha_composite(p, (px, py))
    else:   # no cut-out: rounded photo card
        p = Image.open(photo).convert("RGB")
        scale = min((w - 2 * margin) / p.width, area_h / p.height)
        p = p.resize((int(p.width * scale), int(p.height * scale)))
        mask = Image.new("L", p.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *p.size), 36, fill=255)
        bg.paste(p, ((w - p.width) // 2, area_top + (area_h - p.height) // 2), mask)
    d = ImageDraw.Draw(bg)

    # CTA pill + price
    d.rounded_rectangle((bx, by, bx + bw, by + bh), bh / 2, fill=(255, 255, 255))
    d.text((bx + 55, by + (bh - cf.size) / 2 - 4), cta, font=cf, fill=dark)
    if copy.get("price"):
        if vertical:
            d.text((margin, by - pf.size - 30), copy["price"], font=pf, fill=(255, 255, 255))
        else:   # square: price sits left of the button, on the same line
            d.text((margin, by + (bh - pf.size) / 2 - 6), copy["price"], font=pf, fill=(255, 255, 255))
    bg.convert("RGB").save(out, quality=95)


AD_PROMPT = """You are a sharp South African direct-response copywriter.
Product (from a photo): {desc}
Extra notes from the owner: {notes}
Write ad copy. Reply with JSON only:
{{"product_name": "...", "headline": "max 6 punchy words", "subline": "max 14 words, the key benefit",
  "cta": "2-3 words", "price": "{price}", "caption": "social caption, 2-4 short lines",
  "hashtags": "8 relevant hashtags", "video_script": [{{"heading": "...", "text": "...", "narration": "..."}},
  {{"heading": "...", "text": "...", "narration": "..."}}, {{"heading": "...", "text": "...", "narration": "..."}}]}}"""


@tool(group="camera")
def make_ad(notes: str = "", price: str = "", brand: str = "", image_path: str = "", make_video: bool = False) -> str:
    """Create social-media ads for a product. Takes a webcam photo unless an image path is given.
    Produces a square feed ad, a vertical story ad, a caption with hashtags, and optionally a short ad video.
    Args:
        notes: anything to emphasise (audience, offer, tone, where it will run)
        price: optional price text, e.g. "R499"
        brand: optional brand/shop name shown on the ad
        image_path: optional existing photo instead of the webcam
        make_video: also make a ~20 second vertical ad video
    """
    photo = Path(image_path).expanduser() if image_path else capture()
    desc = context.llm.see(str(photo), "Identify this product precisely (type, brand if visible, colour, material, "
                                       "notable features). Two sentences.")
    raw = context.llm.complete(AD_PROMPT.format(desc=desc, notes=notes or "none", price=price), prefer_smart=True,
                               temperature=0.8)
    copy = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    copy["price"] = price or copy.get("price", "")
    rgba = cutout(photo)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r"[^\w]+", "_", copy.get("product_name", "product"))[:40]
    folder = _ads_dir() / f"{name}_{stamp}"
    folder.mkdir(parents=True)
    feed, story = folder / "ad_feed_1080x1080.jpg", folder / "ad_story_1080x1920.jpg"
    compose_ad(photo, rgba, copy, (1080, 1080), feed, brand)
    compose_ad(photo, rgba, copy, (1080, 1920), story, brand)
    if rgba is not None:
        rgba.save(folder / "product_cutout.png")
    (folder / "caption.txt").write_text(f"{copy.get('caption', '')}\n\n{copy.get('hashtags', '')}", encoding="utf-8")
    (folder / "copy.json").write_text(json.dumps(copy, indent=2), encoding="utf-8")
    context.record("ad", copy.get("product_name", "Ad"), folder, copy.get("headline", ""))
    for f in (feed, story):
        context.attach(f)

    result = f"Ads ready in {folder}. Headline: \"{copy.get('headline')}\". Caption saved to caption.txt."
    if make_video and copy.get("video_script"):
        imgs = [str(story)] * len(copy["video_script"])
        vid = build_video(copy.get("product_name", "Ad"), copy["video_script"], "vertical", imgs)
        context.attach(vid)
        result += f" Video: {vid}"
    if rgba is None:
        result += " (Tip: install rembg for automatic background removal.)"
    return result
