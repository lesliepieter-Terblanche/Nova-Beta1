"""Example plugin: live currency conversion (free, no API key — frankfurter.app, ECB rates).

Any .py file in this folder is loaded at startup. Delete this file if you don't want it.
"""
import httpx

from nova.tools import register_group, tool

register_group("currency", ["convert", "exchange rate", "rand", "zar", "dollar", "usd", "euro", "eur", "pound", "gbp"])


@tool(group="currency")
def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert money between currencies at today's reference rate.
    Args:
        amount: the amount
        from_currency: 3-letter code, e.g. USD
        to_currency: 3-letter code, e.g. ZAR
    """
    f, t = from_currency.upper(), to_currency.upper()
    r = httpx.get("https://api.frankfurter.app/latest", params={"amount": amount, "from": f, "to": t}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return f"{amount:,.2f} {f} = {data['rates'][t]:,.2f} {t} (rate date {data['date']})"
