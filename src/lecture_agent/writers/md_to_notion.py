"""Small, dependency free Markdown to Notion blocks converter.

Supports: headings, paragraphs, bullet/numbered/todo lists (2 levels), quotes,
Obsidian style callouts, code fences, $$ equations, tables, dividers and inline
bold / italic / code / links / $math$. Obsidian [[wikilinks]] become plain text.
"""
from __future__ import annotations

import re
from typing import Callable

MAX_RICH = 2000  # Notion limit per rich_text object

_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_INLINE = re.compile(
    r"(<small>.+?</small>"
    r"|\*\*[^*\n]+\*\*"
    r"|`[^`\n]+`"
    r"|\[[^\]\n]+\]\([^)\n]+\)"
    r"|\$(?=\S)[^$\n]*?(?<=\S)\$"
    r"|(?<!\*)\*(?!\s)[^*\n]+?(?<!\s)\*(?!\*))"
)

_LANG = {
    "py": "python", "python": "python", "js": "javascript", "javascript": "javascript",
    "ts": "typescript", "typescript": "typescript", "java": "java", "c": "c", "cpp": "c++",
    "c++": "c++", "cs": "c#", "c#": "c#", "go": "go", "rust": "rust", "rs": "rust",
    "sql": "sql", "bash": "bash", "sh": "shell", "shell": "shell", "zsh": "shell",
    "json": "json", "yaml": "yaml", "yml": "yaml", "html": "html", "css": "css",
    "md": "markdown", "markdown": "markdown", "tex": "latex", "latex": "latex",
    "r": "r", "kotlin": "kotlin", "swift": "swift", "haskell": "haskell", "hs": "haskell",
    "": "plain text", "text": "plain text", "txt": "plain text",
}

_CALLOUT_ICONS = {"summary": "📝", "note": "📌", "tip": "💡", "warning": "⚠️", "important": "❗", "info": "ℹ️", "quote": "🎤"}


def _plain(text: str) -> str:
    return _WIKILINK.sub(lambda m: m.group(2) or m.group(1), text)


def _rt(content: str, bold=False, italic=False, code=False, link: str | None = None) -> list[dict]:
    items = []
    for i in range(0, len(content), MAX_RICH):
        piece = content[i:i + MAX_RICH]
        obj: dict = {"type": "text", "text": {"content": piece}}
        if link:
            obj["text"]["link"] = {"url": link}
        if bold or italic or code:
            obj["annotations"] = {"bold": bold, "italic": italic, "code": code}
        items.append(obj)
    return items


def rich_text(text: str) -> list[dict]:
    text = _plain(text)
    out: list[dict] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out += _rt(text[pos:m.start()])
        tok = m.group(0)
        if tok.startswith("<small>"):  # no font sizes in Notion, so small text becomes grey
            inner = rich_text(tok[7:-8])
            for item in inner:
                if item["type"] == "text":
                    item.setdefault("annotations", {})["color"] = "gray"
            out += inner
        elif tok.startswith("**"):
            out += _rt(tok[2:-2], bold=True)
        elif tok.startswith("`"):
            out += _rt(tok[1:-1], code=True)
        elif tok.startswith("["):
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok).groups()
            out += _rt(label, link=url if url.startswith(("http://", "https://")) else None)
        elif tok.startswith("$"):
            out.append({"type": "equation", "equation": {"expression": tok[1:-1]}})
        else:
            out += _rt(tok[1:-1], italic=True)
        pos = m.end()
    if pos < len(text):
        out += _rt(text[pos:])
    return out or _rt("")


def _block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text), **extra}}


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_TODO = re.compile(r"^\[( |x|X)\]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_FENCE = re.compile(r"^```\s*([\w+#-]*)\s*$")
_CALLOUT = re.compile(r"^\[!(\w+)\][+-]?\s*(.*)$")
_FIGURE = re.compile(r"^\{\{figure:\s*([^|}]+?)\s*(?:\|\s*(.*?))?\s*\}\}$")

FigureResolver = Callable[[str, str], "dict | None"]


def _table(rows: list[str]) -> dict:
    parsed = []
    for r in rows:
        if _TABLE_SEP.match(r.strip()):
            continue
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        parsed.append(cells)
    width = max(len(r) for r in parsed)
    children = []
    for r in parsed:
        r = r + [""] * (width - len(r))
        children.append({"object": "block", "type": "table_row",
                         "table_row": {"cells": [rich_text(c) for c in r]}})
    return {"object": "block", "type": "table",
            "table": {"table_width": width, "has_column_header": True,
                      "has_row_header": False, "children": children}}


def _is_special(line: str) -> bool:
    s = line.strip()
    return bool(
        not s or _HEADING.match(line) or _BULLET.match(line) or _NUMBERED.match(line)
        or s.startswith(("```", ">", "|", "$$", "{{figure:")) or re.match(r"^(-{3,}|\*{3,}|_{3,})$", s)
    )


def to_blocks(md: str, figure_resolver: FigureResolver | None = None) -> list[dict]:
    """Convert markdown to Notion blocks. `figure_resolver(id, caption)` returns an image block or None."""
    lines = md.replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    stack: list[tuple[int, dict]] = []  # for nested list items
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()

        if not s:
            stack.clear()
            i += 1
            continue

        fm = _FIGURE.match(s)
        if fm:
            block = figure_resolver(fm.group(1).strip(), (fm.group(2) or "").strip()) if figure_resolver else None
            if block:
                blocks.append(block)
            stack.clear()
            i += 1
            continue

        m = _FENCE.match(s)
        if m:
            lang = _LANG.get(m.group(1).lower(), "plain text")
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append({"object": "block", "type": "code",
                           "code": {"rich_text": _rt("\n".join(code)), "language": lang}})
            stack.clear()
            continue

        if s.startswith("$$"):
            if s != "$$" and s.endswith("$$") and len(s) > 4:
                expr = s[2:-2].strip()
                i += 1
            else:
                i += 1
                eq = [] if s == "$$" else [s[2:]]
                while i < len(lines) and lines[i].strip() != "$$":
                    eq.append(lines[i])
                    i += 1
                i += 1
                expr = "\n".join(eq).strip()
            blocks.append({"object": "block", "type": "equation", "equation": {"expression": expr}})
            stack.clear()
            continue

        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            stack.clear()
            i += 1
            continue

        m = _HEADING.match(line)
        if m:
            level = min(len(m.group(1)), 3)
            blocks.append(_block(f"heading_{level}", m.group(2).strip()))
            stack.clear()
            i += 1
            continue

        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            if len(rows) >= 2:
                blocks.append(_table(rows))
            else:
                blocks.append(_block("paragraph", rows[0]))
            stack.clear()
            continue

        if s.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            cm = _CALLOUT.match(quote[0]) if quote else None
            if cm:
                title, rest = cm.group(2).strip(), " ".join(x for x in quote[1:] if x)
                body = f"{title.rstrip(':')}: {rest}" if title and rest else " ".join(x for x in [title, rest] if x)
                icon = _CALLOUT_ICONS.get(cm.group(1).lower(), "💬")
                blocks.append(_block("callout", body, icon={"type": "emoji", "emoji": icon}))
            else:
                blocks.append(_block("quote", " ".join(quote)))
            stack.clear()
            continue

        lm = _BULLET.match(line)
        nm = _NUMBERED.match(line) if not lm else None
        if lm or nm:
            mm = lm or nm
            depth = min(len(mm.group(1).replace("\t", "  ")) // 2, 1)
            text = mm.group(2)
            if lm and _TODO.match(text):
                t = _TODO.match(text)
                block = _block("to_do", t.group(2), checked=t.group(1).lower() == "x")
            else:
                block = _block("bulleted_list_item" if lm else "numbered_list_item", text)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                parent[parent["type"]].setdefault("children", []).append(block)
            else:
                blocks.append(block)
            stack.append((depth, block))
            i += 1
            continue

        para = [s]
        i += 1
        while i < len(lines) and not _is_special(lines[i]):
            para.append(lines[i].strip())
            i += 1
        blocks.append(_block("paragraph", " ".join(para)))
        stack.clear()

    return blocks
