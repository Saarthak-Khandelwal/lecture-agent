"""LLM calls. Providers:

gemini     free tier via Google AI Studio key (OpenAI compatible endpoint), supports images
ollama     free, runs locally, no key (images only if you use a vision model)
anthropic  paid, uses forced tool calling for structured output
"""
from __future__ import annotations

import logging
import re
import time

import anthropic

from .config import Config
from .extractors import Source
from .figures import Figure, thumbnail
from .prompts import CONDENSE, SYSTEM, SYSTEM_PLAIN, TOOL

log = logging.getLogger(__name__)

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "ollama": "http://localhost:11434/v1",
}


# ---------------------------------------------------------------- helpers

def _chunks(text: str, size: int) -> list[str]:
    """Split on paragraph/line boundaries as far as possible."""
    out, buf = [], ""
    for piece in text.replace("\r", "").split("\n"):
        if len(buf) + len(piece) + 1 > size and buf:
            out.append(buf)
            buf = ""
        while len(piece) > size:  # one huge line (common in cleaned VTT)
            cut = piece.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            out.append(piece[:cut])
            piece = piece[cut:].lstrip()
        buf += piece + "\n"
    if buf.strip():
        out.append(buf)
    return out


def _to_openai_parts(blocks: list[dict]) -> list[dict]:
    parts = []
    for b in blocks:
        if b["type"] == "text":
            parts.append({"type": "text", "text": b["text"]})
        elif b["type"] == "image":
            src = b["source"]
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
    return parts


def _chat_text(cfg: Config, system: str, content: list[dict] | str, model: str, max_tokens: int) -> str:
    """One chat completion on an OpenAI compatible provider (gemini / ollama), with rate limit retries."""
    from openai import APIConnectionError, OpenAI, RateLimitError

    client = OpenAI(
        api_key=cfg.gemini_api_key if cfg.provider == "gemini" else "ollama",
        base_url=cfg.llm_base_url or BASE_URLS[cfg.provider],
        timeout=600,
    )
    user = content if isinstance(content, str) else _to_openai_parts(content)
    for attempt in range(6):
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return resp.choices[0].message.content or ""
        except RateLimitError:
            wait = min(20 * 2 ** attempt, 180)  # free tiers allow only a few requests per minute
            log.warning("Rate limited by %s, waiting %ds (attempt %d/6)", cfg.provider, wait, attempt + 1)
            time.sleep(wait)
        except APIConnectionError as e:
            hint = " Is Ollama running? Start it with `ollama serve`." if cfg.provider == "ollama" else ""
            raise RuntimeError(f"Could not reach the {cfg.provider} API.{hint}") from e
    raise RuntimeError(f"Still rate limited by {cfg.provider} after several retries, try again later")


def _complete_plain(cfg: Config, system: str, text: str, model: str, max_tokens: int) -> str:
    """Text in, text out, for any provider (used for transcript condensing)."""
    if cfg.provider == "anthropic":
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        resp = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": text}])
        return "".join(b.text for b in resp.content if b.type == "text")
    return _chat_text(cfg, system, text, model, max_tokens)


def _condense_transcripts(cfg: Config, sources: list[Source]) -> list[Source]:
    """Map step for very long inputs: shrink transcripts chunk by chunk."""
    total = sum(len(s.text) for s in sources)
    if total <= cfg.max_input_chars:
        return sources

    log.info("Input is %d chars, condensing transcripts first", total)
    result = []
    for s in sources:
        if s.kind != "transcript" or len(s.text) <= cfg.chunk_chars:
            result.append(s)
            continue
        parts = []
        for i, chunk in enumerate(_chunks(s.text, cfg.chunk_chars), start=1):
            out = _complete_plain(cfg, CONDENSE, chunk, cfg.condense_model, 4000)
            parts.append(f"(part {i})\n{out}")
        result.append(Source(s.kind, s.name, "\n\n".join(parts)))
    return result


def _build_content(cfg: Config, unit: str, lecture_name: str, sources: list[Source],
                   existing_titles: list[str] | None, figures: list[Figure],
                   material_links: list[dict] | None = None) -> list[dict]:
    header = f"Unit: {unit}\nLecture folder name: {lecture_name}\n"
    if existing_titles:
        header += "\nExisting notes you may link to with [[Title]]:\n" + "\n".join(f"- {t}" for t in existing_titles)
    if material_links:
        header += ("\nLinks found in the lecture material (place the useful ones with {{links: ID}} at the end of "
                   "the section they belong to; do not place administrative ones):\n")
        header += "\n".join(
            f"- {m['key']}: {m['url']}" + (f"  (appears at: {m['context']})" if m.get("context") else "")
            for m in material_links) + "\n"
    if any(s.kind == "transcript" and s.text.strip() for s in sources):
        header += ("\nA lecture transcript is included. Add the important extra points the lecturer made as "
                   "'> [!quote] Lecturer' callouts, placed right after the slide content they relate to.\n")
    content: list[dict] = [{"type": "text", "text": header}]

    file_fig_ids = {f.source_name: f.id for f in figures if f.source_name}
    for s in sources:
        if s.image_b64:
            if not cfg.vision:
                log.info("Model has no vision, skipping image %s", s.name)
                continue
            fid = file_fig_ids.get(s.name)
            tag = f'<source kind="image" name="{s.name}"' + (f' figure_id="{fid}"' if fid else "") + ">"
            content.append({"type": "text", "text": tag})
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": s.media_type, "data": s.image_b64}})
        elif s.text.strip():
            content.append({"type": "text",
                            "text": f'<source kind="{s.kind}" name="{s.name}">\n{s.text}\n</source>'})

    candidates = [f for f in figures if f.in_prompt]
    embeddable = candidates + ([f for f in figures if f.source_name] if cfg.vision else [])
    if embeddable:
        if cfg.vision:
            intro = (f"Candidate figures (embed at most {cfg.fig_max_in_notes} in total, only if they help; "
                     "uploaded image files above can be embedded by their figure_id too):")
        else:
            intro = (f"Candidate figures (you cannot see the images, decide from the slide titles and the slide "
                     f"text above; embed at most {cfg.fig_max_in_notes} in total, only if they clearly help):")
        content.append({"type": "text", "text": intro})
        for f in candidates:
            hint = f' title="{f.hint}"' if f.hint else ""
            content.append({"type": "text", "text": f'<figure id="{f.id}" location="{f.label}"{hint}>'})
            if cfg.vision:
                media, data = thumbnail(f)
                content.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
    return content


# ---------------------------------------------------------------- plain text output parsing

_SECTION = re.compile(r"^===([A-Z_]+)===[ \t]*$", re.M)


def _lines(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", ln).strip()
        if ln:
            out.append(ln)
    return out


def parse_plain(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):  # model wrapped everything in a code fence
        text = re.sub(r"^```\w*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    matches = list(_SECTION.finditer(text))
    parts: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts[m.group(1)] = text[m.end():end].strip()
    if not parts.get("TITLE") or not parts.get("BODY"):
        raise ValueError("answer is missing the TITLE or BODY section")
    return {
        "title": parts["TITLE"].splitlines()[0].strip("# ").strip(),
        "summary": parts.get("SUMMARY", ""),
        "tags": re.split(r"[,\n]", parts.get("TAGS", "")),
        "reference_topics": _lines(parts.get("REFERENCE_TOPICS", ""))[:5],
        "review_questions": _lines(parts.get("REVIEW_QUESTIONS", "")),
        "body_markdown": parts["BODY"],
    }


def _clean_tags(tags: list[str]) -> list[str]:
    return [t.strip().lower().replace(" ", "-").lstrip("#") for t in tags if t and t.strip()]


# ---------------------------------------------------------------- public API

def generate_notes(
    cfg: Config,
    unit: str,
    lecture_name: str,
    sources: list[Source],
    existing_titles: list[str] | None = None,
    figures: list[Figure] | None = None,
    material_links: list[dict] | None = None,
) -> dict:
    sources = _condense_transcripts(cfg, sources)
    content = _build_content(cfg, unit, lecture_name, sources, existing_titles, figures or [], material_links)

    if cfg.provider == "anthropic":
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        resp = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=[{"role": "user", "content": content}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                notes = dict(block.input)
                notes["tags"] = _clean_tags(notes.get("tags", []))
                notes.setdefault("reference_topics", [])
                return notes
        raise RuntimeError("Model did not return notes (no tool call in response)")

    last_error: Exception | None = None
    for attempt in range(2):  # smaller models sometimes ignore the format, so try once more
        raw = _chat_text(cfg, SYSTEM_PLAIN, content, cfg.model, cfg.max_tokens)
        try:
            notes = parse_plain(raw)
            notes["tags"] = _clean_tags(notes["tags"])
            return notes
        except ValueError as e:
            last_error = e
            log.warning("Could not parse model answer (%s), retrying. Start of answer: %r", e, raw[:200])
    raise RuntimeError(f"Model did not follow the output format: {last_error}")
