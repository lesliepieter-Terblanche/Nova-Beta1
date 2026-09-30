"""File and folder management — restricted to the folders listed in config.yaml."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .. import context
from ..config import resolve
from ..tools import register_group, tool

register_group("files", ["file", "folder", "document", "desktop", "download", "pdf", "docx", "pptx", "powerpoint",
                         "slides", "table", "markdown", "convert", "move", "copy",
                         "delete", "rename", "organi", "tidy", "clean up", "find my", "where is", "open the",
                         "read the", "save", "spreadsheet", "zip", "send me", "phone"])

TYPES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".svg"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".ppt", ".pptx"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Videos": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "Audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi"},
}


def roots() -> list[Path]:
    return [resolve(r) for r in context.cfg.files.allowed_roots]


def safe(path: str) -> Path:
    """Resolve a user path and make sure it's inside an allowed folder."""
    p = Path(os.path.expanduser(path.strip().strip('"')))
    if not p.is_absolute():
        # "Documents/report.pdf" or just "report.pdf" -> try each root
        for r in roots():
            cand = (r / p) if not str(p).lower().startswith(r.name.lower()) else (r.parent / p)
            if cand.exists():
                p = cand
                break
        else:
            p = roots()[-1] / p     # default: workspace
    p = p.resolve()
    if not any(p == r or r in p.parents for r in roots()):
        raise PermissionError(f"{p} is outside the folders Nova may touch (see files.allowed_roots in config.yaml)")
    return p


def extract_text(p: Path, limit: int = 60000) -> str:
    """Document → text: markitdown / docling when installed, the built-in readers otherwise (see nova/convert.py)."""
    from ..convert import to_markdown
    return to_markdown(p, limit)


@tool(group="files")
def list_folder(path: str = "~/Desktop", pattern: str = "*") -> list:
    """List what's in a folder.
    Args:
        path: folder path, e.g. ~/Downloads
        pattern: optional filter like *.pdf
    """
    p = safe(path)
    items = sorted(p.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)[:80]
    return [f"{'[folder] ' if i.is_dir() else ''}{i.name}" + ("" if i.is_dir() else f"  ({i.stat().st_size // 1024} KB)")
            for i in items] or "Empty."


@tool(group="files")
def find_files(name_contains: str, extension: str = "", folder: str = "") -> list:
    """Search for files by name in the allowed folders.
    Args:
        name_contains: part of the file name
        extension: optional, e.g. pdf
        folder: optional folder to search in
    """
    needle, ext = name_contains.lower(), extension.lower().lstrip(".")
    found = []
    for r in ([safe(folder)] if folder else roots()):
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "node_modules", "__pycache__"))]
            for f in filenames:
                if needle in f.lower() and (not ext or f.lower().endswith("." + ext)):
                    found.append(os.path.join(dirpath, f))
                    if len(found) >= 25:
                        return found
    return found or f"No files matching '{name_contains}'."


@tool(group="files")
def read_file(path: str) -> str:
    """Read a document: PDF, Word, PowerPoint, Excel, Outlook .msg, EPUB, HTML, CSV, text…
    Args:
        path: file path
    """
    return extract_text(safe(path), 8000)


@tool(group="files")
def write_file(path: str, content: str, append: bool = False) -> str:
    """Create a text file (or append to one). Existing files are backed up before being replaced.
    Args:
        path: file path, e.g. ~/Documents/list.txt
        content: the text
        append: add to the end instead of replacing
    """
    p = safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not append:
        shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    context.record("file", p.name, p)
    return f"Saved {p}"


@tool(group="files")
def create_folder(path: str) -> str:
    """Create a folder.
    Args:
        path: folder path
    """
    p = safe(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Created {p}"


@tool(group="files")
def copy_path(source: str, destination: str) -> str:
    """Copy a file or folder.
    Args:
        source: what to copy
        destination: where to copy it to (folder or new path)
    """
    s, d = safe(source), safe(destination)
    if d.is_dir():
        d = d / s.name
    (shutil.copytree if s.is_dir() else shutil.copy2)(s, d)
    return f"Copied to {d}"


@tool(group="files", confirm=True)
def move_path(source: str, destination: str) -> str:
    """Move or rename a file or folder.
    Args:
        source: what to move
        destination: new folder or new name/path
    """
    s, d = safe(source), safe(destination)
    if d.is_dir():
        d = d / s.name
    shutil.move(str(s), str(d))
    return f"Moved to {d}"


@tool(group="files", confirm=True)
def delete_path(path: str) -> str:
    """Delete a file or folder (it goes to the Recycle Bin, so it can be restored).
    Args:
        path: what to delete
    """
    from send2trash import send2trash
    p = safe(path)
    send2trash(str(p))
    return f"Moved {p.name} to the Recycle Bin."


@tool(group="files", confirm=True)
def organize_folder(path: str = "~/Downloads") -> str:
    """Tidy a folder by sorting loose files into subfolders (Images, Documents, Videos, …).
    Args:
        path: the folder to tidy
    """
    p = safe(path)
    moved = 0
    for f in p.iterdir():
        if f.is_file() and not f.name.startswith("."):
            cat = next((k for k, v in TYPES.items() if f.suffix.lower() in v), "Other")
            dest = p / cat
            dest.mkdir(exist_ok=True)
            target = dest / f.name
            i = 1
            while target.exists():
                target = dest / f"{f.stem} ({i}){f.suffix}"
                i += 1
            shutil.move(str(f), str(target))
            moved += 1
    return f"Sorted {moved} files in {p}."


@tool(group="files")
def zip_path(path: str) -> str:
    """Zip a folder or file.
    Args:
        path: what to zip
    """
    p = safe(path)
    out = shutil.make_archive(str(p), "zip", root_dir=p.parent, base_dir=p.name)
    context.record("file", Path(out).name, out, "zip")
    return f"Created {out}"


@tool(group="files")
def open_path(path: str) -> str:
    """Open a file or folder on the PC with its default app.
    Args:
        path: file or folder
    """
    p = safe(path)
    if platform.system() == "Windows":
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return f"Opened {p.name}."


@tool(group="files")
def send_to_phone(path: str) -> str:
    """Send a file from the PC to the user's phone (Telegram).
    Args:
        path: file path
    """
    p = safe(path)
    if p.stat().st_size > 49e6:
        return "That file is over Telegram's 50 MB bot limit. I can zip it or upload it to Google Drive instead."
    context.attach(p)
    return f"Sending {p.name}."


@tool(group="files")
def document_to_markdown(path: str) -> str:
    """Convert a document (PDF, Word, PowerPoint, Excel, Outlook .msg, EPUB, HTML…) to a Markdown file.
    Args:
        path: the document
    """
    from ..convert import to_markdown
    p = safe(path)
    text = to_markdown(p, limit=2_000_000)
    out = resolve("workspace/docs") / f"{p.stem}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    context.record("file", out.name, out, "markdown")
    context.attach(out)
    return f"Saved {out} ({len(text.split())} words)."


NUM = re.compile(r"-?(\d{1,3}([ ,]\d{3})+|\d+)(\.\d+)?")


def _num(v: str):
    """Plain numbers ('12 000', '1,299.50', '42') become numbers so Excel can add them up; anything else stays text."""
    t = str(v).strip().replace("\u00a0", " ")
    if NUM.fullmatch(t):
        return float(t.replace(" ", "").replace(",", ""))
    return v


@tool(group="files")
def extract_tables(path: str) -> str:
    """Pull every table out of a PDF / Word / PowerPoint file into an Excel workbook (one sheet per table).
    Args:
        path: the document, e.g. a price list PDF
    """
    from ..convert import pdf_tables
    p = safe(path)
    tables = pdf_tables(p)
    if not tables:
        return f"I couldn't find any tables in {p.name}."
    import openpyxl
    out = resolve("workspace/docs") / f"{p.stem} tables.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for n, t in enumerate(tables, 1):
        ws = wb.create_sheet(f"Table {n}")
        ws.append(t["columns"])
        for row in t["rows"]:
            ws.append([_num(v) for v in row])
    wb.save(out)
    context.record("file", out.name, out, "tables")
    context.attach(out)
    sizes = ", ".join(f"{len(t['rows'])}×{len(t['columns'])}" for t in tables[:6])
    return f"Found {len(tables)} table(s) ({sizes}) — saved to {out}."
