"""Web: search, scraping and website building."""
from __future__ import annotations

import csv
import datetime as dt
import re
import webbrowser
from pathlib import Path
from urllib.parse import urljoin

import httpx

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("web", ["search", "google it", "look up", "look it up", "website", "web site", "site", "scrape",
                       "url", "http", "www", ".co", "news", "price", "latest", "online", "internet",
                       "landing page", "web page", "webpage", "html", "who is", "what is"])
# (weather has its own skill: nova/skills/weather.py)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


def _get(url: str) -> httpx.Response:
    r = httpx.get(url, headers=UA, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r


def fetch_text(url: str) -> tuple[str, str]:
    """(main text, title) of a page."""
    html = _get(url).text
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    text = None
    try:
        import trafilatura
        text = trafilatura.extract(html, include_tables=True, include_links=False)
    except Exception:
        pass
    if not text:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            t.decompose()
        text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()
    return text, title


def _searxng(url: str, query: str, n: int) -> list:
    r = httpx.get(url.rstrip("/") + "/search", params={"q": query, "format": "json"}, headers=UA, timeout=20)
    r.raise_for_status()
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")}
            for x in r.json().get("results", [])[:n]]


def _duckduckgo(query: str, n: int) -> list:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    return [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")}
            for r in DDGS().text(query, max_results=n)]


@tool(group="web")
def web_search(query: str, max_results: int = 5) -> list:
    """Search the internet and return titles, links and snippets (your own SearXNG if configured, else DuckDuckGo).
    Args:
        query: search terms
        max_results: number of results
    """
    url = (context.cfg.get("web") or {}).get("searxng_url")
    if url:
        try:
            results = _searxng(url, query, max_results)
            if results:
                return results
        except Exception as e:
            print(f"[search] SearXNG failed ({e}); falling back to DuckDuckGo")
    return _duckduckgo(query, max_results) or "No results."


@tool(group="web")
def scrape_page(url: str, save: bool = False) -> str:
    """Read the main text of a web page. Optionally save it to the workspace.
    Args:
        url: the page address
        save: also save it as a markdown file
    """
    text, title = fetch_text(url)
    if save:
        slug = re.sub(r"[^\w]+", "_", title or "page")[:60]
        out = resolve("workspace/scrapes") / f"{slug}_{dt.date.today()}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f"# {title}\n\nSource: {url}\n\n{text}", encoding="utf-8")
        context.record("scrape", title or url, out, url)
        return f"Saved to {out}.\n\n{text[:3000]}"
    return f"{title}\n\n{text[:6000]}"


@tool(group="web")
def scrape_links(url: str, contains: str = "") -> list:
    """List the links on a web page.
    Args:
        url: the page address
        contains: only links whose text or address contains this
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_get(url).text, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href, txt = urljoin(url, a["href"]), a.get_text(" ", strip=True)
        if href in seen or not href.startswith("http"):
            continue
        if contains and contains.lower() not in (href + txt).lower():
            continue
        seen.add(href)
        out.append(f"{txt[:80]} -> {href}")
    return out[:60] or "No links found."


@tool(group="web")
def scrape_tables(url: str) -> str:
    """Extract all tables from a web page into CSV files in the workspace.
    Args:
        url: the page address
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(_get(url).text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return "No tables on that page. (Pages that load data with JavaScript can't be read this way.)"
    folder = resolve("workspace/scrapes") / f"tables_{dt.datetime.now():%Y%m%d_%H%M%S}"
    folder.mkdir(parents=True, exist_ok=True)
    previews = []
    for i, t in enumerate(tables, 1):
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
        rows = [r for r in rows if r]
        path = folder / f"table_{i}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        previews.append(f"table {i}: {len(rows)} rows, first row: {rows[0][:6] if rows else ''}")
    context.record("scrape", f"Tables from {url}", folder, url)
    return f"Saved {len(tables)} tables to {folder}\n" + "\n".join(previews[:10])


# ── website builder ───────────────────────────────────────
SITE_SYSTEM = """You are a senior web designer. Output ONE complete, self-contained index.html file and nothing else —
no explanations, no markdown fences. Use Tailwind via <script src="https://cdn.tailwindcss.com"></script>,
Google Fonts, modern responsive layout, real copy (no lorem ipsum), accessible colour contrast,
smooth hover states and a mobile menu. Images: use https://picsum.photos/seed/<word>/1200/800 placeholders."""


def _sites() -> Path:
    p = resolve(context.cfg.web.sites_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _clean_html(raw: str) -> str:
    raw = re.sub(r"^```(?:html)?\s*|\s*```$", "", raw.strip(), flags=re.M)
    i = raw.lower().find("<!doctype")
    return raw[i:] if i >= 0 else raw


def _site_url(name: str) -> str:
    return f"http://localhost:{context.cfg.dashboard.port}/sites/{name}/"


@tool(group="web")
def build_website(name: str, description: str) -> str:
    """Design and build a complete website from a description, then open a preview.
    Args:
        name: short folder name for the site, e.g. truehome-landing
        description: what the site is for, sections wanted, style, colours, audience
    """
    name = re.sub(r"[^\w-]+", "-", name.lower()).strip("-") or "site"
    html = _clean_html(context.llm.complete(f"Build this website:\n{description}", system=SITE_SYSTEM, prefer_smart=True))
    folder = _sites() / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(html, encoding="utf-8")
    (folder / "brief.md").write_text(description, encoding="utf-8")
    context.record("site", name, folder / "index.html", description[:300])
    webbrowser.open(_site_url(name))
    return f"Built '{name}' and opened the preview at {_site_url(name)}. Files are in {folder}."


@tool(group="web")
def edit_website(name: str, changes: str) -> str:
    """Change an existing website Nova built.
    Args:
        name: the site folder name
        changes: what to change
    """
    f = _sites() / name / "index.html"
    if not f.exists():
        return f"No site called {name}. Sites: {', '.join(p.name for p in _sites().iterdir())}"
    old = f.read_text(encoding="utf-8")
    (f.parent / f"index.{dt.datetime.now():%Y%m%d%H%M%S}.bak.html").write_text(old, encoding="utf-8")
    html = _clean_html(context.llm.complete(
        f"Here is the current index.html:\n\n{old}\n\nApply these changes and return the full updated file:\n{changes}",
        system=SITE_SYSTEM, prefer_smart=True))
    f.write_text(html, encoding="utf-8")
    context.record("site", name, f, f"edited: {changes[:200]}")
    webbrowser.open(_site_url(name))
    return f"Updated {name}; a backup of the previous version was kept."


@tool(group="web")
def list_websites() -> list:
    """List the websites Nova has built."""
    return [p.name for p in _sites().iterdir() if (p / "index.html").exists()] or "No sites yet."


@tool(group="web")
def open_website(name: str) -> str:
    """Open the preview of a site Nova built.
    Args:
        name: the site folder name
    """
    webbrowser.open(_site_url(name))
    return f"Opened {_site_url(name)}"
