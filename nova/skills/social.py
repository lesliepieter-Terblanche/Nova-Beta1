"""Social media posting through Postiz (github.com/gitroomhq/postiz-app) — an open-source scheduler you can host
yourself for free, or use as a paid cloud service.

  "what social channels do I have?"                          → the accounts connected in Postiz
  "post the Harbour reel to Instagram and TikTok at 6pm"     → uploads the video and schedules it
  "what's scheduled this week?"                              → the queue

Nova always asks for a yes before anything is published or scheduled. Set the address in Settings → Files & web
(social.postiz_url) and the key in Settings → API keys (POSTIZ_API_KEY). See docs/POSTIZ.md.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from .. import context
from ..tools import register_group, tool

register_group("social", ["postiz", "social media", "social post", "schedule a post", "schedule the post",
                          "schedule this", "post to", "post this", "post it", "post the", "publish", "linkedin post",
                          "instagram", "tiktok", "facebook page", "youtube short", "threads", "bluesky",
                          "what's scheduled", "whats scheduled", "content calendar", "social channels"])

CLOUD = "https://api.postiz.com/public/v1"
MEDIA = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4")
# what each network insists on (Postiz "settings"); anything not listed just needs its own name
DEFAULTS = {
    "x": {"who_can_reply_post": "everyone"},
    "linkedin": {"post_as_images_carousel": False},
    "linkedin-page": {"post_as_images_carousel": False},
    "instagram": {"post_type": "post", "is_trial_reel": False, "collaborators": []},
    "instagram-standalone": {"post_type": "post", "is_trial_reel": False, "collaborators": []},
    "tiktok": {"privacy_level": "PUBLIC_TO_EVERYONE", "duet": True, "stitch": True, "comment": True,
               "autoAddMusic": "no", "brand_content_toggle": False, "brand_organic_toggle": False,
               "video_made_with_ai": False, "content_posting_method": "DIRECT_POST"},
    "youtube": {"type": "public", "selfDeclaredMadeForKids": "no", "tags": []},
}
NAMES = {"x": "X", "linkedin": "LinkedIn", "linkedin-page": "LinkedIn page", "instagram": "Instagram",
         "instagram-standalone": "Instagram", "facebook": "Facebook", "tiktok": "TikTok", "youtube": "YouTube",
         "threads": "Threads", "bluesky": "Bluesky", "pinterest": "Pinterest", "gmb": "Google Business"}
ALIASES = {"twitter": "x", "insta": "instagram", "ig": "instagram", "fb": "facebook", "yt": "youtube",
           "shorts": "youtube", "tik tok": "tiktok"}


def base_url() -> str:
    url = str(((context.cfg or {}).get("social") or {}).get("postiz_url") or CLOUD).rstrip("/")
    return url if url.endswith("/public/v1") else url + "/public/v1"


def _key() -> str:
    return os.environ.get("POSTIZ_API_KEY", "").strip()


def call(method: str, path: str, **kw):
    """One request to Postiz. Raises RuntimeError with a plain reason when it doesn't work."""
    import httpx
    if not _key():
        raise RuntimeError("no Postiz key yet — add POSTIZ_API_KEY in Settings → API keys (see docs/POSTIZ.md)")
    try:
        r = httpx.request(method, base_url() + path, headers={"Authorization": _key()}, timeout=kw.pop("timeout", 60),
                          **kw)
    except httpx.HTTPError as e:
        raise RuntimeError(f"I can't reach Postiz at {base_url()} ({type(e).__name__}). Is it running?") from e
    if r.status_code == 401:
        raise RuntimeError("Postiz rejected the key — check POSTIZ_API_KEY in Settings → API keys")
    if r.status_code == 429:
        raise RuntimeError("Postiz says too many requests this hour — try again later")
    if r.status_code >= 400:
        raise RuntimeError(f"Postiz said {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def channels() -> list[dict]:
    return [c for c in call("GET", "/integrations") if not c.get("disabled")]


def pick_channels(want: str, have: list[dict]) -> tuple[list[dict], list[str]]:
    """The connected channels a phrase like "instagram and tiktok" means, and the names that matched nothing."""
    if not want.strip() or want.strip().lower() in ("all", "everything", "everywhere", "all channels"):
        return have, []
    picked, missing = [], []
    for raw in [x.strip().lower() for x in re.split(r",|;|\band\b|&|\+", want) if x.strip()]:
        word = ALIASES.get(raw, raw)
        hit = [c for c in have if word in (c.get("identifier", "").lower(), c.get("id", "").lower())
               or c.get("identifier", "").lower().startswith(word)
               or word in str(c.get("name", "")).lower() or word in str(c.get("profile", "")).lower()]
        if hit:
            picked += [c for c in hit if c not in picked]
        else:
            missing.append(raw)
    return picked, missing


def when_utc(when: str) -> tuple[str, str]:
    """("now" | "schedule", ISO time in UTC) for a phrase like "tomorrow 6pm"."""
    if not when.strip() or when.strip().lower() in ("now", "right now", "immediately", "asap"):
        return "now", dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    import dateparser
    tz = str(((context.cfg or {}).get("assistant") or {}).get("timezone") or "UTC")
    t = dateparser.parse(when, settings={"PREFER_DATES_FROM": "future", "TIMEZONE": tz,
                                         "RETURN_AS_TIMEZONE_AWARE": True})
    if t is None:
        raise ValueError(f"I couldn't work out the time '{when}'. Try 'tomorrow 6pm' or '2026-10-12 09:00'.")
    if t <= dt.datetime.now(dt.timezone.utc):
        raise ValueError(f"'{when}' is in the past.")
    return "schedule", t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def settings_for(identifier: str, text: str, title: str = "") -> dict:
    s = {"__type": identifier, **DEFAULTS.get(identifier, {})}
    if identifier == "youtube":
        s["title"] = (title or text.split("\n")[0])[:100].strip() or "New video"
    if identifier == "tiktok" and title:
        s["title"] = title[:90]
    return s


def label(c: dict) -> str:
    kind = NAMES.get(c.get("identifier", ""), str(c.get("identifier", "")).title())
    who = c.get("profile") or c.get("name") or ""
    return f"{kind} ({who})" if who else kind


@tool(group="social")
def social_channels() -> str:
    """List the social media accounts connected in Postiz that Nova can post to."""
    try:
        have = channels()
    except RuntimeError as e:
        return f"ERROR: {e}"
    if not have:
        return "Postiz is reachable but no channels are connected yet. Open Postiz and add your accounts first."
    return "Connected channels: " + "; ".join(label(c) for c in have) + "."


@tool(group="social", confirm=True)
def social_post(text: str, channels_to_post: str = "", media: str = "", when: str = "", title: str = "",
                draft: bool = False) -> str:
    """Publish or schedule a social media post through Postiz, with optional photos or a video. Always confirmed
    with the user first, because it is public.
    Args:
        text: the caption / post text, with hashtags
        channels_to_post: which accounts, e.g. "instagram and tiktok" or "linkedin" ("" = every connected channel)
        media: photo or video files to attach, separated by semicolons (a reel, a cover image…)
        when: when to post, e.g. "tomorrow 6pm" ("" = now)
        title: title for YouTube / TikTok (the first line of the text if left empty)
        draft: save it in Postiz as a draft instead of posting
    """
    from .files import safe
    try:
        have = channels()
        picked, missing = pick_channels(channels_to_post, have)
        if missing or not picked:
            names = "; ".join(label(c) for c in have) or "none"
            return (f"ERROR: no connected channel matches {', '.join(missing) or 'that'}. Connected: {names}. "
                    "Nothing was posted.")
        kind, date = when_utc(when)
        files = [safe(m.strip()) for m in re.split(r"[;\n]", media or "") if m.strip()]
        bad = [f.name for f in files if not f.exists() or f.suffix.lower() not in MEDIA]
        if bad:
            return f"ERROR: I can't attach {', '.join(bad)} (missing, or not a jpg/png/gif/webp/mp4). Nothing was posted."
        needs_video = [label(c) for c in picked if c.get("identifier") in ("tiktok", "youtube")
                       and not any(f.suffix.lower() == ".mp4" for f in files)]
        if needs_video:
            return f"ERROR: {', '.join(needs_video)} needs a video (.mp4). Nothing was posted."
        uploaded = []
        for f in files:
            with open(f, "rb") as fh:
                up = call("POST", "/upload", files={"file": (f.name, fh)}, timeout=600)
            uploaded.append({"id": up["id"], "path": up["path"]})
        body = {"type": "draft" if draft else kind, "date": date, "shortLink": False, "tags": [],
                "posts": [{"integration": {"id": c["id"]}, "value": [{"content": text, "image": uploaded}],
                           "settings": settings_for(c.get("identifier", ""), text, title)} for c in picked]}
        made = call("POST", "/posts", json=body, timeout=120)
    except (RuntimeError, ValueError) as e:
        return f"ERROR: {e}"
    where = ", ".join(label(c) for c in picked)
    n = len(made) if isinstance(made, list) else len(picked)
    if draft:
        how = "Saved as a draft in Postiz"
    elif kind == "now":
        how = "Sent for posting now"
    else:
        local = dt.datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=dt.timezone.utc).astimezone()
        how = f"Scheduled for {local:%A %d %B at %H:%M}"
    extra = f" with {len(uploaded)} file{'s' if len(uploaded) != 1 else ''}" if uploaded else ""
    return f"{how} on {where}{extra} ({n} post{'s' if n != 1 else ''}). Check it in Postiz if you want to edit it."


@tool(group="social")
def social_scheduled(days: int = 7) -> str:
    """What is queued in Postiz: the posts scheduled over the next days.
    Args:
        days: how many days ahead to look (1-60)
    """
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=max(1, min(60, int(days or 7))))
    try:
        data = call("GET", "/posts", params={"startDate": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                             "endDate": end.strftime("%Y-%m-%dT%H:%M:%S.000Z")})
    except RuntimeError as e:
        return f"ERROR: {e}"
    posts = data.get("posts", data) if isinstance(data, dict) else data
    if not posts:
        return f"Nothing is scheduled in the next {days} days."
    lines = []
    for p in sorted(posts, key=lambda p: str(p.get("publishDate", "")))[:25]:
        try:
            t = dt.datetime.fromisoformat(str(p.get("publishDate", "")).replace("Z", "+00:00")).astimezone()
            stamp = f"{t:%a %d %b %H:%M}"
        except ValueError:
            stamp = str(p.get("publishDate", "?"))
        integ = p.get("integration") or {}
        text = re.sub(r"<[^>]+>", "", str(p.get("content", ""))).strip().replace("\n", " ")
        lines.append(f"- {stamp} · {NAMES.get(integ.get('providerIdentifier', ''), integ.get('name', 'post'))}: "
                     f"{text[:90]}")
    return f"{len(posts)} post{'s' if len(posts) != 1 else ''} scheduled:\n" + "\n".join(lines)


def media_file(path: str) -> bool:
    return Path(path).suffix.lower() in MEDIA
