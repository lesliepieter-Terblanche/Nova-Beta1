"""News briefing: South African headlines plus news about the vendors you work with (free RSS, no keys).

  "What's in the news?"                 → top headlines from News24, BusinessTech, MyBroadband, TechCentral
  "Any news about Juniper this week?"   → Google News for that name
  "Vendor news"                         → Juniper, Avaya, Nokia, SonarSource, Westcon… (Settings → News)
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx

from nova import context
from nova.tools import register_group, tool

register_group("news", ["news", "headlines", "what's happening", "latest on", "any news", "vendor news", "in the news",
                        "press release", "announcement", "announced"])

FEEDS = {
    "News24": "https://feeds.news24.com/articles/news24/TopStories/rss",
    "BusinessTech": "https://businesstech.co.za/news/feed/",
    "MyBroadband": "https://mybroadband.co.za/news/feed",
    "TechCentral": "https://techcentral.co.za/feed/",
}
VENDORS = ["Juniper Networks", "Avaya", "Nokia", "SonarSource", "Westcon-Comstor"]
UA = {"User-Agent": "Mozilla/5.0 Nova-news"}


def _cfg() -> dict:
    return dict(((context.cfg or {}).get("news") or {}))


def parse(xml_text: str, source: str = "") -> list[dict]:
    """RSS 2.0 or Atom → [{title, link, when, source}] (newest first)."""
    out = []
    try:
        root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    for it in items:
        title = (it.findtext("title") or it.findtext("a:title", namespaces=ns) or "").strip()
        link = it.findtext("link") or ""
        if not link:
            el = it.find("a:link", ns)
            link = el.get("href", "") if el is not None else ""
        when_s = it.findtext("pubDate") or it.findtext("a:updated", namespaces=ns) or it.findtext("a:published", namespaces=ns)
        when = None
        if when_s:
            try:
                when = email.utils.parsedate_to_datetime(when_s)
            except (TypeError, ValueError):
                try:
                    when = dt.datetime.fromisoformat(when_s.replace("Z", "+00:00"))
                except ValueError:
                    when = None
        src = source or (it.findtext("source") or "").strip()
        if " - " in title and not source:                 # Google News: "Headline - Publisher"
            title, src = title.rsplit(" - ", 1)
        title = html.unescape(re.sub(r"\s+", " ", title)).strip()
        if title:
            out.append({"title": title, "link": link.strip(), "when": when, "source": src})
    out.sort(key=lambda x: x["when"].timestamp() if x["when"] else 0, reverse=True)
    return out


def fetch(url: str, source: str = "") -> list[dict]:
    r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
    r.raise_for_status()
    return parse(r.text, source)


def google_news(query: str, days: int = 7) -> list[dict]:
    q = quote_plus(f"{query} when:{max(1, days)}d")
    return fetch(f"https://news.google.com/rss/search?q={q}&hl=en-ZA&gl=ZA&ceid=ZA:en")


def _ago(when) -> str:
    if not when:
        return ""
    h = (dt.datetime.now(dt.timezone.utc) - when.astimezone(dt.timezone.utc)).total_seconds() / 3600
    return "just now" if h < 1 else f"{int(h)} h ago" if h < 24 else f"{int(h // 24)} d ago"


@tool(group="news")
def news_briefing(topic: str = "", max_items: int = 8) -> str:
    """Latest headlines: South African top stories, or news about a topic/company.
    Args:
        topic: empty for top SA headlines, or e.g. "Juniper Networks", "load shedding", "Eskom"
        max_items: how many headlines
    """
    max_items = max(1, min(int(max_items or 8), 15))
    try:
        if topic.strip():
            items = google_news(topic.strip())
            head = f"News about {topic.strip()}"
        else:
            items, errors = [], 0
            feeds = _cfg().get("feeds") or FEEDS
            feeds = feeds if isinstance(feeds, dict) else {u.split('/')[2]: u for u in feeds}
            for name, url in feeds.items():
                try:
                    items += fetch(url, name)[:4]
                except Exception:
                    errors += 1
            if not items and errors:
                return "ERROR: I couldn't reach the news sites right now."
            items.sort(key=lambda x: x["when"].timestamp() if x["when"] else 0, reverse=True)
            head = "Top headlines"
    except Exception as e:
        return f"ERROR: couldn't get the news ({e})."
    seen, out = set(), []
    for it in items:
        key = it["title"].lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= max_items:
            break
    if not out:
        return f"No recent news found{' about ' + topic if topic else ''}."
    lines = [f"{i}. {it['title']} ({it['source']}{', ' + _ago(it['when']) if it['when'] else ''})"
             for i, it in enumerate(out, 1)]
    context.record("scrape", f"News: {topic or 'top headlines'} {dt.date.today()}", out[0]["link"] or "news",
                   "\n".join(f"{it['title']} — {it['link']}" for it in out))
    return head + ":\n" + "\n".join(lines)


@tool(group="news")
def vendor_news(days: int = 7) -> str:
    """News from the last few days about the vendors and companies the user works with (set in Settings → News)."""
    vendors = _cfg().get("vendors") or VENDORS
    parts = []
    for v in vendors:
        try:
            items = google_news(str(v), days)[:3]
        except Exception:
            continue
        if items:
            parts.append(f"{v}: " + " | ".join(f"{it['title']} ({it['source']})" for it in items))
    if not parts:
        return f"No news about {', '.join(map(str, vendors))} in the last {days} days."
    return "Vendor news:\n" + "\n".join(parts)
