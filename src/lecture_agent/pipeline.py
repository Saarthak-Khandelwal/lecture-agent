from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Config
from .extractors import extract
from .figures import build_figures
from .lectures import all_lecture_dirs, build_lecture
from .llm import generate_notes
from .references import collect_references, urls_from_material
from .render import used_figure_ids
from .state import State
from .writers import notion_writer, obsidian_writer

log = logging.getLogger(__name__)


def process_lecture(cfg: Config, state: State, folder: Path, force: bool = False) -> bool:
    """Process one inbox/<UNIT>/<LECTURE> folder. Returns True if notes were (re)generated."""
    lecture = build_lecture(folder, cfg.inbox)
    if lecture is None:
        log.info("Nothing to do in %s", folder)
        return False

    digest = lecture.digest()
    prev = state.get(lecture.key)
    if not force and prev.get("hash") == digest:
        log.info("%s unchanged, skipping", lecture.key)
        return False

    log.info("Processing %s (%d files)", lecture.key, len(lecture.files))
    sources = [s for s in (extract(f) for f in lecture.files) if s is not None]
    if not sources:
        log.warning("No readable files in %s", lecture.key)
        return False

    figures = build_figures(cfg, lecture)

    # Generated notes are cached by input hash, so a failed Notion/Obsidian write never costs another model call.
    cache = cfg.state_file.parent / "cache" / f"{digest}.json"
    if cache.exists() and not force:
        notes = json.loads(cache.read_text(encoding="utf-8"))
        log.info("Reusing generated notes from cache (no model call): %s", notes["title"])
    else:
        titles = []
        if cfg.obsidian_enabled and cfg.link_existing_notes:
            titles = obsidian_writer.existing_titles(cfg)
        material = urls_from_material(sources) if cfg.ref_from_material else []
        notes = generate_notes(cfg, lecture.unit, lecture.name, sources, titles, figures, material)
        log.info("Generated notes: %s (%d figure(s) used)", notes["title"], len(used_figure_ids(notes)))
        notes["references"] = collect_references(cfg, notes, material)
        log.info("References: %d link(s)", len(notes["references"]))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = True
    if cfg.obsidian_enabled:
        try:
            path = obsidian_writer.write_note(cfg, lecture, notes, prev.get("obsidian_path"), figures)
            state.update(lecture.key, obsidian_path=str(path))
            log.info("Obsidian: wrote %s", path)
        except Exception:
            ok = False
            log.exception("Obsidian write failed for %s", lecture.key)

    if cfg.notion_enabled:
        try:
            page_id = notion_writer.publish(cfg, lecture, notes, prev.get("notion_page_id"), figures)
            state.update(lecture.key, notion_page_id=page_id)
            log.info("Notion: created page %s", page_id)
        except Exception:
            ok = False
            log.exception("Notion write failed for %s", lecture.key)

    if ok:  # only mark as done when every enabled output succeeded, so failures retry
        state.update(lecture.key, hash=digest, title=notes["title"])
    return ok


def scan_all(cfg: Config, state: State, force: bool = False) -> int:
    done = 0
    for folder in all_lecture_dirs(cfg.inbox):
        try:
            done += bool(process_lecture(cfg, state, folder, force=force))
        except Exception:
            log.exception("Failed processing %s", folder)
    return done
