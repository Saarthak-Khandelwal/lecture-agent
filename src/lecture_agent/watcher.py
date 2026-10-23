from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .lectures import is_ignored, lecture_dir_for
from .pipeline import process_lecture, scan_all
from .state import State

log = logging.getLogger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, inbox: Path, pending: dict[Path, float], lock: threading.Lock):
        self.inbox, self.pending, self.lock = inbox, pending, lock

    def on_any_event(self, event):
        if event.is_directory:
            return
        for raw in (event.src_path, getattr(event, "dest_path", None)):
            if not raw:
                continue
            path = Path(raw)
            if is_ignored(path):
                continue
            folder = lecture_dir_for(path, self.inbox)
            if folder is not None:
                with self.lock:
                    self.pending[folder] = time.time()


def watch(cfg: Config, state: State) -> None:
    """Block forever; process a lecture folder once its files stop changing for `debounce_seconds`."""
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    log.info("Catching up on lectures added while the agent was not running")
    scan_all(cfg, state)  # unchanged lectures are skipped via their hash, so this is cheap
    pending: dict[Path, float] = {}
    lock = threading.Lock()

    observer = Observer()
    observer.schedule(_Handler(cfg.inbox, pending, lock), str(cfg.inbox), recursive=True)
    observer.start()
    log.info("Watching %s (debounce %ss). Drop files into inbox/<UNIT>/<LECTURE>/. Ctrl+C to stop.",
             cfg.inbox, cfg.debounce_seconds)
    try:
        while True:
            time.sleep(2)
            now = time.time()
            with lock:
                due = [f for f, t in pending.items() if now - t >= cfg.debounce_seconds]
                for f in due:
                    pending.pop(f)
            for folder in due:
                try:
                    process_lecture(cfg, state, folder)
                except Exception:
                    log.exception("Failed processing %s", folder)
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        observer.stop()
        observer.join()
