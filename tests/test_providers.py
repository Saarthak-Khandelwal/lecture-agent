from types import SimpleNamespace

import pytest
from PIL import Image

from lecture_agent import llm, references
from lecture_agent.config import load_config, validate
from lecture_agent.extractors import Source
from lecture_agent.figures import Figure
from lecture_agent.prompts import SYSTEM_PLAIN

ANSWER = """===TITLE===
Graph Basics
===SUMMARY===
Intro to graphs.
===TAGS===
Graph Theory, #bfs
===REFERENCE_TOPICS===
- Breadth first search
- Adjacency lists
===REVIEW_QUESTIONS===
- What is a node?
1. What is an edge?
===BODY===
## Key terms
- **Node**: a vertex

{{figure:deck-s02 | A pipeline}}
"""


def test_defaults_are_free_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = load_config()
    assert cfg.provider == "gemini" and cfg.model == "gemini-flash-latest" and cfg.vision is True
    assert cfg.ref_method == "ddgs"
    assert any("GEMINI_API_KEY" in p for p in validate(cfg))


def test_ollama_defaults_small_context(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("llm:\n  provider: ollama\n")
    cfg = load_config(f)
    assert cfg.vision is False and cfg.max_input_chars == 24000 and cfg.model == "llama3.1:8b"
    assert not any("KEY" in p and "NOTION" not in p and "GEMINI" in p for p in validate(cfg))


def test_system_plain_has_no_tool_instruction():
    assert "save_lecture_notes" not in SYSTEM_PLAIN and "===BODY===" in SYSTEM_PLAIN


def test_parse_plain():
    n = llm.parse_plain(ANSWER)
    assert n["title"] == "Graph Basics"
    assert n["reference_topics"] == ["Breadth first search", "Adjacency lists"]
    assert n["review_questions"] == ["What is a node?", "What is an edge?"]
    assert n["body_markdown"].startswith("## Key terms") and "{{figure:deck-s02" in n["body_markdown"]
    assert llm._clean_tags(n["tags"]) == ["graph-theory", "bfs"]


def test_parse_plain_handles_code_fence_and_missing_sections():
    assert llm.parse_plain("```markdown\n" + ANSWER + "\n```")["title"] == "Graph Basics"
    with pytest.raises(ValueError):
        llm.parse_plain("just some chatty text")


class FakeOpenAI:
    calls = []
    replies = []

    def __init__(self, **kw):
        FakeOpenAI.kw = kw
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kw):
        FakeOpenAI.calls.append(kw)
        text = FakeOpenAI.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def run_generate(monkeypatch, tmp_path, provider, vision):
    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    FakeOpenAI.calls, FakeOpenAI.replies = [], ["chatty nonsense", ANSWER]
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    png = tmp_path / "a.png"
    Image.new("RGB", (800, 600), "red").save(png)
    cfg = load_config()
    cfg.provider, cfg.vision, cfg.gemini_api_key = provider, vision, "k"
    figs = [Figure("deck-s02", png, "Slide 2", "Pipeline")]
    src = [Source("slides", "deck.pptx", "## Slide 2: Pipeline")]
    return cfg, llm.generate_notes(cfg, "U", "w1", src, [], figs)


def test_gemini_path_sends_images_and_retries_bad_format(monkeypatch, tmp_path):
    cfg, notes = run_generate(monkeypatch, tmp_path, "gemini", True)
    assert notes["title"] == "Graph Basics" and notes["tags"] == ["graph-theory", "bfs"]
    assert len(FakeOpenAI.calls) == 2                       # first answer was unparsable, retried
    assert "generativelanguage.googleapis.com" in FakeOpenAI.kw["base_url"]
    user = FakeOpenAI.calls[0]["messages"][1]["content"]
    assert any(p["type"] == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in user)
    assert FakeOpenAI.calls[0]["messages"][0]["content"] == SYSTEM_PLAIN


def test_ollama_path_text_only_catalog(monkeypatch, tmp_path):
    cfg, notes = run_generate(monkeypatch, tmp_path, "ollama", False)
    assert "localhost:11434" in FakeOpenAI.kw["base_url"]
    user = FakeOpenAI.calls[0]["messages"][1]["content"]
    assert all(p["type"] == "text" for p in user)           # no images sent
    joined = "\n".join(p["text"] for p in user)
    assert "you cannot see the images" in joined and '<figure id="deck-s02"' in joined


def test_ddgs_references_prefer_good_sources(monkeypatch):
    results = [
        {"title": "Quora answer", "href": "https://www.quora.com/bfs"},
        {"title": "Random blog", "href": "https://someblog.io/bfs"},
        {"title": "BFS - Wikipedia", "href": "https://en.wikipedia.org/wiki/Breadth-first_search"},
    ]
    monkeypatch.setattr(references, "_ddgs_text", lambda q, n: results)
    cfg = load_config()
    out = references.research_references(cfg, "T", ["Breadth first search"])
    assert [r["url"] for r in out] == ["https://en.wikipedia.org/wiki/Breadth-first_search"]
    assert out[0]["topic"] == "Breadth first search"


def test_ddgs_failure_is_not_fatal(monkeypatch):
    def boom(q, n):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(references, "_ddgs_text", boom)
    assert references.research_references(load_config(), "T", ["x"]) == []
