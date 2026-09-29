"""Sorting the 2nd brain: which project does each memory, note, creation, mission and action belong to?

Each item is linked to its best-matching project using
  1. names — the project's distinctive words (Nova, TrueHome, Juniper, Avaya…) appearing in the item, weighted so
     a word shared by several projects counts less, and
  2. meaning — embedding similarity (when Ollama's embeddings are available),
  3. your own choice — anything you link or unlink by hand in the dashboard always wins.
Projects can belong to other projects (e.g. every "Nova upgrade: …" roadmap item sits under the Nova project).
"""
from __future__ import annotations

import math
import re

import numpy as np

STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "his", "her", "their", "our", "your", "its", "has",
    "have", "was", "were", "are", "will", "would", "should", "could", "about", "after", "before", "when", "where",
    "what", "which", "who", "how", "why", "all", "any", "each", "every", "new", "old", "project", "projects", "plan",
    "plans", "goal", "goals", "idea", "note", "notes", "task", "tasks", "mission", "report", "i", "a", "an", "it",
    "is", "in", "on", "of", "to", "at", "by", "or", "as", "be", "we", "he", "she", "they", "mr", "mrs", "ms", "dr",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "today", "tomorrow", "yesterday", "week",
    "q1", "q2", "q3", "q4", "fy", "ok", "yes", "no", "upgrade", "build", "building", "website", "site", "app",
    "south", "african", "africa", "local", "first", "voice", "agent", "dashboard", "platform",
    # verbs that start sentences ("Service the XUV500…", "Call Sipho…") aren't names
    "service", "buy", "call", "email", "send", "remember", "make", "get", "check", "review", "prepare", "meet", "book",
    "finish", "start", "update", "fix", "find", "create", "write", "read", "pay", "order", "research", "draft",
    "follow", "ask", "tell", "visit", "add", "remove", "set", "need", "needs", "want", "wants", "use", "try", "test",
    "please", "also", "then", "next", "maybe", "remind", "schedule", "cancel", "move", "share", "open", "close",
}
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]*[A-Za-z0-9]|[A-Za-z]{3,}")


def keywords(text: str, owner: str = "") -> list[str]:
    """Distinctive names in a text: capitalised words, product codes (XUV500), domains (truehome.co.za → truehome)."""
    out: list[str] = []
    skip = {w.lower() for w in re.findall(r"\w+", owner or "")}
    for raw in _WORD.findall(text or ""):
        w = raw.strip(".-")
        low = w.lower()
        if "." in low:                                  # domain or file name
            low = low.split(".")[0]
            w = low
        low = low.removesuffix("'s")
        if len(low) < 3 or low in STOP or low in skip:
            continue
        distinctive = w[:1].isupper() or any(c.isdigit() for c in w) or "." in raw or \
            (sum(c.isupper() for c in w) >= 2)
        if distinctive and low not in out:
            out.append(low)
    return out


def short_title(text: str, words: int = 5) -> str:
    t = re.sub(r"\s*\([^)]*\)", "", (text or "")).strip()
    for sep in (" — ", " - ", ": ", " is ", " are ", " was ", ", ", ". "):
        if sep in t:
            head = t.split(sep)[0].strip()
            if sep == ": " and " — " in t:              # "Nova upgrade: Gesture control — …"
                head = t.split(" — ")[0].strip()
            if 2 <= len(head) <= 60:
                t = head
                break
    parts = t.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _contains(text_low: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text_low) is not None


def _cos(a, b) -> float:
    if a is None or b is None or len(a) != len(b):
        return 0.0
    return float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))


def organize(projects: list[dict], items: list[dict], manual: dict | None = None, owner: str = "",
             min_sim: float = 0.6) -> dict:
    """projects/items: [{"id", "text", "vec" (np.array|None), "created"?, "source"?}]
    manual: {item_id: project_id or ""}  ("" = you said: no project)
    Returns {"item_project": {item_id: project_id}, "parents": {project_id: parent_project_id},
             "why": {item_id: "name 'truehome'" | "meaning 0.71" | "you linked it"}, "short": {project_id: title}}"""
    manual = manual or {}
    kw = {p["id"]: keywords(p["text"], owner) for p in projects}
    df: dict[str, int] = {}
    for words in kw.values():
        for w in set(words):
            df[w] = df.get(w, 0) + 1
    name = {pid: (words[0] if words else "") for pid, words in kw.items()}

    # sub-projects: a project whose text names another project (roadmap "Nova upgrade: …" → the Nova project)
    parents: dict[str, str] = {}
    by_created = sorted(projects, key=lambda p: (str(p.get("source", "")).startswith("roadmap:"), p.get("created") or ""))
    for p in projects:
        low = p["text"].lower()
        for q in by_created:
            if q["id"] == p["id"] or not name[q["id"]] or parents.get(q["id"]) == p["id"]:
                continue
            same_name = name[q["id"]] == name[p["id"]]
            if same_name and by_created.index(q) > by_created.index(p):
                continue                                  # the older / non-roadmap one is the parent
            if _contains(low, name[q["id"]]) and (same_name or df.get(name[q["id"]], 1) == 1):
                parents[p["id"]] = q["id"]
                break

    item_project, why = {}, {}
    for it in items:
        iid = it["id"]
        if iid in manual:
            if manual[iid]:
                item_project[iid], why[iid] = manual[iid], "you linked it"
            else:
                why[iid] = "you removed it"
            continue
        low = (it.get("text") or "").lower()
        best, best_score, best_why = None, 0.0, ""
        for p in projects:
            score, hit = 0.0, ""
            for w in kw[p["id"]]:
                if _contains(low, w):
                    weight = 1.0 / df[w] + (0.5 if w == name[p["id"]] else 0.0)
                    score += weight
                    hit = hit or w
            sim = _cos(it.get("vec"), p.get("vec"))
            if sim >= min_sim:
                score += (sim - min_sim) * 5 + 0.3
            if score > best_score:
                reason = f"name '{hit}'" if hit else f"meaning {sim:.2f}"
                best, best_score, best_why = p["id"], score, reason
        if best and best_score >= 0.3:
            # prefer the most specific project: if the winner has sub-projects that tie, keep the winner
            item_project[iid], why[iid] = best, best_why
    return {"item_project": item_project, "parents": parents, "why": why,
            "short": {p["id"]: short_title(p["text"]) for p in projects}}


def palette(n: int) -> list[str]:
    """Distinct, bright project colours (golden-angle hues)."""
    out = []
    for i in range(n):
        h = (i * 137.508) % 360
        out.append(_hsl(h, 0.78, 0.66))
    return out


def _hsl(h: float, s: float, lum: float) -> str:
    c = (1 - abs(2 * lum - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = lum - c / 2
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return "#" + "".join(f"{int(round((v + m) * 255)):02x}" for v in (r, g, b))


_ = math
