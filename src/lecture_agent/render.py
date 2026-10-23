from __future__ import annotations

import re

FIGURE_RE = re.compile(r"\{\{figure:\s*([^|}\n]+?)\s*(?:\|\s*([^}\n]*?))?\s*\}\}")
LINKS_RE = re.compile(r"\n*[ \t]*\{\{links:\s*([^}\n]+?)\s*\}\}[ \t]*\n*")


def used_figure_ids(notes: dict) -> list[str]:
    """Figure ids referenced in the notes, in order, without duplicates."""
    seen: list[str] = []
    for m in FIGURE_RE.finditer(notes.get("body_markdown", "")):
        fid = m.group(1).strip()
        if fid not in seen:
            seen.append(fid)
    return seen


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _title(r: dict) -> str:
    return (r.get("title") or r["url"]).replace("[", "(").replace("]", ")")


def _link_line(refs: list[dict]) -> str:
    """One small font line, e.g. 'Further reading: [A](url) | [B](url)'. <small> is small text in Obsidian, grey in Notion."""
    label = refs[0].get("label") or "Further reading"
    links = " | ".join(f"[{_title(r)}]({r['url']})" for r in refs)
    return f"<small>**{label}**: {links}</small>"


def place_links(body: str, refs: list[dict]) -> tuple[str, list[dict]]:
    """Put each reference group at the {{links: KEY}} marker the model left at the end of its section.

    Returns (body, leftovers). Leftovers are searched links the model never placed. Links taken from the
    slides that were not placed are dropped on purpose: the model skips administrative links.
    """
    groups: dict[str, list[dict]] = {}
    for r in refs:
        groups.setdefault(_key(r.get("key") or r.get("topic") or ""), []).append(r)
    used: set[str] = set()

    def repl(m) -> str:
        k = _key(m.group(1))
        if k in groups and k not in used:
            used.add(k)
            return "\n\n" + _link_line(groups[k]) + "\n\n"
        return "\n\n"

    body = LINKS_RE.sub(repl, body)
    leftovers = [r for r in refs if _key(r.get("key") or r.get("topic") or "") not in used
                 and not r.get("from_material")]
    return body, leftovers


def render_leftovers(refs: list[dict]) -> str:
    if not refs:
        return ""
    lines = ["## More links"]
    for r in refs:
        topic = f"**{r['topic']}**: " if r.get("topic") else ""
        note = f", {r['note']}" if r.get("note") else ""
        lines.append(f"- <small>{topic}[{_title(r)}]({r['url']}){note}</small>")
    return "\n".join(lines)


def render_body(notes: dict, include_title: bool = False, include_summary: bool = True) -> str:
    """Assemble final markdown from the structured model output.

    Figure placeholders ({{figure:ID | caption}}) are left in place; each writer resolves them.
    Link markers ({{links: KEY}}) are resolved here.
    """
    parts: list[str] = []
    if include_title:
        parts.append(f"# {notes['title']}")
    if include_summary and notes.get("summary"):
        parts.append("> [!summary]\n> " + notes["summary"].strip().replace("\n", "\n> "))
    body, leftovers = place_links(notes["body_markdown"].strip(), notes.get("references") or [])
    parts.append(body.strip())
    questions = notes.get("review_questions") or []
    if questions:
        parts.append("## Review questions\n" + "\n".join(f"- {q}" for q in questions))
    extra = render_leftovers(leftovers)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) + "\n"
