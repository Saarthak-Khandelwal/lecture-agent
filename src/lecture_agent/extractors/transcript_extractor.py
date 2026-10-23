from __future__ import annotations

import re

_TIMESTAMP = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?([.,]\d+)?\s*-->\s*\d{1,2}:\d{2}(:\d{2})?([.,]\d+)?.*$")
_TAGS = re.compile(r"<[^>]+>")


def clean_transcript(raw: str, ext: str = ".txt") -> str:
    """Strip WEBVTT/SRT scaffolding (headers, cue numbers, timestamps) and repeated lines."""
    if ext not in {".vtt", ".srt"} and "-->" not in raw[:2000]:
        return raw.strip()

    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.upper().startswith(("WEBVTT", "NOTE", "KIND:", "LANGUAGE:")):
            continue
        if s.isdigit() or _TIMESTAMP.match(s):
            continue
        s = _TAGS.sub("", s).strip()
        if s and (not lines or lines[-1] != s):
            lines.append(s)
    return " ".join(lines)
