from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .extractors import is_supported

IGNORED_PREFIXES = (".", "~$")
IGNORED_SUFFIXES = (".tmp", ".part", ".crdownload", ".swp")


@dataclass
class Lecture:
    key: str          # "UNIT/LECTURE", stable id used in state
    unit: str
    name: str
    folder: Path
    files: list[Path]

    def digest(self) -> str:
        """Hash of all file names and contents so re-runs only happen on real changes."""
        h = hashlib.sha256()
        for f in self.files:
            h.update(f.name.encode())
            h.update(hashlib.sha256(f.read_bytes()).digest())
        return h.hexdigest()


def is_ignored(path: Path) -> bool:
    name = path.name
    return name.startswith(IGNORED_PREFIXES) or name.endswith(IGNORED_SUFFIXES)


def lecture_dir_for(path: Path, inbox: Path) -> Path | None:
    """Map any file path inside inbox/<UNIT>/<LECTURE>/... to its lecture folder."""
    try:
        rel = path.resolve().relative_to(inbox.resolve())
    except ValueError:
        return None
    if len(rel.parts) < 3:  # needs UNIT/LECTURE/file
        return None
    return inbox.resolve() / rel.parts[0] / rel.parts[1]


def build_lecture(folder: Path, inbox: Path) -> Lecture | None:
    rel = folder.resolve().relative_to(inbox.resolve())
    if len(rel.parts) != 2:
        return None
    files = sorted(
        p for p in folder.rglob("*")
        if p.is_file() and not is_ignored(p) and is_supported(p)
    )
    if not files:
        return None
    unit, name = rel.parts
    return Lecture(key=f"{unit}/{name}", unit=unit, name=name, folder=folder, files=files)


def all_lecture_dirs(inbox: Path) -> list[Path]:
    out = []
    if not inbox.exists():
        return out
    for unit in sorted(p for p in inbox.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for lec in sorted(p for p in unit.iterdir() if p.is_dir() and not p.name.startswith(".")):
            out.append(lec)
    return out
