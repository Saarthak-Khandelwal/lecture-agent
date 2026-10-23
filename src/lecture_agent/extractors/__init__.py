from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SLIDE_EXT = {".pptx", ".pdf"}
TRANSCRIPT_EXT = {".vtt", ".srt"}
TEXT_EXT = {".txt", ".md"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass
class Source:
    kind: str                       # slides | transcript | notes | image
    name: str
    text: str = ""
    image_b64: str | None = None
    media_type: str | None = None


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SLIDE_EXT | TRANSCRIPT_EXT | TEXT_EXT | IMAGE_EXT


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in SLIDE_EXT:
        return "slides"
    if ext in TRANSCRIPT_EXT or "transcript" in path.stem.lower():
        return "transcript"
    if ext in IMAGE_EXT:
        return "image"
    return "notes"


def extract(path: Path) -> Source | None:
    """Turn one file into a Source. Returns None if the file can't be used."""
    kind = classify(path)
    ext = path.suffix.lower()
    try:
        if ext == ".pptx":
            from .pptx_extractor import extract_pptx
            return Source(kind, path.name, extract_pptx(path))
        if ext == ".pdf":
            from .pdf_extractor import extract_pdf
            return Source(kind, path.name, extract_pdf(path))
        if ext in TRANSCRIPT_EXT or kind == "transcript":
            from .transcript_extractor import clean_transcript
            return Source(kind, path.name, clean_transcript(path.read_text(errors="replace"), ext))
        if ext in IMAGE_EXT:
            data = path.read_bytes()
            if len(data) > MAX_IMAGE_BYTES:
                log.warning("Skipping %s: image larger than 5 MB", path.name)
                return None
            return Source(kind, path.name, image_b64=base64.b64encode(data).decode(),
                          media_type=MEDIA_TYPES[ext])
        return Source(kind, path.name, path.read_text(errors="replace"))
    except Exception:
        log.exception("Failed to extract %s", path)
        return None
