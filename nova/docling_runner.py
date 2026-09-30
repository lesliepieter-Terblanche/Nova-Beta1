"""Runs docling in its own Python (tools/docling/venv). Standalone — must not import Nova.
usage: docling_runner.py md|tables <input> <output>"""
from __future__ import annotations

import json
import sys


def main(mode: str, src: str, out: str) -> None:
    from docling.document_converter import DocumentConverter
    doc = DocumentConverter().convert(src).document
    if mode == "md":
        with open(out, "w", encoding="utf-8") as f:
            f.write(doc.export_to_markdown())
        return
    tables = []
    for t in doc.tables:
        try:
            df = t.export_to_dataframe(doc=doc)
        except TypeError:
            df = t.export_to_dataframe()
        tables.append({"columns": [str(c) for c in df.columns],
                       "rows": [["" if v is None else str(v) for v in row] for row in df.itertuples(index=False)]})
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False)


if __name__ == "__main__":
    main(*sys.argv[1:4])
