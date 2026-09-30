"""Price watcher: "tell me when this drops below R2,000" for Takealot and most online shops.

Nova checks the page every few hours (Takealot through its own product API; other shops through the price that
shops publish for Google — JSON-LD / product meta tags). When the price hits your target you get a Telegram
message and a spoken heads-up. Watches are saved and survive restarts.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time

import httpx

from nova import context
from nova.tools import register_group, tool

register_group("prices", ["price", "prices", "cheaper", "drops below", "price drop", "on sale", "sale", "takealot",
                          "discount", "watch the price", "price watch", "deal"])

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36", "Accept-Language": "en-ZA,en;q=0.9"}
_SCHEMA = """CREATE TABLE IF NOT EXISTS price_watches(
  id INTEGER PRIMARY KEY, url TEXT, title TEXT, target REAL, currency TEXT, last_price REAL, lowest REAL,
  checked TEXT, every_hours REAL, created TEXT, status TEXT)"""


def _num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(" ", " ")
    m = re.search(r"\d[\d\s,]*(?:[.,]\d{1,2})?", s)
    if not m:
        return None
    t = m.group(0).replace(" ", "")
    if re.search(r",\d{2}$", t) and "." not in t:      # 1 299,99
        t = t.replace(",", ".")
    return float(t.replace(",", ""))


def takealot(url: str) -> dict | None:
    m = re.search(r"PLID(\d+)", url, re.I)
    if not m:
        return None
    r = httpx.get(f"https://api.takealot.com/rest/v-1-12-0/product-details/PLID{m.group(1)}", params={"platform": "desktop"},
                  headers=UA, timeout=20)
    r.raise_for_status()
    d = r.json()
    title = (d.get("title") or (d.get("core") or {}).get("title") or "").strip()
    price = None
    buybox = d.get("buybox") or {}
    for key in ("prices", "app_prices"):
        vals = buybox.get(key)
        if vals:
            price = _num(vals[0])
            break
    if price is None:
        price = _num(buybox.get("pretty_price")) or _num(((d.get("event_data") or {}).get("documents") or {})
                                                      .get("product", {}).get("purchase_price"))
    return {"title": title or "Takealot product", "price": price, "currency": "ZAR"}


def from_html(html: str) -> dict:
    """Price the way shops publish it for Google: JSON-LD Product/Offer, then meta tags, then a visible R price."""
    title = ""
    m = re.search(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)", html, re.I) or \
        re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:150]
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.I | re.S):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@graph"):
                    stack.extend(node["@graph"])
                offers = node.get("offers")
                if offers:
                    offers = offers if isinstance(offers, list) else [offers]
                    for o in offers:
                        p = _num(o.get("price") or o.get("lowPrice"))
                        if p:
                            return {"title": node.get("name") or title, "price": p,
                                    "currency": o.get("priceCurrency") or "ZAR"}
            elif isinstance(node, list):
                stack.extend(node)
    for pat in (r"<meta[^>]+(?:property|itemprop|name)=[\"'](?:product:price:amount|og:price:amount|price)[\"'][^>]+"
                r"content=[\"']([^\"']+)", r"itemprop=[\"']price[\"'][^>]*content=[\"']([^\"']+)"):
        m = re.search(pat, html, re.I)
        if m and _num(m.group(1)):
            return {"title": title, "price": _num(m.group(1)), "currency": "ZAR"}
    m = re.search(r"R\s?(\d{1,3}(?:[ , ]\d{3})*(?:[.,]\d{2})?)", re.sub(r"<[^>]+>", " ", html))
    return {"title": title, "price": _num(m.group(1)) if m else None, "currency": "ZAR"}


def price_of(url: str) -> dict:
    t = takealot(url) if "takealot.com" in url else None
    if t and t.get("price"):
        return t
    r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
    r.raise_for_status()
    return from_html(r.text)


def _db():
    s = context.store
    with s.lock:
        s.db.execute(_SCHEMA)
    return s


def _money(p: float | None, cur: str = "ZAR") -> str:
    if p is None:
        return "?"
    sym = "R" if cur in ("ZAR", "R") else cur + " "
    return f"{sym}{p:,.2f}".replace(",", " ").replace(".00", "")


@tool(group="prices")
def watch_price(url: str, target_price: float, check_every_hours: float = 6) -> str:
    """Watch a product's price and tell the user when it drops to (or below) a target.
    Args:
        url: product page link (Takealot and most online shops)
        target_price: the price to wait for, in rand
        check_every_hours: how often to check (minimum 1)
    """
    try:
        info = price_of(url)
    except Exception as e:
        return f"ERROR: couldn't read that page ({e})."
    if info.get("price") is None:
        return "I couldn't find a price on that page — try the product's own page (not a search or list page)."
    s = _db()
    now = dt.datetime.now().isoformat(timespec="seconds")
    with s.lock:
        wid = s.db.execute("INSERT INTO price_watches(url,title,target,currency,last_price,lowest,checked,every_hours,"
                           "created,status) VALUES(?,?,?,?,?,?,?,?,?,'watching')",
                           (url, info["title"], float(target_price), info["currency"], info["price"], info["price"], now,
                            max(1.0, float(check_every_hours)), now)).lastrowid
        s.db.commit()
    context.record("scrape", f"Price watch: {info['title'][:60]}", url, f"target {_money(target_price)}")
    _start()
    if info["price"] <= target_price:
        return (f"{info['title']} is already {_money(info['price'], info['currency'])} — at or below your "
                f"{_money(target_price)}. I'll keep watching anyway (watch #{wid}).")
    return (f"Watching {info['title']} — now {_money(info['price'], info['currency'])}. I'll tell you when it's "
            f"{_money(target_price)} or less (watch #{wid}, checked every {check_every_hours:g} h).")


@tool(group="prices")
def list_price_watches() -> str:
    """The products whose prices Nova is watching."""
    s = _db()
    with s.lock:
        rows = s.db.execute("SELECT * FROM price_watches WHERE status='watching' ORDER BY id").fetchall()
    if not rows:
        return "I'm not watching any prices."
    return "; ".join(f"#{r['id']} {r['title'][:60]} — now {_money(r['last_price'], r['currency'])}, target "
                     f"{_money(r['target'])}, lowest seen {_money(r['lowest'], r['currency'])}" for r in rows) + "."


@tool(group="prices")
def stop_price_watch(watch_id: int = 0) -> str:
    """Stop watching a price (by number), or all of them (0).
    Args:
        watch_id: the watch number, or 0 for all
    """
    s = _db()
    with s.lock:
        if watch_id:
            n = s.db.execute("UPDATE price_watches SET status='stopped' WHERE id=? AND status='watching'",
                             (int(watch_id),)).rowcount
        else:
            n = s.db.execute("UPDATE price_watches SET status='stopped' WHERE status='watching'").rowcount
        s.db.commit()
    return f"Stopped {n} price watch{'es' if n != 1 else ''}." if n else "Nothing to stop."


def check_due(now: dt.datetime | None = None) -> list[str]:
    """Check every watch that's due; returns the alerts sent (for tests)."""
    s = _db()
    now = now or dt.datetime.now()
    with s.lock:
        rows = s.db.execute("SELECT * FROM price_watches WHERE status='watching'").fetchall()
    alerts = []
    for r in rows:
        if r["checked"] and now - dt.datetime.fromisoformat(r["checked"]) < dt.timedelta(hours=r["every_hours"] or 6):
            continue
        try:
            info = price_of(r["url"])
        except Exception as e:
            print(f"[prices] #{r['id']}: {e}")
            continue
        p = info.get("price")
        low = min(x for x in (p, r["lowest"]) if x is not None) if (p or r["lowest"]) else None
        hit = p is not None and p <= r["target"] and (r["last_price"] is None or r["last_price"] > r["target"])
        with s.lock:
            s.db.execute("UPDATE price_watches SET last_price=?, lowest=?, checked=? WHERE id=?",
                         (p, low, now.isoformat(timespec="seconds"), r["id"]))
            s.db.commit()
        if hit:
            msg = (f"Price drop! {r['title'][:80]} is now {_money(p, r['currency'])} (your target "
                   f"{_money(r['target'])}). {r['url']}")
            context.push("💸 " + msg, [])
            if context.announce:
                context.announce(f"Price drop: {r['title'][:60]} is now {_money(p, r['currency'])}.")
            alerts.append(msg)
    return alerts


_thread: threading.Thread | None = None


def _loop() -> None:
    while True:
        time.sleep(90)               # let Nova finish starting first
        try:
            if context.store:
                check_due()
        except Exception as e:
            print(f"[prices] {e}")
        time.sleep(510)


def _start() -> None:
    global _thread
    if not (_thread and _thread.is_alive()):
        _thread = threading.Thread(target=_loop, daemon=True, name="prices")
        _thread.start()


_start()
