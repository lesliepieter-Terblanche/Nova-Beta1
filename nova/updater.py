"""GitHub updates and rollback, using plain git.

- update:   fetch from GitHub and fast-forward to the latest commit on your branch
- rollback: jump back to any earlier version (tag or commit); your uncommitted edits are stashed first
Every update records the commit you were on, so "roll back" always has somewhere safe to go.
"""
from __future__ import annotations

import json
import subprocess
import sys

from .config import ROOT

HISTORY = ROOT / "data" / "version_history.json"
RESTART_CODE = 42          # run.bat restarts Nova when it exits with this code


def git(*args, check=True) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip())
    return p.stdout.strip()


def is_repo() -> bool:
    try:
        git("rev-parse", "--is-inside-work-tree")
        return True
    except Exception:
        return False


def current() -> str:
    desc = git("describe", "--tags", "--always", check=False)
    return f"{desc} ({git('rev-parse', '--short', 'HEAD')})"


def branch() -> str:
    b = git("rev-parse", "--abbrev-ref", "HEAD")
    return "main" if b == "HEAD" else b


def _record(entry: dict) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    items = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    items.append(entry)
    HISTORY.write_text(json.dumps(items[-50:], indent=1))


def _stash_if_dirty(reason: str) -> str:
    if git("status", "--porcelain"):
        git("stash", "push", "-u", "-m", f"nova-auto-stash before {reason}")
        return " Your unsaved local edits were stashed (recover with: git stash pop)."
    return ""


def check() -> dict:
    """{'behind': n, 'latest': 'message', 'current': ...}"""
    if not is_repo():
        return {"error": "Nova isn't a git repository yet. Run setup_github.bat."}
    git("fetch", "--tags", "--quiet", "origin")
    b = branch()
    behind = int(git("rev-list", "--count", f"HEAD..origin/{b}") or 0)
    latest = git("log", "-1", "--format=%s (%cr)", f"origin/{b}") if behind else ""
    return {"behind": behind, "latest": latest, "current": current()}


def update() -> str:
    info = check()
    if "error" in info:
        return info["error"]
    if not info["behind"]:
        return f"Already up to date ({info['current']})."
    before = git("rev-parse", "HEAD")
    req_before = (ROOT / "requirements.txt").read_text()
    note = _stash_if_dirty("update")
    b = branch()
    git("checkout", b, check=False)
    git("merge", "--ff-only", f"origin/{b}")
    _record({"action": "update", "from": before, "to": git("rev-parse", "HEAD")})
    if (ROOT / "requirements.txt").read_text() != req_before:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "requirements.txt")], cwd=ROOT)
    return f"Updated to {current()}.{note} Restart Nova to use it."


def versions(limit: int = 10) -> list[str]:
    tags = git("tag", "--sort=-creatordate", check=False).splitlines()[:limit]
    commits = git("log", f"-{limit}", "--format=%h %s (%cr)").splitlines()
    return [f"tag {t}" for t in tags] + commits


def rollback(target: str = "previous") -> str:
    if not is_repo():
        return "Nova isn't a git repository yet."
    if target == "previous":
        hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
        target = hist[-1]["from"] if hist else "HEAD~1"
    try:
        git("rev-parse", "--verify", target)
    except RuntimeError:
        return f"There's no version '{target}' to roll back to. Say 'list versions' to see what exists."
    before = git("rev-parse", "HEAD")
    note = _stash_if_dirty("rollback")
    git("reset", "--hard", target)
    _record({"action": "rollback", "from": before, "to": git("rev-parse", "HEAD")})
    return f"Rolled back to {current()}.{note} Restart Nova to use it. (Update again any time to return to the latest.)"


if __name__ == "__main__":      # used by update.bat / rollback.bat
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        print(check())
    elif cmd == "update":
        print(update())
    elif cmd == "versions":
        print("\n".join(versions(20)))
    elif cmd == "rollback":
        print(rollback(sys.argv[2] if len(sys.argv) > 2 else "previous"))
