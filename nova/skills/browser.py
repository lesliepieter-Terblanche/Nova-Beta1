"""Browser control with Playwright (free, local).

Nova drives its own Chromium/Chrome window with a persistent profile, so
anything you log into once stays logged in. Playwright objects must stay on
one thread, so every browser action runs on a dedicated worker thread.

How the model uses it: browser_read() lists the clickable/typeable elements
with numbers; then browser_click(3) or browser_type(5, "hello").
"""
from __future__ import annotations

import datetime as dt
import queue
import threading

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("browser", ["browser", "chrome", "click", "log in", "login", "sign in", "fill in", "form",
                           "tab", "navigate", "go to", "on the page", "scroll", "button", "facebook", "linkedin",
                           "marketplace", "gumtree", "takealot", "book", "order", "checkout", "portal"])

MARK_JS = r"""
() => {
  document.querySelectorAll('[data-nova]').forEach(e => e.removeAttribute('data-nova'));
  const sel = 'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [role=tab], [contenteditable=true]';
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.top > innerHeight * 2.5) continue;
    const st = getComputedStyle(el); if (st.visibility === 'hidden' || st.display === 'none') continue;
    n++; el.setAttribute('data-nova', n);
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder')
                   || el.getAttribute('title') || el.name || el.getAttribute('href') || '').trim().replace(/\s+/g, ' ').slice(0, 70);
    const kind = el.tagName === 'A' ? 'link' : el.tagName === 'INPUT' ? 'input:' + (el.type || 'text')
               : el.tagName.toLowerCase();
    out.push(`[${n}] ${kind} "${label}"`);
    if (n >= 120) break;
  }
  return out;
}
"""


class _Browser:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.ctx = None
        self.page = None

    def call(self, fn, *args, timeout=90):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._worker, daemon=True, name="browser")
            self.thread.start()
        done = queue.Queue()
        self.q.put((fn, args, done))
        ok, val = done.get(timeout=timeout)
        if not ok:
            raise val
        return val

    def _worker(self):
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        profile = resolve("data/browser_profile")
        profile.mkdir(parents=True, exist_ok=True)
        opts = dict(headless=False, viewport=None, args=["--start-maximized"])
        try:   # prefer the real Chrome if installed, else Playwright's Chromium
            self.ctx = pw.chromium.launch_persistent_context(str(profile), channel="chrome", **opts)
        except Exception:
            self.ctx = pw.chromium.launch_persistent_context(str(profile), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        while True:
            fn, args, done = self.q.get()
            try:
                if self.page.is_closed():
                    self.page = self.ctx.pages[-1] if self.ctx.pages else self.ctx.new_page()
                done.put((True, fn(self, *args)))
            except Exception as e:
                done.put((False, e))

    # actions (run on the worker thread)
    def _snapshot(self, _=None) -> str:
        p = self.page
        p.wait_for_load_state("domcontentloaded", timeout=15000)
        elements = p.evaluate(MARK_JS)
        text = p.evaluate("() => document.body ? document.body.innerText : ''")
        text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())[:3500]
        return f"PAGE: {p.title()}\nURL: {p.url}\n\nTEXT:\n{text}\n\nELEMENTS:\n" + "\n".join(elements)


_B = _Browser()


@tool(group="browser")
def browser_open(url: str) -> str:
    """Open a web page in Nova's controllable browser and return its text and numbered elements.
    Args:
        url: address to open
    """
    if not url.startswith("http"):
        url = "https://" + url

    def go(b):
        b.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return b._snapshot()
    return _B.call(go)


@tool(group="browser")
def browser_read() -> str:
    """Read the current page: its text plus a numbered list of links, buttons and inputs."""
    return _B.call(lambda b: b._snapshot())


@tool(group="browser")
def browser_click(element: int) -> str:
    """Click an element by its number from browser_read / browser_open.
    Args:
        element: the element number
    """
    def click(b):
        b.page.locator(f'[data-nova="{int(element)}"]').first.click(timeout=10000)
        b.page.wait_for_timeout(1200)
        return b._snapshot()
    return _B.call(click)


@tool(group="browser")
def browser_type(element: int, text: str, press_enter: bool = False) -> str:
    """Type into an input field by its element number.
    Args:
        element: the element number
        text: what to type
        press_enter: press Enter afterwards (e.g. to search)
    """
    def typ(b):
        loc = b.page.locator(f'[data-nova="{int(element)}"]').first
        loc.fill(text, timeout=10000)
        if press_enter:
            loc.press("Enter")
            b.page.wait_for_timeout(1500)
        return b._snapshot()
    return _B.call(typ)


@tool(group="browser", confirm=True)
def browser_submit(element: int, what_it_does: str) -> str:
    """Click a button that submits, sends, posts, buys or books something. Always use this (not browser_click)
    for irreversible actions so the user can confirm.
    Args:
        element: the element number of the button
        what_it_does: short description, e.g. "post the listing" or "pay R499"
    """
    return browser_click(element)


@tool(group="browser")
def browser_scroll(direction: str = "down") -> str:
    """Scroll the page up or down.
    Args:
        direction: down or up
    """
    def sc(b):
        b.page.mouse.wheel(0, 900 if direction == "down" else -900)
        b.page.wait_for_timeout(600)
        return b._snapshot()
    return _B.call(sc)


@tool(group="browser")
def browser_back() -> str:
    """Go back to the previous page."""
    def back(b):
        b.page.go_back(timeout=15000)
        return b._snapshot()
    return _B.call(back)


@tool(group="browser")
def browser_press(key: str) -> str:
    """Press a keyboard key in the browser, e.g. Enter, Escape, Tab, PageDown.
    Args:
        key: key name
    """
    def press(b):
        b.page.keyboard.press(key)
        b.page.wait_for_timeout(600)
        return b._snapshot()
    return _B.call(press)


@tool(group="browser")
def browser_screenshot() -> str:
    """Screenshot the browser page and send it to the user's phone."""
    out = resolve("workspace/screenshots") / f"browser_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    _B.call(lambda b: b.page.screenshot(path=str(out)))
    context.attach(out)
    context.record("image", out.name, out, "browser screenshot")
    return f"Screenshot saved to {out}."


@tool(group="browser")
def browser_look(question: str = "What is on this page and what can I do here?") -> str:
    """Look at the browser page visually (Gemini vision) — useful for images, charts or confusing layouts.
    Args:
        question: what to find out
    """
    out = resolve("workspace/screenshots") / "browser_look.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    _B.call(lambda b: b.page.screenshot(path=str(out)))
    return context.llm.see(str(out), question)
