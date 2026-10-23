from __future__ import annotations

from pathlib import Path

import pymupdf as fitz


def extract_pdf(path: Path) -> str:
    out: list[str] = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if text:
                out.append(f"## Page {i}\n{text}")
    return "\n\n".join(out)
