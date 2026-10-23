from __future__ import annotations

import re
import shutil
from datetime import date, datetime
from pathlib import Path

import yaml

from ..config import Config
from ..figures import Figure
from ..lectures import Lecture
from ..render import FIGURE_RE, render_body

_BAD_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]')


def safe_name(text: str, max_len: int = 100) -> str:
    cleaned = _BAD_CHARS.sub("", text).strip().strip(".")
    return re.sub(r"\s+", " ", cleaned)[:max_len] or "Untitled"


def existing_titles(cfg: Config, limit: int = 400) -> list[str]:
    """Note names already in the vault, so the model can link to them."""
    if not cfg.vault_path or not cfg.vault_path.exists():
        return []
    names = []
    for p in cfg.vault_path.rglob("*.md"):
        if any(part.startswith(".") for part in p.relative_to(cfg.vault_path).parts):
            continue
        names.append(p.stem)
    names.sort()
    return names[:limit]


def _embed_figures(cfg: Config, body: str, figures: list[Figure], note_dir: Path, lecture: Lecture) -> str:
    """Copy used figure images into the vault and replace {{figure:...}} placeholders with embeds."""
    by_id = {f.id: f for f in figures}
    attach = note_dir / "attachments" / safe_name(lecture.name)
    shutil.rmtree(attach, ignore_errors=True)  # this folder is owned by the agent, rebuilt each run
    used: set[str] = set()

    def repl(m) -> str:
        fid, caption = m.group(1).strip(), (m.group(2) or "").strip()
        fig = by_id.get(fid)
        if fig is None or fid in used:
            return ""
        used.add(fid)
        attach.mkdir(parents=True, exist_ok=True)
        dest = attach / f"{fid}{fig.path.suffix.lower()}"
        shutil.copy2(fig.path, dest)
        rel = dest.relative_to(cfg.vault_path).as_posix()
        cap = f"Figure: {caption} ({fig.label})" if caption else f"Figure ({fig.label})"
        return f"![[{rel}|700]]\n\n<small>{cap}</small>"

    return FIGURE_RE.sub(repl, body)


def write_note(cfg: Config, lecture: Lecture, notes: dict, previous_path: str | None = None,
               figures: list[Figure] | None = None) -> Path:
    assert cfg.vault_path is not None
    if previous_path and Path(previous_path).exists():
        target = Path(previous_path)  # keep the same file on re-runs so links stay stable
    else:
        folder = cfg.vault_path / cfg.obsidian_subfolder / safe_name(lecture.unit)
        target = folder / f"{safe_name(lecture.name)} - {safe_name(notes['title'])}.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "title": notes["title"],
        "unit": lecture.unit,
        "lecture": lecture.name,
        "date": date.today().isoformat(),
        "tags": ["lecture", *notes.get("tags", [])],
        "source": "lecture-agent",
        "generated": datetime.now().isoformat(timespec="seconds"),
    }
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = _embed_figures(cfg, render_body(notes), figures or [], target.parent, lecture)
    target.write_text(f"---\n{fm}\n---\n\n# {notes['title']}\n\n{body}", encoding="utf-8")
    return target
