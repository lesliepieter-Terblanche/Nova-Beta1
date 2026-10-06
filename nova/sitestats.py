"""Visitors and searches for each business's website (v2.40), from Google's two free tools.

  Search Console   how often the site showed up in Google, how often it was clicked, and the searches people used
  Analytics (GA4)  visitors, visits, page views and the most-viewed pages

Both only know a site that has been added to them (and, for Analytics, has its tag on the site). Nova signs in
once with read-only access — a separate sign-in from Gmail, so the Gmail connection is never touched — and finds
the right property for each business by its web address. Numbers are this week against the week before.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from urllib.parse import quote, urlparse

from . import context
from .config import resolve

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly", "https://www.googleapis.com/auth/analytics.readonly"]
TOKEN = "secrets/token_sites.json"
_SCHEMA = "CREATE TABLE IF NOT EXISTS biz_stats(biz TEXT PRIMARY KEY, ts TEXT, data TEXT);"
_connecting = {"busy": False, "error": ""}


class NeedsYou(Exception):
    """Something only the owner can do; `.link` is where."""

    def __init__(self, msg: str, link: str = ""):
        super().__init__(msg)
        self.link = link


def _q(sql: str, args: tuple = ()) -> list[dict]:
    s = context.store
    with s.lock:
        s.db.executescript(_SCHEMA)
        cur = s.db.execute(sql, args)
        rows = [dict(r) for r in cur] if cur.description else []
        s.db.commit()
        return rows


def token_file():
    return resolve(TOKEN)


def connected() -> bool:
    return token_file().exists()


def _session():
    """A signed-in web session for Google's read-only reporting services."""
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(str(token_file()), SCOPES)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file().write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            raise NeedsYou("Google signed the website numbers out — press Connect again.") from e
    return AuthorizedSession(creds)


def connect() -> None:
    """Open Google's sign-in in the browser on this PC (read-only: Search Console and Analytics). Blocks until done."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    from .skills.google_ws import find_client_file
    secret = resolve(context.cfg.google.credentials_file)
    if not find_client_file(secret):
        raise NeedsYou("Connect Google in Settings → Google first — the website numbers use the same Google project.")
    creds = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES).run_local_server(
        port=0, access_type="offline", prompt="consent",
        success_message="Nova can now read your website numbers. You can close this tab.")
    token_file().parent.mkdir(parents=True, exist_ok=True)
    token_file().write_text(creds.to_json(), encoding="utf-8")


def connect_in_background() -> str:
    if _connecting["busy"]:
        return "The Google sign-in page is already open in your browser on the PC."

    def run():
        _connecting.update(busy=True, error="")
        try:
            connect()
        except Exception as e:
            _connecting["error"] = str(e)[:300]
        finally:
            _connecting["busy"] = False
    threading.Thread(target=run, daemon=True, name="sitestats-connect").start()
    return "Google's sign-in page is opening in the browser on your PC — choose your account and allow read-only access."


def disconnect() -> None:
    token_file().unlink(missing_ok=True)
    _q("DELETE FROM biz_stats")


def _call(sess, method: str, url: str, body: dict | None = None) -> dict:
    r = sess.request(method, url, json=body, timeout=30)
    if r.status_code == 403 and ("has not been used" in r.text or "is disabled" in r.text):
        link = re.search(r"https://console\.developers\.google\.com/[^\s\"']+", r.text)
        api = "Search Console" if "searchconsole" in url or "webmasters" in url else "Google Analytics"
        raise NeedsYou(f"The {api} service is switched off in your Google project. Open the link, press Enable, wait a "
                       "minute, then press Refresh here.", link.group(0).rstrip(".") if link else "")
    if r.status_code >= 400:
        raise RuntimeError(f"Google answered {r.status_code}: {r.text[:200]}")
    return r.json() if r.text.strip() else {}


def _host(url: str) -> str:
    return (urlparse(url if "//" in url else "//" + url).hostname or "").lower().removeprefix("www.")


def _change(now: float, before: float) -> int | None:
    return round(100 * (now - before) / before) if before else None


def search(sess, site_url: str, today: dt.date) -> dict | None:
    """Search Console for the site: this week against last, and the top searches. None = not added there."""
    host = _host(site_url)
    sites = _call(sess, "GET", "https://www.googleapis.com/webmasters/v3/sites").get("siteEntry", [])
    mine = [s["siteUrl"] for s in sites if s.get("permissionLevel") != "siteUnverifiedUser"
            and (s["siteUrl"] == f"sc-domain:{host}" or _host(s["siteUrl"]) == host)]
    if not mine:
        return None
    prop = sorted(mine, key=lambda s: not s.startswith("sc-domain:"))[0]
    end = today - dt.timedelta(days=3)                        # Search Console runs about three days behind
    base = f"https://www.googleapis.com/webmasters/v3/sites/{quote(prop, safe='')}/searchAnalytics/query"

    def total(a, z):
        rows = _call(sess, "POST", base, {"startDate": a.isoformat(), "endDate": z.isoformat()}).get("rows", [])
        r = rows[0] if rows else {}
        return {"clicks": int(r.get("clicks", 0)), "shown": int(r.get("impressions", 0)), "position": round(r.get("position", 0), 1)}
    now = total(end - dt.timedelta(days=6), end)
    before = total(end - dt.timedelta(days=13), end - dt.timedelta(days=7))
    top = _call(sess, "POST", base, {"startDate": (end - dt.timedelta(days=27)).isoformat(), "endDate": end.isoformat(),
                                     "dimensions": ["query"], "rowLimit": 8}).get("rows", [])
    return {**now, "clicks_change": _change(now["clicks"], before["clicks"]), "shown_change": _change(now["shown"], before["shown"]),
            "queries": [{"q": r["keys"][0], "clicks": int(r["clicks"]), "shown": int(r["impressions"]),
                         "position": round(r["position"], 1)} for r in top]}


def visitors(sess, site_url: str) -> dict | None:
    """Analytics (GA4) for the site: visitors, visits and page views this week against last, and the top pages.
    None = no Analytics property has this web address."""
    host = _host(site_url)
    prop = None
    for acc in _call(sess, "GET", "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200").get("accountSummaries", []):
        for p in acc.get("propertySummaries", []):
            streams = _call(sess, "GET", f"https://analyticsadmin.googleapis.com/v1beta/{p['property']}/dataStreams").get("dataStreams", [])
            if any(_host((s.get("webStreamData") or {}).get("defaultUri", "")) == host for s in streams):
                prop = p["property"]
                break
        if prop:
            break
    if not prop:
        return None
    base = f"https://analyticsdata.googleapis.com/v1beta/{prop}:runReport"
    rep = _call(sess, "POST", base, {"dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"},
                                                    {"startDate": "14daysAgo", "endDate": "8daysAgo"}],
                                     "metrics": [{"name": "activeUsers"}, {"name": "sessions"}, {"name": "screenPageViews"}]})
    vals = {0: [0, 0, 0], 1: [0, 0, 0]}
    for r in rep.get("rows", []):
        which = 1 if any(d.get("value") == "date_range_1" for d in r.get("dimensionValues", [])) else 0
        vals[which] = [int(float(m.get("value", 0))) for m in r.get("metricValues", [])]
    pages = _call(sess, "POST", base, {"dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
                                       "dimensions": [{"name": "pagePath"}], "metrics": [{"name": "screenPageViews"}],
                                       "limit": 6, "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}]})
    return {"visitors": vals[0][0], "visits": vals[0][1], "views": vals[0][2],
            "visitors_change": _change(vals[0][0], vals[1][0]), "views_change": _change(vals[0][2], vals[1][2]),
            "pages": [{"path": r["dimensionValues"][0]["value"], "views": int(float(r["metricValues"][0]["value"]))}
                      for r in pages.get("rows", [])]}


def refresh(b: dict, today: dt.date | None = None) -> dict:
    """Fetch a business's numbers from Google now and keep them."""
    data: dict = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "search": None, "visitors": None, "needs": []}
    try:
        sess = _session()
    except NeedsYou as e:
        data["needs"].append({"text": str(e), "link": e.link})
        sess = None
    for key, fn, missing in (
            ("search", lambda: search(sess, b["url"], today or dt.date.today()),
             f"Add {_host(b['url'])} to Google Search Console (free) to see the searches people use to find it."),
            ("visitors", lambda: visitors(sess, b["url"]),
             f"Add Google Analytics to {_host(b['url'])} (free) to see visitors and most-viewed pages.")):
        if sess is None:
            break
        try:
            data[key] = fn()
            if data[key] is None:
                data["needs"].append({"text": missing, "link": "https://search.google.com/search-console" if key == "search"
                                      else "https://analytics.google.com"})
        except NeedsYou as e:
            data["needs"].append({"text": str(e), "link": e.link})
        except Exception as e:
            data["needs"].append({"text": f"Couldn't read the {'search' if key == 'search' else 'visitor'} numbers: {str(e)[:160]}", "link": ""})
    _q("INSERT OR REPLACE INTO biz_stats(biz,ts,data) VALUES(?,?,?)", (b["id"], data["ts"], json.dumps(data)))
    return data


def stats(biz: str) -> dict:
    """What the dashboard shows (the saved numbers; `refresh` fetches new ones)."""
    out = {"connected": connected(), "connecting": _connecting["busy"], "error": _connecting["error"]}
    if out["connected"]:
        rows = _q("SELECT data FROM biz_stats WHERE biz=?", (biz,))
        if rows:
            out.update(json.loads(rows[0]["data"]))
    return out


def refresh_due(businesses: list[dict], hours: float = 12) -> int:
    if not connected():
        return 0
    n = 0
    for b in businesses:
        if not (b.get("url") or "").startswith("http"):
            continue
        rows = _q("SELECT ts FROM biz_stats WHERE biz=?", (b["id"],))
        if rows and (dt.datetime.now() - dt.datetime.fromisoformat(rows[0]["ts"])).total_seconds() < hours * 3600:
            continue
        try:
            refresh(b)
            n += 1
        except Exception as e:
            print(f"[sitestats] {b['name']}: {e}")
    return n
