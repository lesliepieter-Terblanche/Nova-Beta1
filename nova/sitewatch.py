"""Watching each business's website (v2.38): is it up, is it healthy, how does Google see it, what are rivals doing.

  health   every round of the business worker: does the site answer, how fast, when does its security certificate
           run out; once a day also: do the links on the home page work, is there still a way to contact you.
           You get a message the moment a site goes down and again when it is back.
  SEO      once a week: reads up to ten pages and lists what hurts the site in Google (titles, descriptions,
           headings, thin or script-only pages, images without descriptions), with a score out of 100 and drafted
           titles and descriptions in the approval queue.
  rivals   once a week: reads the competitor pages you named and says what changed (new lines, new prices).

Everything is read the way a visitor would read it — no logins, nothing is changed on any site.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlparse

from . import context

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept-Language": "en-ZA,en;q=0.9"}
GUARDED = (401, 403, 429)        # the site answers but turns automatic readers away: it is up for people
_SCHEMA = """
CREATE TABLE IF NOT EXISTS biz_health(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, up INTEGER, status INTEGER DEFAULT 0,
  ms INTEGER DEFAULT 0, ssl_days INTEGER, deep INTEGER DEFAULT 0, links INTEGER DEFAULT 0, broken TEXT DEFAULT '[]',
  contact INTEGER DEFAULT 1, error TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS biz_health_biz ON biz_health(biz, id);
CREATE TABLE IF NOT EXISTS biz_seo(id INTEGER PRIMARY KEY, biz TEXT, ts TEXT, score INTEGER, pages INTEGER,
  issues TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS biz_rivals(id INTEGER PRIMARY KEY, biz TEXT, name TEXT, url TEXT, text TEXT DEFAULT '',
  checked TEXT DEFAULT '', changed TEXT DEFAULT '', summary TEXT DEFAULT '');
"""
PRICE = re.compile(r"\bR\s?\d[\d\s,]*(?:\.\d{2})?")


# ── storage ───────────────────────────────────────────────
def _db():
    s = context.store
    if s is None:
        raise RuntimeError("Nova's memory isn't open")
    if not getattr(s, "_sitewatch_ready", False):
        with s.lock:
            s.db.executescript(_SCHEMA)
            s.db.commit()
        s._sitewatch_ready = True
    return s


def _q(sql: str, args: tuple = ()) -> list[dict]:
    s = _db()
    with s.lock:
        return [dict(r) for r in s.db.execute(sql, args)]


def _x(sql: str, args: tuple = ()) -> int:
    s = _db()
    with s.lock:
        cur = s.db.execute(sql, args)
        s.db.commit()
        return cur.lastrowid


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _age_hours(ts: str) -> float:
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(ts)).total_seconds() / 3600
    except (ValueError, TypeError):
        return 1e9


def cfg() -> dict:
    return dict(((context.cfg or {}).get("business") or {}))


# ── reading pages ─────────────────────────────────────────
def _get(url: str, timeout: float = 20):
    """One page, as a visitor's browser would ask for it. Returns (status, text, seconds, final url)."""
    import httpx
    t0 = time.time()
    r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
    return r.status_code, r.text, time.time() - t0, str(r.url)


def _cert_days(host: str) -> int | None:
    """Days until the site's security certificate runs out (None when that can't be read)."""
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
                end = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
        return int((end - time.time()) // 86400)
    except (OSError, ssl.SSLError, KeyError, ValueError):
        return None


def _soup(text: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(text or "", "html.parser")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _links(base: str, soup, limit: int = 30) -> list[str]:
    """The site's own pages linked from this one."""
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "whatsapp:")):
            continue
        u = urljoin(base, href).split("#")[0]
        if _host(u) != _host(base) or u in seen or re.search(r"\.(jpe?g|png|gif|webp|svg|pdf|zip|mp4)$", u, re.I):
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _has_contact(soup, text: str) -> bool:
    if soup.find("form") or soup.find("a", href=re.compile(r"^(mailto:|tel:|https://wa\.me|whatsapp:)", re.I)):
        return True
    return bool(soup.find("a", string=re.compile(r"contact|get in touch|enquir", re.I))
                or re.search(r"contact|enquir|get in touch", text or "", re.I))


# ── 1. health ─────────────────────────────────────────────
def check(b: dict, deep: bool = False) -> dict:
    """Look at a business's website once and save what was found. `deep` also tries the links on the home page."""
    url = b.get("url") or ""
    row = {"biz": b["id"], "ts": now(), "up": 0, "status": 0, "ms": 0, "ssl_days": None, "deep": int(deep),
           "links": 0, "broken": [], "contact": 1, "error": ""}
    try:
        status, text, secs, final = _get(url)
        row.update(status=status, ms=int(secs * 1000), up=int(status < 400 or status in GUARDED))
        if status in GUARDED:
            row["error"] = f"it answers, but its protection turns automatic checks away (code {status}), so I can't look inside"
            deep = False
        elif status >= 400:
            row["error"] = f"the site answers with error {status}"
    except Exception as e:
        text, final = "", url
        row["error"] = _plain(e)
    if row["up"]:
        if final.startswith("https"):
            row["ssl_days"] = _cert_days(urlparse(final).hostname or "")
        if deep:
            soup = _soup(text)
            row["contact"] = int(_has_contact(soup, text))
            links = _links(final, soup, 25)
            row["links"] = len(links)
            for u in links:
                try:
                    st = _get(u, timeout=15)[0]
                    if st >= 400 and st not in (401, 403, 405, 429):       # blocked to robots isn't broken
                        row["broken"].append({"url": u, "why": f"error {st}"})
                except Exception as e:
                    row["broken"].append({"url": u, "why": _plain(e)})
    _x("INSERT INTO biz_health(biz,ts,up,status,ms,ssl_days,deep,links,broken,contact,error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
       (row["biz"], row["ts"], row["up"], row["status"], row["ms"], row["ssl_days"], row["deep"], row["links"],
        json.dumps(row["broken"]), row["contact"], row["error"][:300]))
    _x("DELETE FROM biz_health WHERE biz=? AND ts<?", (b["id"], (dt.datetime.now() - dt.timedelta(days=30)).isoformat()))
    return row


def _plain(e: Exception) -> str:
    t = str(e) or type(e).__name__
    if re.search(r"getaddrinfo|Name or service|nodename|resolve", t, re.I):
        return "the web address can't be found (DNS)"
    if re.search(r"timed? ?out", t, re.I):
        return "the site didn't answer in time"
    if re.search(r"certificate|SSL", t, re.I):
        return "the security certificate is broken or expired"
    if re.search(r"refused|unreachable|reset", t, re.I):
        return "the server refused the connection"
    return t[:140]


def health(biz: str) -> dict:
    """What the dashboard shows for a site: its light, speed, certificate, uptime, broken links."""
    rows = _q("SELECT * FROM biz_health WHERE biz=? ORDER BY id DESC LIMIT 700", (biz,))
    if not rows:
        return {"state": "unknown"}
    last = rows[0]
    deep = next((r for r in rows if r["deep"] and r["up"]), None)
    week = [r for r in rows if _age_hours(r["ts"]) <= 168]
    since = None
    if not last["up"]:
        since = last["ts"]
        for r in rows:
            if r["up"]:
                break
            since = r["ts"]
    ssl_days = next((r["ssl_days"] for r in rows if r["ssl_days"] is not None), None)
    fast = sorted(r["ms"] for r in week if r["up"])
    return {"state": "up" if last["up"] else "down", "checked": last["ts"], "status": last["status"], "ms": last["ms"],
            "typical_ms": fast[len(fast) // 2] if fast else 0, "error": last["error"], "down_since": since,
            "uptime": round(100 * sum(r["up"] for r in week) / len(week), 1) if week else None, "checks": len(week),
            "ssl_days": ssl_days, "broken": json.loads(deep["broken"]) if deep else [], "links": deep["links"] if deep else 0,
            "contact": bool(deep["contact"]) if deep else True, "deep_checked": deep["ts"] if deep else ""}


def online() -> bool:
    """Is this PC's own internet working? (So a dead Wi-Fi isn't reported as three websites down.)"""
    for url in ("https://www.google.com/generate_204", "https://www.cloudflare.com/cdn-cgi/trace"):
        try:
            if _get(url, timeout=8)[0] < 500:
                return True
        except Exception:
            continue
    return False


def watch(businesses: list[dict]) -> list[str]:
    """One round for every business with a web address. Returns the alerts to send (down, back up, certificate)."""
    alerts = []
    for b in businesses:
        if not (b.get("url") or "").startswith("http"):
            continue
        prev = _q("SELECT * FROM biz_health WHERE biz=? ORDER BY id DESC LIMIT 1", (b["id"],))
        last_deep = _q("SELECT ts FROM biz_health WHERE biz=? AND deep=1 ORDER BY id DESC LIMIT 1", (b["id"],))
        deep = not last_deep or _age_hours(last_deep[0]["ts"]) >= 24
        row = check(b, deep=deep)
        if not row["up"] and (not prev or prev[0]["up"]):
            time.sleep(float(cfg().get("recheck_seconds", 20)))          # one blip isn't an outage: look again
            row = check(b)
            if not row["up"] and not row["status"] and not online():
                _x("DELETE FROM biz_health WHERE biz=? AND up=0 AND id>?", (b["id"], prev[0]["id"] if prev else 0))
                print("[sitewatch] this PC is offline — not counting that as the website being down")
                return alerts
            if not row["up"]:
                alerts.append(f"🔴 The {b['name']} website is DOWN — {row['error'] or 'no answer'}. ({b['url']})")
        elif row["up"] and prev and not prev[0]["up"]:
            start = None
            for h in _q("SELECT up, ts FROM biz_health WHERE biz=? ORDER BY id DESC LIMIT 300", (b["id"],))[1:]:
                if h["up"]:
                    break
                start = h["ts"]
            mins = int(_age_hours(start) * 60) if start else 0
            alerts.append(f"🟢 The {b['name']} website is back up" + (f" (it was down for about {mins} min)." if mins else "."))
        if row["up"] and deep:
            if row["ssl_days"] is not None and row["ssl_days"] <= 14:
                alerts.append(f"🔒 The {b['name']} website's security certificate runs out in {max(0, row['ssl_days'])} days — "
                              "ask your web host to renew it, or visitors will get a warning page.")
            if row["broken"]:
                alerts.append(f"🔗 {b['name']}: {len(row['broken'])} broken link(s) on the home page — see the Business dashboard.")
            if not row["contact"]:
                alerts.append(f"✉ {b['name']}: I can't find a contact form or contact details on the home page any more.")
    return alerts


# ── 4. SEO ────────────────────────────────────────────────
def _sitemap(root: str) -> list[str]:
    try:
        status, text, _, _ = _get(urljoin(root, "/sitemap.xml"), timeout=15)
    except Exception:
        return []
    if status >= 400:
        return []
    return [u.strip() for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text) if not u.strip().endswith(".xml")]


def _page(url: str, text: str) -> dict:
    soup = _soup(text)
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    imgs = soup.find_all("img")
    scripts = len(soup.find_all("script"))
    viewport = bool(soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}))
    og = bool(soup.find("meta", attrs={"property": "og:image"}))
    for t in soup(["script", "style", "noscript", "svg", "head"]):
        t.extract()
    words = len(re.findall(r"\w{2,}", soup.get_text(" ")))
    return {"url": url, "title": title, "desc": (desc.get("content") or "").strip() if desc else "",
            "h1": len(soup.find_all("h1")), "words": words, "scripts": scripts, "imgs": len(imgs),
            "no_alt": sum(1 for i in imgs if not (i.get("alt") or "").strip()),
            "noindex": bool(robots and "noindex" in (robots.get("content") or "").lower()),
            "viewport": viewport, "og": og}


def _short(url: str) -> str:
    p = urlparse(url)
    return (p.path or "/") if p.path not in ("", "/") else "home page"


def audit(pages: list[dict], has_sitemap: bool, has_robots: bool) -> tuple[int, list[dict]]:
    """Score the pages out of 100 and list what to fix, worst first. Each issue: {sev 1-3, page, text, fix}."""
    issues = []

    def add(sev, page, text, fix):
        issues.append({"sev": sev, "page": page, "text": text, "fix": fix})
    titles: dict[str, list[str]] = {}
    for p in pages:
        name = _short(p["url"])
        if p["noindex"]:
            add(3, name, "is hidden from Google (it says 'noindex')", "Remove the noindex setting unless that is on purpose.")
        if p["words"] < 40 and p["scripts"] >= 3:
            add(3, name, "has almost no text until JavaScript runs, so Google and link previews may see an empty page",
                "Ask your site builder for 'server-side rendering' or 'pre-rendering', or put the key text in the page itself.")
        elif p["words"] < 150:
            add(2, name, f"is thin ({p['words']} words)", "Add a few paragraphs that answer what a visitor would ask.")
        if not p["title"]:
            add(3, name, "has no page title", "Give it a title of 30–60 characters with what you sell and where.")
        elif len(p["title"]) > 65:
            add(1, name, f"title is long ({len(p['title'])} characters) and will be cut off in Google", "Shorten it to under 60.")
        elif len(p["title"]) < 15:
            add(2, name, f"title is very short (“{p['title']}”)", "Say what the page offers and where, in 30–60 characters.")
        if p["title"]:
            titles.setdefault(p["title"].lower(), []).append(name)
        if not p["desc"]:
            add(2, name, "has no description for Google to show under the title", "Write 120–155 characters that make someone click.")
        elif len(p["desc"]) > 170:
            add(1, name, f"description is long ({len(p['desc'])} characters)", "Trim it to about 155.")
        if p["h1"] == 0 and p["words"] >= 40:
            add(2, name, "has no main heading (H1)", "Add one clear heading that says what the page is about.")
        elif p["h1"] > 1:
            add(1, name, f"has {p['h1']} main headings (H1)", "Keep one; make the others sub-headings.")
        if p["imgs"] and p["no_alt"] / p["imgs"] > 0.3:
            add(1, name, f"{p['no_alt']} of {p['imgs']} pictures have no description (alt text)",
                "Describe each picture in a few words — it helps Google Images and blind visitors.")
        if not p["viewport"]:
            add(2, name, "isn't marked as phone-friendly (no viewport setting)", "Ask your site builder to add the viewport tag.")
    for t, where in titles.items():
        if len(where) > 1:
            add(2, ", ".join(where[:4]), "share the same title", "Give every page its own title.")
    if pages and not pages[0]["og"]:
        add(1, "home page", "has no share picture (og:image), so links on WhatsApp and Facebook look bare",
            "Add a 1200×630 share image in your site builder's SEO settings.")
    if not has_sitemap:
        add(2, "whole site", "has no sitemap.xml for Google to find all the pages", "Switch the sitemap on in your site builder, then submit it in Google Search Console.")
    if not has_robots:
        add(1, "whole site", "has no robots.txt", "Add one that allows everything and points to the sitemap.")
    issues.sort(key=lambda i: -i["sev"])
    score = max(5, 100 - sum({3: 14, 2: 6, 1: 2}[i["sev"]] for i in issues))
    return score, issues


def seo_check(b: dict) -> dict:
    """Read up to ten pages of the site and save the score and the list of fixes."""
    root = b.get("url") or ""
    status, text, _, final = _get(root)
    if status >= 400:
        raise RuntimeError(f"the site answers with error {status}")
    urls = [final] + [u for u in (_sitemap(final) or _links(final, _soup(text), 12)) if u.rstrip("/") != final.rstrip("/")]
    pages = [_page(final, text)]
    for u in urls[1:10]:
        try:
            st, t, _, f = _get(u, timeout=15)
            if st < 400:
                pages.append(_page(f, t))
        except Exception:
            continue
    try:
        has_robots = _get(urljoin(final, "/robots.txt"), timeout=10)[0] < 400
    except Exception:
        has_robots = False
    score, issues = audit(pages, bool(_sitemap(final)), has_robots)
    _x("INSERT INTO biz_seo(biz,ts,score,pages,issues) VALUES(?,?,?,?,?)", (b["id"], now(), score, len(pages), json.dumps(issues)))
    return {"score": score, "pages": len(pages), "issues": issues, "home": pages[0]}


def seo(biz: str) -> dict:
    rows = _q("SELECT * FROM biz_seo WHERE biz=? ORDER BY id DESC LIMIT 2", (biz,))
    if not rows:
        return {}
    return {"score": rows[0]["score"], "pages": rows[0]["pages"], "ts": rows[0]["ts"], "issues": json.loads(rows[0]["issues"]),
            "before": rows[1]["score"] if len(rows) > 1 else None}


def seo_report(b: dict, res: dict) -> str:
    """The fixes as a to-do, with a drafted title and description for the home page when the model is there."""
    lines = [f"{b['name']} scores {res['score']} out of 100 in my Google check ({res['pages']} pages read).", ""]
    for i, it in enumerate(res["issues"][:12], 1):
        lines.append(f"{i}. {it['page']} {it['text']}.\n   Fix: {it['fix']}")
    if not res["issues"]:
        lines.append("Nothing to fix this week.")
    home = res.get("home") or {}
    if context.llm and (not home.get("desc") or not home.get("title") or len(home.get("title", "")) < 15):
        try:
            draft = (context.llm.complete(
                f"Write a Google page title (max 60 characters) and a description (max 155 characters) for the home "
                f"page of {b['name']} ({b['url']}): {b['about']}\nSouth African English, no hype, nothing invented. "
                "Reply exactly as:\nTitle: …\nDescription: …", prefer_smart=True, temperature=0.4) or "").strip()
            if draft:
                lines += ["", "Ready to paste for the home page:", draft]
        except Exception as e:
            print(f"[sitewatch] no drafted title: {e}")
    return "\n".join(lines)


# ── 6. rivals ─────────────────────────────────────────────
def add_rival(biz: str, name: str, url: str) -> dict:
    url = url.strip()
    if not url:
        raise ValueError("give me the competitor's web address")
    if not url.startswith("http"):
        url = "https://" + url
    name = name.strip() or _host(url)
    have = _q("SELECT id FROM biz_rivals WHERE biz=? AND url=?", (biz, url))
    rid = have[0]["id"] if have else _x("INSERT INTO biz_rivals(biz,name,url) VALUES(?,?,?)", (biz, name[:80], url[:300]))
    return _q("SELECT id,biz,name,url,checked,changed,summary FROM biz_rivals WHERE id=?", (rid,))[0]


def drop_rival(rival_id: int) -> None:
    _x("DELETE FROM biz_rivals WHERE id=?", (int(rival_id),))


def rivals(biz: str) -> list[dict]:
    return _q("SELECT id,biz,name,url,checked,changed,summary FROM biz_rivals WHERE biz=? ORDER BY id", (biz,))


def _lines(html_text: str) -> list[str]:
    soup = _soup(html_text)
    for t in soup(["script", "style", "noscript", "svg"]):
        t.extract()
    out, seen = [], set()
    for ln in soup.get_text("\n").splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if len(ln) >= 4 and ln not in seen:
            seen.add(ln)
            out.append(ln[:200])
    return out[:1500]


def check_rival(r: dict) -> str:
    """Read one competitor page and say what is new since last time. Returns the summary ('' = no change)."""
    row = _q("SELECT * FROM biz_rivals WHERE id=?", (r["id"],))[0]
    try:
        status, text, _, _ = _get(row["url"])
    except Exception as e:
        _x("UPDATE biz_rivals SET checked=?, summary=? WHERE id=?", (now(), f"couldn't read it: {_plain(e)}", row["id"]))
        return ""
    if status >= 400:
        _x("UPDATE biz_rivals SET checked=?, summary=? WHERE id=?",
           (now(), f"couldn't read it (error {status} — some big sites block automatic readers)", row["id"]))
        return ""
    new = _lines(text)
    old = row["text"].split("\n") if row["text"] else []
    if not old:
        _x("UPDATE biz_rivals SET text=?, checked=?, summary=? WHERE id=?",
           ("\n".join(new), now(), "First look saved — changes show from next week.", row["id"]))
        return ""
    added = [ln[2:] for ln in difflib.ndiff(old, new) if ln.startswith("+ ")]
    gone = [ln[2:] for ln in difflib.ndiff(old, new) if ln.startswith("- ")]
    if not added and not gone:
        _x("UPDATE biz_rivals SET checked=?, summary=? WHERE id=?", (now(), "No change since last look.", row["id"]))
        return ""
    old_prices = {p.strip(" ,") for p in PRICE.findall(" ".join(old))}
    new_prices = {p.strip(" ,") for p in PRICE.findall(" ".join(new))}
    bits = []
    if new_prices - old_prices:
        bits.append("new prices: " + ", ".join(sorted(new_prices - old_prices)[:6]))
    if old_prices - new_prices:
        bits.append("prices gone: " + ", ".join(sorted(old_prices - new_prices)[:6]))
    if added:
        bits.append(f"{len(added)} new line(s), e.g. “" + "” · “".join(a[:90] for a in added[:3]) + "”")
    if gone and not added:
        bits.append(f"{len(gone)} line(s) removed, e.g. “{gone[0][:90]}”")
    summary = "; ".join(bits)
    if context.llm and len(added) >= 3:
        try:
            said = (context.llm.complete(
                f"A competitor's web page ({row['name']}) changed. In at most two short plain sentences say what is "
                "new that a rival business should know (offers, prices, products, listings). Nothing invented.\n\n"
                "NEW LINES:\n" + "\n".join(added[:60]) + "\n\nREMOVED LINES:\n" + "\n".join(gone[:30]),
                prefer_smart=False, temperature=0.2) or "").strip()
            if said:
                summary = said + (f" ({bits[0]})" if new_prices - old_prices else "")
        except Exception as e:
            print(f"[sitewatch] plain summary instead: {e}")
    _x("UPDATE biz_rivals SET text=?, checked=?, changed=?, summary=? WHERE id=?",
       ("\n".join(new), now(), now(), summary[:600], row["id"]))
    return summary


def check_rivals(biz: str = "", force: bool = False) -> list[str]:
    """Look at every competitor page that hasn't been looked at for a week. Returns 'name: what changed' lines."""
    out = []
    for r in _q("SELECT id,biz,name,url,checked FROM biz_rivals ORDER BY id"):
        if (biz and r["biz"] != biz) or (not force and _age_hours(r["checked"]) < 24 * 6.5):
            continue
        s = check_rival(r)
        if s:
            out.append(f"{r['name']}: {s}")
    return out


def summary(biz: str) -> dict:
    return {"health": health(biz), "seo": seo(biz), "rivals": rivals(biz)}
