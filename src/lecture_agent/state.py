from __future__ import annotations

import json
from pathlib import Path


class State:
    """Tiny JSON key-value store: lecture key -> {hash, obsidian_path, notion_page_id}."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.data = {}

    def get(self, key: str) -> dict:
        return self.data.get(key, {})

    def update(self, key: str, **fields) -> None:
        self.data.setdefault(key, {}).update(fields)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)
