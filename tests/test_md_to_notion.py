from lecture_agent.writers.md_to_notion import rich_text, to_blocks


def types(blocks):
    return [b["type"] for b in blocks]


def test_headings_and_paragraph():
    b = to_blocks("# A\n\n## B\n\n### C\n\nhello world\nsecond line")
    assert types(b) == ["heading_1", "heading_2", "heading_3", "paragraph"]
    assert b[3]["paragraph"]["rich_text"][0]["text"]["content"] == "hello world second line"


def test_nested_lists():
    b = to_blocks("- one\n  - child\n- two\n1. first\n2. second")
    assert types(b) == ["bulleted_list_item", "bulleted_list_item", "numbered_list_item", "numbered_list_item"]
    assert b[0]["bulleted_list_item"]["children"][0]["type"] == "bulleted_list_item"


def test_code_and_equation():
    b = to_blocks("```py\nprint(1)\n```\n\n$$\nx^2\n$$")
    assert b[0]["code"]["language"] == "python"
    assert b[1]["equation"]["expression"] == "x^2"


def test_table():
    b = to_blocks("| a | b |\n|---|---|\n| 1 | 2 |")
    assert b[0]["type"] == "table"
    assert b[0]["table"]["table_width"] == 2
    assert len(b[0]["table"]["children"]) == 2


def test_callout_and_quote():
    b = to_blocks("> [!summary]\n> short text\n\n> plain quote")
    assert types(b) == ["callout", "quote"]


def test_inline_formatting_and_wikilinks():
    rt = rich_text("**bold** and *it* and `code` and [[Other Note]] and $x^2$")
    kinds = [(r.get("annotations") or {}) for r in rt if r["type"] == "text"]
    assert any(k.get("bold") for k in kinds)
    assert any(k.get("italic") for k in kinds)
    assert any(k.get("code") for k in kinds)
    assert any(r["type"] == "equation" for r in rt)
    joined = "".join(r["text"]["content"] for r in rt if r["type"] == "text")
    assert "Other Note" in joined and "[[" not in joined


def test_long_text_is_split():
    rt = rich_text("x" * 4500)
    assert len(rt) == 3 and all(len(r["text"]["content"]) <= 2000 for r in rt)
