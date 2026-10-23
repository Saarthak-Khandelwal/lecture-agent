from types import SimpleNamespace

from PIL import Image

from lecture_agent import llm
from lecture_agent.config import load_config
from lecture_agent.extractors import Source
from lecture_agent.figures import Figure


class FakeClient:
    last = None

    def __init__(self, **kw):
        self.messages = self

    def create(self, **kw):
        FakeClient.last = kw
        block = SimpleNamespace(type="tool_use", input={
            "title": "T", "summary": "s", "tags": ["Graph Theory"], "body_markdown": "b", "review_questions": []})
        return SimpleNamespace(content=[block])


def test_request_contains_figures_and_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(llm.anthropic, "Anthropic", FakeClient)
    png = tmp_path / "a.png"
    Image.new("RGB", (1600, 900), "red").save(png)
    cfg = load_config()
    cfg.provider = "anthropic"
    figs = [Figure("deck-s02", png, "Slide 2", "Pipeline"),
            Figure("file-board", png, "Uploaded image", "board.png", in_prompt=False, source_name="board.png")]
    sources = [Source("slides", "deck.pptx", "## Slide 2"),
               Source("image", "board.png", image_b64="AAAA", media_type="image/png")]
    notes = llm.generate_notes(cfg, "U", "w1", sources, ["Existing"], figs)

    assert notes["tags"] == ["graph-theory"] and notes["reference_topics"] == []
    req = FakeClient.last
    content = req["messages"][0]["content"]
    texts = [c["text"] for c in content if c["type"] == "text"]
    assert any('figure_id="file-board"' in t for t in texts)
    assert any('<figure id="deck-s02" location="Slide 2" title="Pipeline">' in t for t in texts)
    assert any(f"at most {cfg.fig_max_in_notes}" in t for t in texts)
    assert sum(c["type"] == "image" for c in content) == 2   # uploaded image + one thumbnail
    assert req["tool_choice"]["name"] == "save_lecture_notes"
    assert "reference_topics" in req["tools"][0]["input_schema"]["properties"]
