"""The public skills library from github.com/anthropics/skills, turned into Nova playbooks.

A "skill" there is a folder with a SKILL.md: plain-English instructions for doing one kind of job well (designing
a web page, writing internal comms, building a theme…). Nova's playbooks are the same idea, so each open-source
skill becomes playbooks/library/<name>.md, and the skill's full text and reference files go to
workspace/skill-library/<name>/ where Nova can read them.

Licences: only skills whose folder carries the Apache-2.0 licence are imported. The document skills (docx, pdf,
pptx, xlsx) are "source-available" — Anthropic doesn't allow them to be copied — so they are left out, as is
anything without a licence file. Nothing from the library is stored in Nova's own repository: it is downloaded
onto this PC, and playbooks/library/ is git-ignored.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path

import yaml

from .config import ROOT, resolve

ZIP_URL = "https://github.com/anthropics/skills/archive/refs/heads/main.zip"
GIT_URL = "https://github.com/anthropics/skills"
SOURCE = "https://github.com/anthropics/skills"
BODY_LIMIT = 2300                 # a playbook's steps are trimmed to 3 000 characters in Nova's prompt

# How each skill is wired into Nova: the phrases that call it up, the tool groups it needs, and whether it is on.
# Off = written for Claude's own apps and of little use here; switch it on in Settings → Extensions if you want it.
KNOWN = {
    "frontend-design": (["frontend design", "design a website", "design the website", "landing page", "web design",
                         "ui design", "make the site look", "redesign the site"], ["web", "files"], True),
    "internal-comms": (["internal comms", "status report", "leadership update", "company newsletter",
                        "internal announcement", "incident report", "project update for", "3p update"],
                       ["files", "brain"], True),
    "theme-factory": (["theme factory", "colour theme", "color theme", "theme for the", "pick a theme"],
                      ["files"], True),
    "canvas-design": (["canvas design", "design a poster", "design a flyer", "poster design", "visual design for"],
                      ["media", "files"], True),
    "algorithmic-art": (["algorithmic art", "generative art", "p5.js", "flow field"], ["files", "web"], True),
    "mcp-builder": (["mcp builder", "build an mcp server", "write an mcp server", "create an mcp server"],
                    ["files"], True),
    "webapp-testing": (["webapp testing", "test the web app", "test the website", "test my site"],
                       ["browser", "web"], True),
    "doc-coauthoring": (["doc coauthoring", "co-author", "coauthor", "write a spec", "write a proposal",
                         "draft a document with me"], ["files", "brain"], True),
    "skill-creator": (["skill creator", "create a skill", "write a playbook", "new playbook"], ["files"], False),
    "brand-guidelines": (["anthropic brand"], ["files"], False),
    "slack-gif-creator": (["slack gif"], ["files"], False),
    "web-artifacts-builder": (["web artifact"], ["files", "web"], False),
    "claude-api": (["claude api", "anthropic sdk"], ["files"], False),
    "academy-guide": (["academy guide"], ["files"], False),
    "discernment-nudge": (["discernment nudge"], ["files"], False),
}
PREFACE = ("This guide comes from Anthropic's public skills library and was written for Claude. Follow its method "
           "with YOUR tools (write_file, read_file, build_website, generate_image, web_search…). Skip any step that "
           "needs a tool you don't have, and say so plainly instead of pretending.")


def library_dir() -> Path:
    return ROOT / "playbooks" / "library"


def files_dir() -> Path:
    return resolve("workspace/skill-library")


def _manifest() -> Path:
    return library_dir() / "library.json"


def installed() -> dict:
    try:
        return json.loads(_manifest().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def is_apache(text: str) -> bool:
    return "apache license" in text[:400].lower() and "version 2.0" in text[:400].lower()


def split_skill(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), m.group(2)


def to_playbook(name: str, meta: dict, body: str, full_path: Path) -> str:
    """A Nova playbook from a SKILL.md: Nova's own header, a preface, and as much of the guide as fits."""
    triggers, groups, _ = KNOWN.get(name, ([name.replace("-", " ")], ["files"], True))
    triggers = list(dict.fromkeys([f"use the {name.replace('-', ' ')} skill", *triggers]))
    desc = " ".join(str(meta.get("description") or name).split())
    body = re.sub(r"\n{3,}", "\n\n", body.strip())
    if len(body) > BODY_LIMIT:
        cut = body[:BODY_LIMIT]
        cut = cut[:max(cut.rfind("\n\n"), cut.rfind(". ") + 1, BODY_LIMIT - 400)].rstrip()
        body = (f"{cut}\n\n…The guide continues. Read the whole of it with read_file before a big job: "
                f"{full_path.as_posix()}")
    head = yaml.safe_dump({"name": f"lib-{name}", "description": desc[:300], "triggers": triggers,
                           "tool_groups": groups, "source": f"{SOURCE}/tree/main/skills/{name}",
                           "licence": "Apache-2.0"}, sort_keys=False, allow_unicode=True, width=1000).strip()
    return f"---\n{head}\n---\n{PREFACE}\n\n{body}\n"


def import_zip(data: bytes) -> dict:
    """Unpack the library's zip into playbooks + reference files. Returns {"added": [...], "skipped": {name: why}}."""
    added, skipped, off = [], {}, []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        root = names[0].split("/")[0] + "/skills/"
        skills = sorted({n[len(root):].split("/")[0] for n in names if n.startswith(root) and n != root} - {""})
        lib, files = library_dir(), files_dir()
        if lib.exists():
            shutil.rmtree(lib)
        lib.mkdir(parents=True, exist_ok=True)
        for name in skills:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", name):
                continue
            base = root + name + "/"
            if base + "SKILL.md" not in names:
                continue
            lic = next((n for n in names if n.startswith(base) and n[len(base):].lower() in
                        ("license.txt", "license", "license.md")), "")
            if not lic:
                skipped[name] = "no licence file"
                continue
            if not is_apache(z.read(lic).decode("utf-8", "replace")):
                skipped[name] = "not open source (Anthropic doesn't allow copying it)"
                continue
            dest = files / name
            if dest.exists():
                shutil.rmtree(dest)
            for n in names:                               # the whole folder: SKILL.md, licence, references
                rel = n[len(base):] if n.startswith(base) else ""
                if not rel or n.endswith("/") or ".." in rel or z.getinfo(n).file_size > 5_000_000:
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(n))
            meta, body = split_skill((dest / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            (lib / f"{name}.md").write_text(to_playbook(name, meta, body, dest / "SKILL.md"), encoding="utf-8")
            added.append(name)
            if not KNOWN.get(name, ([], [], True))[2]:
                off.append(f"lib-{name}")
    _manifest().write_text(json.dumps({"source": SOURCE, "skills": added, "skipped": skipped, "off": off}, indent=1),
                           encoding="utf-8")
    return {"added": added, "skipped": skipped, "off": off}


def download() -> bytes:
    """The library as a zip: straight from GitHub, or — where that is blocked — through git."""
    import httpx
    try:
        r = httpx.get(ZIP_URL, timeout=180, follow_redirects=True)
        r.raise_for_status()
        if r.content[:2] == b"PK":
            return r.content
        raise ValueError("GitHub didn't send a zip")
    except Exception as e:
        print(f"[library] zip download failed ({e}); trying git")
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = subprocess.run(["git", "clone", "--depth", "1", "--quiet", GIT_URL, str(Path(td) / "skills-main")],
                           capture_output=True, text=True, timeout=300)
        if p.returncode != 0:
            raise RuntimeError(f"couldn't fetch the library: {p.stderr.strip()[-200:]}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted((Path(td) / "skills-main").rglob("*")):
                rel = f.relative_to(td).as_posix()
                if f.is_file() and "/.git/" not in f"/{rel}/":
                    z.write(f, rel)
        return buf.getvalue()


def install() -> dict:
    """Download the library from GitHub and import it. The Claude-only skills start switched off."""
    first = not installed()
    res = import_zip(download())
    if first and res["off"]:                               # only the first time: afterwards the choice is yours
        try:
            from . import settings
            with settings._lock:
                doc = settings.load_doc()
                cur = list(settings.get_path(doc, "playbooks.disabled", []) or [])
                new = [n for n in res["off"] if n not in cur]
                if new:
                    settings.set_path(doc, "playbooks.disabled", cur + new)
                    settings.save_doc(doc)
            from . import context
            if context.cfg is not None:                    # …and for this run, without a restart
                context.cfg.setdefault("playbooks", {})["disabled"] = cur + new
        except Exception as e:
            print(f"[library] couldn't switch the Claude-only skills off: {e}")
    from .extensions import load_playbooks
    load_playbooks()
    return res
