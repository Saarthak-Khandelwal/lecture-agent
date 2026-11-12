from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .lectures import all_lecture_dirs, is_ignored, lecture_dir_for
from .llm import LLMUnavailable
from .pipeline import process_lecture
from .state import State

log = logging.getLogger(__name__)

RETRY_DELAY = 3 * 3600   # X: wait this long (seconds) before retrying a failed lecture
SERVER_RETRY_DELAY = 30 * 60   # temporary 503 overload: try again sooner


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
    """Process a lecture folder once its files stop changing; retry failures after RETRY_DELAY."""
    cfg.inbox.mkdir(parents=True, exist_ok=True)
    pending: dict[Path, float] = {}    # folder -> time of last file change
    retry_at: dict[Path, float] = {}   # folder -> when to retry it
    attempts: dict[Path, int] = {}     # folder -> failed retries so far
    lock = threading.Lock()
    paused_until = 0.0

    # Catch-up: queue every lecture folder (unchanged ones are skipped via their hash)
    log.info("Catching up on lectures added while the agent was not running")
    for folder in all_lecture_dirs(cfg.inbox):
        pending[folder] = 0.0

    observer = Observer()
    observer.schedule(_Handler(cfg.inbox, pending, lock), str(cfg.inbox), recursive=True)
    observer.start()
    log.info("Watching %s (debounce %ss, retry after %d min). Ctrl+C to stop.",
             cfg.inbox, cfg.debounce_seconds, RETRY_DELAY // 60)

    try:
        while True:
            time.sleep(2)
            now = time.time()

            with lock:
                due = [f for f, t in pending.items() if now - t >= cfg.debounce_seconds]
                for f in due:
                    pending.pop(f)
            for f in due:
                retry_at.pop(f, None)   # a fresh change replaces any scheduled retry
            for f in [f for f, t in retry_at.items() if t <= now]:
                retry_at.pop(f)
                due.append(f)

            if not due:
                continue
            if now < paused_until:      # model unavailable: hold everything until the pause ends
                for f in due:
                    retry_at[f] = paused_until
                continue

            for i, folder in enumerate(due):
                try:
                    process_lecture(cfg, state, folder)
                    attempts.pop(folder, None)
                except LLMUnavailable as e:
                    delay = RETRY_DELAY if e.quota else SERVER_RETRY_DELAY
                    paused_until = time.time() + delay
                    for f in due[i:]:   # this lecture and the ones not yet tried
                        retry_at[f] = paused_until
                    log.warning("%s. Pausing for %d min; %d lecture(s) queued.",
                                e, delay // 60, len(due) - i)
                    break
                except Exception:
                    n = attempts[folder] = attempts.get(folder, 0) + 1
                    delay = min(RETRY_DELAY * 2 ** (n - 1), 24 * 3600)   # 3h, 6h, 12h, then daily
                    retry_at[folder] = time.time() + delay
                    log.exception("Failed processing %s (attempt %d, retrying in %d min)",
                                  folder, n, delay // 60)
    except KeyboardInterrupt:
        log.info("Stopping")
    finally:
        observer.stop()
        observer.join()