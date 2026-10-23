from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from .config import load_config, validate
from .pipeline import process_lecture, scan_all
from .state import State


def _setup_logging(log_file: Path) -> None:
    """Log to the console (if there is one) and to state/agent.log, which is what you read when it runs hidden."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # pythonw.exe has no console
        handlers.append(logging.StreamHandler())
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "ddgs", "primp", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lecture-agent", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="validate config and credentials")
    sub.add_parser("watch", help="watch the inbox and process new lectures automatically")

    p_scan = sub.add_parser("scan", help="process every lecture folder in the inbox once")
    p_scan.add_argument("--force", action="store_true", help="regenerate even if unchanged")

    p_one = sub.add_parser("process", help="process a single lecture folder")
    p_one.add_argument("folder", type=Path, help="e.g. inbox/COMP5xxx/week03")
    p_one.add_argument("--force", action="store_true")

    p_db = sub.add_parser("init-notion", help="create the Notion database under a parent page")
    p_db.add_argument("parent_page_id", help="id of a Notion page shared with your integration")

    args = parser.parse_args(argv)
    cfg = load_config()
    _setup_logging(cfg.state_file.parent / "agent.log")

    if args.cmd == "init-notion":
        from .writers.notion_writer import create_database
        if not cfg.notion_token:
            sys.exit("NOTION_TOKEN is not set in .env")
        db_id = create_database(cfg, args.parent_page_id)
        print(f"Created database. Put this in .env:\nNOTION_DATABASE_ID={db_id}")
        return

    problems = validate(cfg)
    if not problems and cfg.notion_enabled:  # catches a page id pasted where the database id belongs
        from .writers.notion_writer import verify_database
        msg = verify_database(cfg)
        if msg:
            problems.append(msg)
    if args.cmd == "check":
        print("\n".join(f"- {p}" for p in problems) if problems else "Config looks good.")
        sys.exit(1 if problems else 0)
    if problems:
        msg = "Config problems:\n" + "\n".join(f"- {p}" for p in problems) + "\nRun `lecture-agent check` after fixing."
        logging.getLogger(__name__).error(msg)  # also lands in agent.log for hidden runs
        sys.exit(1)

    state = State(cfg.state_file)
    if args.cmd == "watch":
        from .watcher import watch
        watch(cfg, state)
    elif args.cmd == "scan":
        n = scan_all(cfg, state, force=args.force)
        print(f"Processed {n} lecture(s).")
    elif args.cmd == "process":
        process_lecture(cfg, state, args.folder.resolve(), force=args.force)


if __name__ == "__main__":
    main()
