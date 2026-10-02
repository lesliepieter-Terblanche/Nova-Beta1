"""Personal budget: slips, spending, budgets and claims (engine in nova/budget.py)."""
from __future__ import annotations

from pathlib import Path

from .. import budget as bg
from .. import context
from ..tools import register_group, tool

register_group("budget", ["slip", "receipt", "invoice", "budget", "spending", "spent", "spend", "expense", "claim",
                          "reimburse", "how much did i", "how much have i", "groceries", "fuel", "petrol"])


def _card(title: str, text: str) -> None:
    try:
        from .. import cards
        cards.show("info", title, {"text": text})
    except Exception:
        pass


def _line(e: dict) -> str:
    claim = f", to claim from {e['claim_for']}" if e.get("claim_for") else ""
    return f"{bg.money(e['amount'], e.get('currency') or 'R')} at {e['merchant'] or 'a shop'} on {e['date']} ({e['category']}{claim})"


@tool(group="budget")
def add_slip(path: str = "", claim_for: str = "", note: str = "") -> str:
    """Read a photo or scan of a slip / receipt / invoice, file it under Personal → Finance & Budgets and add it to
    the budget. Empty path = the newest picture Nova has (e.g. the one just uploaded or photographed).
    Args:
        path: the picture or PDF; empty = the latest one
        claim_for: who it will be claimed from — work, medical aid, insurance, tax…; empty = ordinary spending
        note: anything to remember about it
    """
    if not path.strip():
        from .files import _latest_file
        path = _latest_file()
        if not path:
            return "Which slip? Send me the photo (or drop it on the dashboard) and say it's a slip."
    e = bg.add_slip(Path(path).expanduser(), claim_for, note)
    if e.get("error"):
        return e["error"]
    month = bg.summary(e["date"][:7])
    return (f"Slip filed: {_line(e)}. That makes {bg.money(month['total'])} this month. If the category is wrong, "
            "tell me and I'll remember it for that shop.")


@tool(group="budget")
def add_expense(amount: float, merchant: str = "", category: str = "", date: str = "", claim_for: str = "") -> str:
    """Add spending by hand when there is no slip.
    Args:
        amount: how much
        merchant: where
        category: Groceries, Eating out, Fuel, Transport, Home, Utilities, Health, Clothing, Entertainment, Family,
                  Car, Insurance, Subscriptions, Gifts, Travel, Electronics or Other
        date: YYYY-MM-DD; empty = today
        claim_for: work, medical aid, insurance, tax…; empty = ordinary spending
    """
    if amount <= 0:
        return "How much was it?"
    return "Added: " + _line(bg.add(amount, merchant, category, date, claim_for)) + "."


@tool(group="budget")
def spending(month: str = "") -> str:
    """How much was spent in a month, per category against the budget, and what is still to be claimed.
    Args:
        month: "this month", "last month", a month name or YYYY-MM
    """
    text = bg.summary_text(month)
    try:
        import datetime as dt

        from .. import cards
        d = bg.summary(month)
        cards.show("budget", "Your spending", {**d, "month_name": dt.datetime.strptime(d["month"], "%Y-%m").strftime("%B %Y")})
    except Exception:
        _card("Your spending", text)
    return text


@tool(group="budget")
def set_budget(category: str, amount: float) -> str:
    """Set the monthly budget for a category, or for the whole month with category "total".
    Args:
        category: a spending category, or "total"
        amount: per month
    """
    cat = bg.set_budget(category, amount)
    return f"Budget for {cat}: {bg.money(amount)} a month." if cat else \
        f"I don't have a category called '{category}'. The categories are: {', '.join(bg.CATEGORIES)}."


@tool(group="budget")
def fix_expense_category(which: str, category: str) -> str:
    """Correct the category of an expense — Nova then uses it for that shop from now on.
    Args:
        which: "last", the shop's name, or the expense number
        category: the right category
    """
    e = bg.set_category(which, category)
    if not e:
        return "I couldn't find that expense or that category."
    return f"Changed to {e['category']}. Slips from {e['merchant'] or 'that shop'} will go there from now on."


@tool(group="budget")
def list_claims() -> str:
    """The slips still to be claimed or waiting to be paid back."""
    rows = bg.claims()
    if not rows:
        return "Nothing is waiting to be claimed."
    text = "\n".join(f"{c['id']}. {c['date']} {c['merchant']} {bg.money(c['amount'])} — {c['claim_for']} "
                     f"({'to claim' if c['claim_state'] == 'to_claim' else 'claimed, not yet paid'})" for c in rows)
    total = sum(c["amount"] for c in rows if c["claim_state"] == "to_claim")
    text += f"\nStill to claim: {bg.money(total)}."
    _card("Your claims", text)
    return text


@tool(group="budget")
def update_claim(which: str, state: str) -> str:
    """Mark claims as submitted or paid back.
    Args:
        which: a number, a shop, what it is for ("work", "medical aid") or "all"
        state: submitted | paid | to_claim
    """
    state = {"claimed": "submitted", "sent": "submitted", "paid back": "paid", "reimbursed": "paid"}.get(state.lower().strip(),
                                                                                                    state.lower().strip())
    done = bg.set_claim(which, state)
    if not done:
        return "I didn't find a claim like that (or that isn't a state I know: submitted, paid)."
    return f"Marked {len(done)} claim{'s' if len(done) != 1 else ''} ({bg.money(sum(c['amount'] for c in done))}) as {state.replace('_', ' ')}."


@tool(group="budget")
def export_claims(claim_for: str = "") -> str:
    """Make a folder with a spreadsheet of everything still to be claimed plus the slip pictures, ready to submit.
    Args:
        claim_for: only these, e.g. "work" or "medical aid"; empty = all
    """
    from ..config import resolve
    out = bg.export_claims(resolve("workspace/claims"), claim_for)
    if not out:
        return "Nothing is waiting to be claimed."
    context.record("document", out.name, out / "claims.csv", "claims")
    context.attach(out / "claims.csv")
    return f"Your claim pack is in {out}: claims.csv plus the slips. Say 'mark the claims as submitted' once you've sent it."
