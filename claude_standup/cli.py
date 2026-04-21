"""CLI module: argument parsing, pipeline orchestration, and entry point."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from claude_standup.cache import CacheDB
from claude_standup.classifier import classify_session
from claude_standup.llm import get_llm_backend
from claude_standup.models import GitInfo, LogEntry
from claude_standup.parser import (
    derive_project_name,
    discover_jsonl_files,
    parse_jsonl_file,
    resolve_git_remote,
)
from claude_standup.reporter import generate_report

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOGS_BASE = str(Path.home() / ".claude" / "projects")
DEFAULT_DB_PATH = str(Path.home() / ".claude-standup" / "cache.db")
VALID_TYPES = [
    "FEATURE",
    "BUGFIX",
    "REFACTOR",
    "DEBUGGING",
    "EXPLORATION",
    "REVIEW",
    "SUPPORT",
    "MEETING",
    "OTHER",
]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments and return a ``Namespace``.

    Subcommands: ``today``, ``yesterday``, ``last-7-days``, ``log``.
    When no subcommand is given the default is ``today``.
    """
    # Shared flags added to every subparser via parent inheritance
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--from", dest="date_from", default=None,
        help="Start date (YYYY-MM-DD). Overrides subcommand range.",
    )
    common.add_argument(
        "--to", dest="date_to", default=None,
        help="End date (YYYY-MM-DD). Overrides subcommand range.",
    )
    common.add_argument("--org", default=None, help="Filter by GitHub org (comma-separated).")
    common.add_argument("--repo", default=None, help="Filter by GitHub repo (comma-separated).")
    common.add_argument(
        "--lang", choices=["es", "en"], default="es",
        help="Report language (default: es).",
    )
    common.add_argument(
        "--format", choices=["markdown", "slack"], default="markdown",
        help="Output format (default: markdown).",
    )
    common.add_argument("--output", default=None, help="Write report to this file path.")
    common.add_argument("--reprocess", action="store_true", help="Re-process all log files.")
    common.add_argument("--verbose", action="store_true", help="Print progress to stderr.")

    parser = argparse.ArgumentParser(
        prog="claude-standup",
        description="Generate daily standup reports from Claude Code activity logs.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("today", parents=[common], help="Report for today.")
    subparsers.add_parser("yesterday", parents=[common], help="Report for yesterday.")
    subparsers.add_parser("last-7-days", parents=[common], help="Report for the last 7 days.")
    subparsers.add_parser("warmup", parents=[common], help="Pre-process all logs into cache (run once).")

    log_parser = subparsers.add_parser("log", parents=[common], help="Add a manual activity entry.")
    log_parser.add_argument("message", help="Activity description.")
    log_parser.add_argument(
        "--type", choices=VALID_TYPES, default="OTHER",
        help="Activity type (default: OTHER).",
    )

    args = parser.parse_args(argv)

    # Default command when none is provided
    if args.command is None:
        args.command = "today"
        # When no subcommand is given, the common-parent defaults are absent.
        # Apply them manually so callers always see every attribute.
        defaults = {
            "date_from": None,
            "date_to": None,
            "org": None,
            "repo": None,
            "lang": "es",
            "format": "markdown",
            "output": None,
            "reprocess": False,
            "verbose": False,
        }
        for key, value in defaults.items():
            if not hasattr(args, key):
                setattr(args, key, value)

    return args


# ---------------------------------------------------------------------------
# Date range resolution
# ---------------------------------------------------------------------------

def resolve_date_range(
    command: str,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, str]:
    """Return ``(from_date, to_date)`` as ISO strings.

    The *command* name determines the default range:
    - ``today``       -> today .. today
    - ``yesterday``   -> yesterday .. yesterday
    - ``last-7-days`` -> today-6 .. today

    Explicit *date_from* / *date_to* override the defaults.
    A partial override (only ``--from``) uses today as the ``to`` date.
    """
    today = date.today()

    if command == "yesterday":
        default_from = today - timedelta(days=1)
        default_to = default_from
    elif command == "last-7-days":
        default_from = today - timedelta(days=6)
        default_to = today
    else:  # "today" and any other fallback
        default_from = today
        default_to = today

    resolved_from = date_from if date_from is not None else default_from.isoformat()
    resolved_to = date_to if date_to is not None else default_to.isoformat()

    # Partial override: --from without --to uses today
    if date_from is not None and date_to is None:
        resolved_to = today.isoformat()

    return resolved_from, resolved_to


# ---------------------------------------------------------------------------
# Report pipeline
# ---------------------------------------------------------------------------

def _process_new_files(
    backend,
    db: CacheDB,
    logs_base: str,
    reprocess: bool = False,
    verbose: bool = False,
) -> int:
    """Parse and classify unprocessed JSONL files, storing results in the cache.

    Returns the number of files processed.
    """
    all_files = discover_jsonl_files(logs_base)
    if verbose:
        print(f"Discovered {len(all_files)} JSONL file(s).", file=sys.stderr)

    if reprocess:
        db.clear_file_tracking()

    files_to_process = db.get_unprocessed_files(all_files)
    total = len(files_to_process)
    if verbose:
        print(f"Files to process: {total}", file=sys.stderr)

    git_cache: dict[str, GitInfo] = {}

    for idx, fi in enumerate(files_to_process, 1):
        dir_name = Path(fi.path).parent.name
        if _looks_like_subagent_dir(dir_name):
            dir_name = Path(fi.path).parent.parent.name
        project = derive_project_name(dir_name)

        if verbose:
            print(f"[{idx}/{total}] {project or '(subagent)'}: {Path(fi.path).name}", file=sys.stderr)

        entries = parse_jsonl_file(fi.path, project)
        if not entries:
            db.mark_file_processed(fi.path, fi.mtime)
            continue

        first_cwd = entries[0].cwd
        git_info = resolve_git_remote(first_cwd, git_cache) if first_cwd else GitInfo()

        sessions: dict[str, list[LogEntry]] = {}
        for entry in entries:
            sessions.setdefault(entry.session_id, []).append(entry)

        for session_id, session_entries in sessions.items():
            timestamps = [e.timestamp for e in session_entries if e.timestamp]
            first_ts = min(timestamps) if timestamps else ""
            last_ts = max(timestamps) if timestamps else ""

            db.store_session(
                session_id=session_id,
                project=project,
                git_org=git_info.org,
                git_repo=git_info.repo,
                first_ts=first_ts,
                last_ts=last_ts,
            )

            activities = classify_session(
                backend, session_entries, git_org=git_info.org, git_repo=git_info.repo,
            )
            if activities:
                db.store_activities(activities)

        db.mark_file_processed(fi.path, fi.mtime)

    return total


def _run_report_pipeline(
    backend,
    db: CacheDB,
    logs_base: str,
    args: argparse.Namespace,
) -> str:
    """Execute the full report pipeline and return the formatted report text."""
    verbose = getattr(args, "verbose", False)

    date_from, date_to = resolve_date_range(args.command, args.date_from, args.date_to)
    if verbose:
        print(f"Date range: {date_from} .. {date_to}", file=sys.stderr)

    _process_new_files(
        backend, db, logs_base,
        reprocess=args.reprocess,
        verbose=verbose,
    )

    orgs = [o.strip() for o in args.org.split(",")] if args.org else None
    repos = [r.strip() for r in args.repo.split(",")] if args.repo else None
    activities = db.query_activities(date_from, date_to, orgs=orgs, repos=repos)

    if verbose:
        print(f"Activities found: {len(activities)}", file=sys.stderr)

    report = generate_report(backend, activities, lang=args.lang, output_format=args.format)
    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _looks_like_subagent_dir(name: str) -> bool:
    """Return True if *name* looks like a Claude subagent UUID directory."""
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", name))




# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(
    argv: list[str] | None = None,
    logs_base: str | None = None,
    db_path: str | None = None,
) -> None:
    """CLI entry point.

    - ``log`` command: stores a manual entry and prints confirmation to stderr.
      Does NOT require an API key.
    - Report commands (``today``, ``yesterday``, ``last-7-days``): require
      ``ANTHROPIC_API_KEY`` env var.  Runs the full pipeline, prints the report
      to stdout, and optionally writes to a file.
    """
    args = parse_args(argv)
    effective_db_path = db_path or DEFAULT_DB_PATH
    effective_logs_base = logs_base or DEFAULT_LOGS_BASE

    # -- log command: no API key needed ---
    if args.command == "log":
        # Ensure parent dir for db exists
        if effective_db_path != ":memory:":
            Path(effective_db_path).parent.mkdir(parents=True, exist_ok=True)

        db = CacheDB(effective_db_path)
        db.store_manual_entry(
            summary=args.message,
            classification=args.type,
            git_org=getattr(args, "org", None),
            git_repo=getattr(args, "repo", None),
        )
        db.close()
        print(f"Logged: [{args.type}] {args.message}", file=sys.stderr)
        return

    # -- warmup and report commands: require LLM backend ---
    try:
        backend = get_llm_backend()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if effective_db_path != ":memory:":
        Path(effective_db_path).parent.mkdir(parents=True, exist_ok=True)

    db = CacheDB(effective_db_path)

    try:
        if args.command == "warmup":
            verbose = getattr(args, "verbose", False)
            processed = _process_new_files(
                backend, db, effective_logs_base,
                reprocess=args.reprocess,
                verbose=verbose,
            )
            if processed == 0:
                print("Cache is up to date — nothing to process.", file=sys.stderr)
            else:
                print(f"Warmup complete: {processed} file(s) processed.", file=sys.stderr)
            return

        report = _run_report_pipeline(backend, db, effective_logs_base, args)
        print(report)

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
    finally:
        db.close()
