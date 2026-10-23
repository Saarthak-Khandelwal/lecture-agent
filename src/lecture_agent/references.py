"""Find a few trustworthy reference links for topics the lecture material covers thinly.

Links come from two places only, so none are invented:
  1. URLs that already appear in the slides or your notes.
  2. URLs returned by a real web search:
       method "ddgs"      free DuckDuckGo search (no key), results ranked toward docs, universities, Wikipedia
       method "anthropic" Anthropic's web_search tool (paid), picks are discarded unless the URL
                          appeared in the actual search results
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urldefrag, urlparse

import anthropic

from .config import Config
from .extractors import Source

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s)>\]\"'<]+")

SYSTEM = """You find reference material for a university student's lecture notes.
Use web search to find, for each requested topic, one or two high quality references:
official documentation, university course pages, textbook chapters, original papers (arXiv, ACM, IEEE),
Wikipedia or reputable explainers. Avoid paywalled pages, forums, link farms, and SEO blogs.
Finish by calling save_references. Only include URLs that appeared in your search results.
Do not use em dashes."""

SAVE_TOOL = {
    "name": "save_references",
    "description": "Save the final list of references.",
    "input_schema": {
        "type": "object",
        "properties": {
            "references": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "note": {"type": "string", "description": "At most 12 words on why it is useful"},
                    },
                    "required": ["topic", "title", "url"],
                },
            }
        },
        "required": ["references"],
    },
}


def _norm(url: str) -> str:
    return urldefrag(url.strip())[0].rstrip("/").lower()


def _join_wrapped_urls(lines: list[str]) -> list[str]:
    """PDF text wraps long URLs across lines, glue the pieces back together."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = list(_URL.finditer(line))
        while m and line.rstrip().endswith(m[-1].group(0)) and i + 1 < len(lines) and len(m[-1].group(0)) > 8:
            url_end = m[-1].group(0)
            nxt = lines[i + 1].strip()
            glued = bool(nxt) and " " not in nxt and (url_end[-1] in "/-_." or any(c in nxt for c in "/-_"))
            if not glued:
                break
            line = line.rstrip() + nxt
            i += 1
            m = list(_URL.finditer(line))
        out.append(line)
        i += 1
    return out


def urls_from_material(sources: list[Source], limit: int = 20) -> list[dict]:
    """URLs the lecturer or student already put in the slides/notes, with where they appeared.

    Each gets a key (L1, L2, ...) so the model can place it in the right section of the notes.
    """
    seen, out = set(), []
    for s in sources:
        if s.kind not in ("slides", "notes"):
            continue
        heading = ""
        for line in _join_wrapped_urls(s.text.splitlines()):
            if line.startswith("## "):
                heading = line[3:].strip()
                continue
            for m in _URL.finditer(line):
                url = m.group(0).rstrip(".,;:")
                key = _norm(url)
                if key in seen:
                    continue
                seen.add(key)
                parsed = urlparse(url)
                out.append({
                    "key": f"L{len(out) + 1}", "label": "From the slides", "topic": "", "from_material": True,
                    "title": parsed.netloc.removeprefix("www.") + parsed.path[:40], "url": url, "context": heading,
                })
                if len(out) >= limit:
                    return out
    return out


def filter_to_search_results(candidates: list[dict], allowed: set[str], limit: int) -> list[dict]:
    """Keep only references whose URL really appeared in search results (anti-hallucination)."""
    out, seen = [], set()
    for r in candidates:
        url = (r.get("url") or "").strip()
        key = _norm(url)
        if not url.startswith(("http://", "https://")) or key in seen or key not in allowed:
            continue
        seen.add(key)
        out.append({k: (r.get(k) or "").strip() for k in ("topic", "title", "url", "note")})
        if len(out) >= limit:
            break
    return out


def _search_results(content) -> set[str]:
    urls = set()
    for block in content:
        if getattr(block, "type", "") == "web_search_tool_result":
            inner = getattr(block, "content", None)
            if isinstance(inner, list):  # an error object is not a list
                for item in inner:
                    u = getattr(item, "url", None)
                    if u:
                        urls.add(_norm(u))
    return urls


def _research_anthropic(cfg: Config, title: str, topics: list[str]) -> list[dict]:
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    tools = [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": cfg.ref_max_searches},
        SAVE_TOOL,
    ]
    prompt = (f"Lecture: {title}\nFind references for these topics (at most {cfg.ref_max} links in total):\n"
              + "\n".join(f"- {t}" for t in topics[:6]))
    messages = [{"role": "user", "content": prompt}]
    allowed: set[str] = set()
    try:
        for _ in range(4):  # server side searches can pause long turns, so continue if asked
            resp = client.messages.create(model=cfg.ref_model, max_tokens=2500, system=SYSTEM,
                                          tools=tools, messages=messages)
            allowed |= _search_results(resp.content)
            for block in resp.content:
                if block.type == "tool_use" and block.name == "save_references":
                    refs = block.input.get("references", [])
                    return filter_to_search_results(refs, allowed, cfg.ref_max)
            if resp.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
    except Exception:
        log.exception("Reference search failed, continuing without references")
    return []


# ---------------------------------------------------------------- free search (ddgs)

PREFERRED = (
    ("wikipedia.org", 5), (".edu", 5), ("arxiv.org", 5), ("developer.mozilla.org", 5),
    ("readthedocs", 4), ("docs.", 4), ("w3.org", 4), ("python.org", 4), ("acm.org", 4),
    ("ieee.org", 4), ("khanacademy.org", 4), ("mit.edu", 5), ("github.io", 2),
    ("geeksforgeeks.org", 2), ("stackoverflow.com", 1),
)
AVOID = ("quora.com", "reddit.com", "pinterest.", "facebook.com", "youtube.com", "medium.com",
         "coursehero.com", "chegg.com", "scribd.com", "tiktok.com", "twitter.com", "x.com")


def _score(url: str) -> int:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if any(a in host for a in AVOID):
        return -10
    return max((pts for key, pts in PREFERRED if key in host), default=0)


def _ddgs_text(query: str, n: int) -> list[dict]:
    from ddgs import DDGS
    return list(DDGS().text(query, max_results=n))


def _research_ddgs(cfg: Config, topics: list[str]) -> list[dict]:
    out, seen = [], set()
    for topic in topics[:5]:
        try:
            results = _ddgs_text(f"{topic} tutorial", 8)
        except Exception as e:
            log.warning("Search failed for '%s': %s", topic, e)
            continue
        ranked = sorted(
            ((_score(r.get("href", "")), -i, r) for i, r in enumerate(results) if r.get("href", "").startswith("http")),
            key=lambda t: (t[0], t[1]), reverse=True,
        )
        for score, _, r in ranked:
            key = _norm(r["href"])
            if score < 0 or key in seen:
                continue
            seen.add(key)
            out.append({"topic": topic, "title": (r.get("title") or r["href"]).strip()[:90],
                        "url": r["href"], "note": ""})
            break  # one link per topic keeps the list short
    return out[: cfg.ref_max]


def research_references(cfg: Config, title: str, topics: list[str]) -> list[dict]:
    if not cfg.references_enabled or not topics:
        return []
    if cfg.ref_method == "anthropic" and cfg.provider == "anthropic":
        return _research_anthropic(cfg, title, topics)
    return _research_ddgs(cfg, topics)


def collect_references(cfg: Config, notes: dict, material: list[dict]) -> list[dict]:
    """Searched links (keyed by their topic) plus the lecture's own links.

    Where each one appears is decided in render.py from the {{links: KEY}} markers the model placed.
    """
    refs = list(material) if cfg.ref_from_material else []
    for r in research_references(cfg, notes["title"], notes.get("reference_topics", [])):
        r["key"] = r.get("topic", "")
        r["label"] = "Further reading"
        refs.append(r)
    seen, out = set(), []
    for r in refs:
        key = _norm(r["url"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out
