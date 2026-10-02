"""Personal budget: slips, invoices and claims.

Photograph or upload a slip → Nova reads the shop, date, total and items, files the picture under
01_Personal / 04_Finance_&_Budgets / Slips (or Claims), and adds it to this month's spending. Claims (work expenses,
medical aid, insurance, tax) are tracked until they are paid back.

Everything stays on this PC. The picture is read by the vision model from Settings (Gemini when a key is saved,
otherwise the local one).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

from . import context, lessons, undo

CATEGORIES = ["Groceries", "Eating out", "Fuel", "Transport", "Home", "Utilities", "Health", "Clothing", "Entertainment",
              "Family", "Car", "Insurance", "Subscriptions", "Gifts", "Travel", "Electronics", "Other"]
CLAIM_STATES = ("to_claim", "submitted", "paid")
SLIP_WORDS = re.compile(r"\b(slip|receipt|till slip|invoice|claim|expense|strokie)\b", re.I)
CLAIM_WORDS = re.compile(r"\b(claim|claims|reimburse|reimbursement|expense claim|medical aid|for work)\b", re.I)
_SCHEMA = """CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, date TEXT, merchant TEXT, amount REAL,
  currency TEXT DEFAULT 'R', category TEXT, kind TEXT DEFAULT 'spend', claim_for TEXT DEFAULT '',
  claim_state TEXT DEFAULT '', note TEXT DEFAULT '', file TEXT DEFAULT '', items TEXT DEFAULT '', created TEXT);
CREATE TABLE IF NOT EXISTS budgets(category TEXT PRIMARY KEY, amount REAL);"""
READ_PROMPT = """This is a photo or scan of a till slip, receipt or invoice. Read it and reply with JSON only:
{{"is_slip": true or false, "merchant": "<shop or supplier name>", "date": "YYYY-MM-DD or empty",
"total": <the final amount paid, a number>, "currency": "R",
"category": "<one of: {cats}>", "items": ["<up to 8 main items, short>"]}}
Use the TOTAL / amount due, not a subtotal or the cash tendered. If it is not a slip or invoice, is_slip is false."""


def _db():
    s = context.store
    if s is None:
        raise RuntimeError("the brain isn't ready")
    with s.lock:
        s.db.executescript(_SCHEMA)
    return s


def category_named(name: str) -> str:
    n = name.strip().lower()
    alias = {"food": "Groceries", "grocery": "Groceries", "restaurant": "Eating out", "restaurants": "Eating out",
             "takeaway": "Eating out", "takeaways": "Eating out", "petrol": "Fuel", "diesel": "Fuel", "medical": "Health",
             "pharmacy": "Health", "kids": "Family", "children": "Family", "vehicle": "Car", "tech": "Electronics"}
    return next((c for c in CATEGORIES if c.lower() == n), "") or alias.get(n, "") or \
        next((c for c in CATEGORIES if n and n in c.lower()), "")


def money(x: float, cur: str = "R") -> str:
    return f"{cur}{x:,.2f}".replace(",", " ")


def read_slip(path: Path, llm=None) -> dict:
    """What the vision model makes of the picture."""
    llm = llm or context.llm
    raw = llm.see(str(path), READ_PROMPT.format(cats=", ".join(CATEGORIES)))
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    try:
        total = float(re.sub(r"[^\d.]", "", str(d.get("total", "")).replace(",", ".")) or 0)
    except ValueError:
        total = 0.0
    date = str(d.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        date = ""
    return {"is_slip": bool(d.get("is_slip", total > 0)), "merchant": str(d.get("merchant") or "").strip()[:60],
            "date": date, "total": round(total, 2), "currency": str(d.get("currency") or "R")[:3],
            "category": category_named(str(d.get("category") or "")) or "Other",
            "items": [str(i)[:60] for i in (d.get("items") or [])][:8]}


def _file_away(src: Path, date: str, merchant: str, amount: float, claim: bool, note_lines: list[str]) -> Path:
    """Keep the picture in the brain under Personal → Finance & Budgets, with a small note beside it."""
    from . import taxonomy
    base = taxonomy.folder_for(taxonomy.PERSONAL, "04_Finance_&_Budgets", "Claims" if claim else "Slips")
    stem = f"{date}_{taxonomy.clean_name(merchant or 'Slip', 30)}_R{amount:.0f}"
    img = taxonomy.unique(base / f"{stem}{src.suffix.lower()}")
    shutil.copy2(src, img)
    note = img.with_suffix(".md")
    status = "WAITING-ON-USER" if claim else "COMPLETED"
    note.write_text(f"{taxonomy.header(taxonomy.PERSONAL, status)}\n\n# {merchant or 'Slip'} — {money(amount)}\n\n"
                    + "\n".join(note_lines) + f"\n\n![[{img.name}]]\n", encoding="utf-8")
    try:
        context.store.index_note(note)
    except Exception as e:
        print(f"[budget] couldn't index {note.name}: {e}")
    return note


def add(amount: float, merchant: str = "", category: str = "", date: str = "", claim_for: str = "", note: str = "",
        file: str = "", items: list[str] | None = None, currency: str = "R", guessed: bool = False) -> dict:
    """guessed: the category came from reading the slip, so what you taught about this shop overrides it."""
    s = _db()
    date = date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or "") else dt.date.today().isoformat()
    learned = lessons.merchant(merchant)
    cat = category_named(category)
    if learned and (guessed or not cat or cat == "Other"):
        cat = learned                                # you corrected this shop before
    cat = cat or "Other"
    kind = "claim" if claim_for else "spend"
    with s.lock:
        cur = s.db.execute("INSERT INTO expenses(date, merchant, amount, currency, category, kind, claim_for, claim_state, "
                           "note, file, items, created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (date, merchant.strip(), round(float(amount), 2), currency, cat, kind, claim_for.strip(),
                            "to_claim" if claim_for else "", note, file, json.dumps(items or []),
                            dt.datetime.now().isoformat(timespec="seconds")))
        s.db.commit()
        eid = cur.lastrowid
    undo.record(f"added {money(amount)} at {merchant or 'a shop'} to your budget", "expense", id=eid)
    return get(eid)


def add_slip(path: str | Path, claim_for: str = "", note: str = "", llm=None) -> dict:
    """Read a slip picture, file it and add it to the budget. Returns the expense (or {"error": …})."""
    p = Path(path)
    if not p.is_file():
        return {"error": f"I can't find {p.name}."}
    try:
        got = read_slip(p, llm)
    except Exception as e:
        return {"error": f"I couldn't read the slip ({e})."}
    if not got["is_slip"] or got["total"] <= 0:
        return {"error": "I couldn't find a total on that picture — is it a slip or invoice? Tell me the amount and "
                         "shop and I'll add it by hand."}
    date = got["date"] or dt.date.today().isoformat()
    if not claim_for and CLAIM_WORDS.search(note or ""):
        claim_for = "medical aid" if re.search(r"medical", note, re.I) else "work"
    lines = [f"- Date: {date}", f"- Total: {money(got['total'], got['currency'])}", f"- Category: {got['category']}"]
    if claim_for:
        lines.append(f"- Claim: {claim_for} — to be claimed")
    if got["items"]:
        lines.append("- Items: " + ", ".join(got["items"]))
    if note:
        lines.append(f"- Note: {note}")
    filed = _file_away(p, date, got["merchant"], got["total"], bool(claim_for), lines)
    return add(got["total"], got["merchant"], got["category"], date, claim_for, note, str(filed), got["items"],
               got["currency"], guessed=True)


def get(eid: int) -> dict:
    s = _db()
    with s.lock:
        r = s.db.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
    return dict(r) if r else {}


def delete(eid: int) -> None:
    s = _db()
    with s.lock:
        s.db.execute("DELETE FROM expenses WHERE id=?", (eid,))
        s.db.commit()


def find(which: str) -> dict:
    """The expense meant by a number, a shop name, or 'last'."""
    s = _db()
    w = which.strip().lower()
    with s.lock:
        if w in ("", "last", "the last one", "that", "it", "latest"):
            r = s.db.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 1").fetchone()
        elif w.isdigit():
            r = s.db.execute("SELECT * FROM expenses WHERE id=?", (int(w),)).fetchone()
        else:
            r = s.db.execute("SELECT * FROM expenses WHERE lower(merchant) LIKE ? ORDER BY id DESC LIMIT 1",
                             (f"%{w}%",)).fetchone()
    return dict(r) if r else {}


def set_category(which: str, category: str) -> dict:
    e, cat = find(which), category_named(category)
    if not e or not cat:
        return {}
    s = _db()
    with s.lock:
        s.db.execute("UPDATE expenses SET category=? WHERE id=?", (cat, e["id"]))
        s.db.commit()
    if e["merchant"]:
        lessons.learn("merchant", e["merchant"], cat)        # next slip from this shop lands here by itself
    return get(e["id"])


def set_budget(category: str, amount: float) -> str:
    cat = category_named(category) or ("Total" if category.strip().lower() in ("total", "overall", "everything", "month") else "")
    if not cat:
        return ""
    s = _db()
    with s.lock:
        s.db.execute("INSERT OR REPLACE INTO budgets(category, amount) VALUES(?,?)", (cat, float(amount)))
        s.db.commit()
    return cat


def month_of(text: str = "") -> str:
    t = (text or "").strip().lower()
    today = dt.date.today()
    if t in ("", "this month", "now", "current"):
        return today.strftime("%Y-%m")
    if t in ("last month", "previous month"):
        return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    if re.fullmatch(r"\d{4}-\d{2}", t):
        return t
    names = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
             "november", "december"]
    for i, n in enumerate(names, 1):
        if t.startswith(n[:3]):
            year = today.year if i <= today.month else today.year - 1
            y = re.search(r"\d{4}", t)
            return f"{int(y.group(0)) if y else year}-{i:02d}"
    return today.strftime("%Y-%m")


def summary(month: str = "") -> dict:
    """Spending for a month: total, per category against its budget, and what is still to be claimed."""
    s = _db()
    m = month_of(month)
    with s.lock:
        rows = s.db.execute("SELECT category, SUM(amount) AS t, COUNT(*) AS n FROM expenses WHERE substr(date,1,7)=? "
                            "AND kind='spend' GROUP BY category ORDER BY t DESC", (m,)).fetchall()
        budgets = {r["category"]: r["amount"] for r in s.db.execute("SELECT * FROM budgets")}
        claims = s.db.execute("SELECT claim_state, SUM(amount) AS t, COUNT(*) AS n FROM expenses WHERE kind='claim' "
                              "AND claim_state!='paid' GROUP BY claim_state").fetchall()
    cats = [{"category": r["category"], "spent": round(r["t"], 2), "count": r["n"], "budget": budgets.get(r["category"])}
            for r in rows]
    for c, b in budgets.items():                         # budgeted but nothing spent yet
        if c != "Total" and c not in {x["category"] for x in cats}:
            cats.append({"category": c, "spent": 0.0, "count": 0, "budget": b})
    return {"month": m, "total": round(sum(c["spent"] for c in cats), 2), "budget": budgets.get("Total"),
            "categories": cats, "claims": {r["claim_state"]: {"total": round(r["t"], 2), "count": r["n"]} for r in claims}}


def summary_text(month: str = "") -> str:
    d = summary(month)
    name = dt.datetime.strptime(d["month"], "%Y-%m").strftime("%B %Y")
    if not d["categories"] and not d["claims"]:
        return f"Nothing recorded for {name} yet. Send me a photo of a slip and I'll start the month."
    out = [f"{name}: {money(d['total'])} spent" + (f" of your {money(d['budget'])} budget" if d["budget"] else "") + "."]
    for c in d["categories"][:10]:
        line = f"- {c['category']}: {money(c['spent'])}"
        if c["budget"]:
            left = c["budget"] - c["spent"]
            line += f" of {money(c['budget'])} — " + (f"{money(left)} left" if left >= 0 else f"{money(-left)} OVER")
        out.append(line)
    cl = d["claims"]
    if cl.get("to_claim"):
        out.append(f"Still to claim: {money(cl['to_claim']['total'])} ({cl['to_claim']['count']} slips).")
    if cl.get("submitted"):
        out.append(f"Claimed, waiting to be paid: {money(cl['submitted']['total'])} ({cl['submitted']['count']}).")
    return "\n".join(out)


def claims(state: str = "") -> list[dict]:
    s = _db()
    with s.lock:
        rows = s.db.execute("SELECT * FROM expenses WHERE kind='claim'" + (" AND claim_state=?" if state else
                            " AND claim_state!='paid'") + " ORDER BY date", (state,) if state else ()).fetchall()
    return [dict(r) for r in rows]


def set_claim(which: str, state: str) -> list[dict]:
    """Move claims along: which = a number, a shop, 'all', or what it is for ('work', 'medical aid')."""
    if state not in CLAIM_STATES:
        return []
    w = which.strip().lower()
    todo = [c for c in claims() if w in ("all", "everything", "them", "") or str(c["id"]) == w
            or w in c["merchant"].lower() or w in c["claim_for"].lower()]
    s = _db()
    from . import taxonomy
    for c in todo:
        with s.lock:
            s.db.execute("UPDATE expenses SET claim_state=? WHERE id=?", (state, c["id"]))
            s.db.commit()
        try:                                             # the note's status follows: waiting → in progress → done
            if c["file"] and Path(c["file"]).exists():
                taxonomy.set_labels(Path(c["file"]), {"to_claim": "WAITING-ON-USER", "submitted": "IN-PROGRESS",
                                                      "paid": "COMPLETED"}[state])
        except Exception as e:
            print(f"[budget] couldn't update the note's status: {e}")
    return todo


def export_claims(dest_dir: Path, claim_for: str = "") -> Path | None:
    """A spreadsheet (CSV) of what is still to be claimed, with the slip pictures in a folder next to it."""
    import csv
    rows = [c for c in claims("to_claim") if not claim_for or claim_for.lower() in c["claim_for"].lower()]
    if not rows:
        return None
    out = dest_dir / f"Claims_{dt.date.today():%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "claims.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Supplier", "Amount", "Category", "Claim for", "Note", "Slip"])
        for c in rows:
            pic = ""
            if c["file"]:
                for cand in Path(c["file"]).parent.glob(Path(c["file"]).stem + ".*"):
                    if cand.suffix.lower() != ".md":
                        shutil.copy2(cand, out / cand.name)
                        pic = cand.name
            w.writerow([c["date"], c["merchant"], f"{c['amount']:.2f}", c["category"], c["claim_for"], c["note"], pic])
        w.writerow(["", "TOTAL", f"{sum(c['amount'] for c in rows):.2f}"])
    return out
