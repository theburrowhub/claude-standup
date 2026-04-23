"""CLI module: argument parsing, pipeline orchestration, and entry point."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from claude_standup.cache import CacheDB
from claude_standup.models import Activity, GitInfo
from claude_standup.parser import (
    derive_project_name,
    discover_jsonl_files,
    parse_session_summaries,
    resolve_git_remote,
)
from claude_standup.reporter import generate_template_report, generate_llm_report

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOGS_BASE = str(Path.home() / ".claude" / "projects")
DEFAULT_DB_PATH = str(Path.home() / ".claude-standup" / "cache.db")
DEFAULT_LOOKBACK_DAYS = 7
VALID_TYPES = [
    "FEATURE", "BUGFIX", "REFACTOR", "DEBUGGING", "EXPLORATION",
    "REVIEW", "SUPPORT", "MEETING", "OTHER",
]


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--from", dest="date_from", default=None, help="Start date (YYYY-MM-DD).")
    common.add_argument("--to", dest="date_to", default=None, help="End date (YYYY-MM-DD).")
    common.add_argument("--org", default=None, help="Filter by GitHub org (comma-separated).")
    common.add_argument("--repo", default=None, help="Filter by GitHub repo (comma-separated).")
    common.add_argument("--lang", choices=["es", "en"], default="es", help="Report language (default: es).")
    common.add_argument("--format", choices=["markdown", "slack"], default="markdown", help="Output format (default: markdown).")
    common.add_argument("--output", default=None, help="Write report to this file path.")
    common.add_argument("--raw", action="store_true", help="Use local template only (no LLM polish).")
    common.add_argument("--verbose", action="store_true", help="Print progress to stderr.")

    parser = argparse.ArgumentParser(
        prog="claude-standup",
        description="Generate daily standup reports from Claude Code activity logs.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("today", parents=[common], help="Report for today.")
    subparsers.add_parser("yesterday", parents=[common], help="Report for yesterday.")
    subparsers.add_parser("last-7-days", parents=[common], help="Report for the last 7 days.")
    subparsers.add_parser("status", parents=[common], help="Show processing status.")

    log_parser = subparsers.add_parser("log", parents=[common], help="Add a manual activity entry.")
    log_parser.add_argument("message", help="Activity description.")
    log_parser.add_argument("--type", choices=VALID_TYPES, default="OTHER", help="Activity type (default: OTHER).")

    args = parser.parse_args(argv)

    if args.command is None:
        args.command = "today"
        for key, value in {"date_from": None, "date_to": None, "org": None, "repo": None,
                           "lang": "es", "format": "markdown", "output": None, "raw": False, "verbose": False}.items():
            if not hasattr(args, key):
                setattr(args, key, value)

    return args


# ---------------------------------------------------------------------------
# Date range
# ---------------------------------------------------------------------------

def resolve_date_range(command: str, date_from: str | None, date_to: str | None) -> tuple[str, str]:
    today = date.today()
    if command == "yesterday":
        default_from = today - timedelta(days=1)
        default_to = default_from
    elif command == "last-7-days":
        default_from = today - timedelta(days=6)
        default_to = today
    else:
        default_from = today
        default_to = today

    resolved_from = date_from if date_from is not None else default_from.isoformat()
    resolved_to = date_to if date_to is not None else default_to.isoformat()
    if date_from is not None and date_to is None:
        resolved_to = today.isoformat()
    return resolved_from, resolved_to


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _looks_like_subagent_dir(name: str) -> bool:
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", name))


def _process_files(
    db: CacheDB,
    logs_base: str,
    verbose: bool = False,
) -> None:
    """Parse new/modified files, extract away_summaries, store as activities.

    Uses away_summary entries as the primary data source — these are
    high-quality session recaps written by Claude Code itself.
    """
    all_files = discover_jsonl_files(logs_base)
    all_files = [f for f in all_files if "/subagents/" not in f.path]

    if verbose:
        print(f"Discovered {len(all_files)} JSONL file(s).", file=sys.stderr)

    to_process = db.get_unprocessed_files(all_files)

    if verbose:
        print(f"New/modified: {len(to_process)}", file=sys.stderr)

    git_cache: dict[str, GitInfo] = {}

    for idx, fi in enumerate(to_process, 1):
        dir_name = Path(fi.path).parent.name
        if _looks_like_subagent_dir(dir_name):
            dir_name = Path(fi.path).parent.parent.name
        project = derive_project_name(dir_name)

        if verbose:
            print(f"  [{idx}/{len(to_process)}] {project or '?'}", file=sys.stderr)

        summaries = parse_session_summaries(fi.path, project)

        for s in summaries:
            cwd = s["cwd"]
            git_info = resolve_git_remote(cwd, git_cache) if cwd else GitInfo()
            day = s["timestamp"][:10]

            db.store_activities([Activity(
                session_id=s["session_id"],
                day=day,
                project=project,
                git_org=git_info.org,
                git_repo=git_info.repo,
                classification="",  # not needed — summary speaks for itself
                summary=s["content"],
            )])

        db.mark_file_processed(fi.path, fi.mtime)


def _run_report(db: CacheDB, logs_base: str, args: argparse.Namespace) -> str:
    """Full pipeline: process files → query → generate report. All local."""
    verbose = getattr(args, "verbose", False)
    date_from, date_to = resolve_date_range(args.command, args.date_from, args.date_to)

    if verbose:
        print(f"Date range: {date_from} .. {date_to}", file=sys.stderr)

    _process_files(db, logs_base, verbose=verbose)

    orgs = [o.strip() for o in args.org.split(",")] if args.org else None
    repos = [r.strip() for r in args.repo.split(",")] if args.repo else None
    activities = db.query_activities(date_from, date_to, orgs=orgs, repos=repos)

    if verbose:
        print(f"Activities found: {len(activities)}", file=sys.stderr)

    use_raw = getattr(args, "raw", False)
    if use_raw:
        return generate_template_report(activities, output_format=args.format, lang=args.lang)

    # Default: LLM polishes the report (1 call, cleans noise, writes proper standup)
    return generate_llm_report(activities, output_format=args.format, lang=args.lang)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None, logs_base: str | None = None, db_path: str | None = None) -> None:
    args = parse_args(argv)
    effective_db_path = db_path or DEFAULT_DB_PATH
    effective_logs_base = logs_base or DEFAULT_LOGS_BASE

    # Log command
    if args.command == "log":
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

    # Status command
    if args.command == "status":
        if effective_db_path != ":memory:":
            Path(effective_db_path).parent.mkdir(parents=True, exist_ok=True)
        db = CacheDB(effective_db_path)
        total_files = db.conn.execute("SELECT count(*) FROM files").fetchone()[0]
        total_sessions = db.conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        total_activities = db.conn.execute("SELECT count(*) FROM activities").fetchone()[0]
        db.close()
        print(f"Processing status:")
        print(f"  Files parsed:   {total_files}")
        print(f"  Sessions:       {total_sessions}")
        print(f"  Activities:     {total_activities}")
        return

    # Report commands
    if effective_db_path != ":memory:":
        Path(effective_db_path).parent.mkdir(parents=True, exist_ok=True)
    db = CacheDB(effective_db_path)
    try:
        report = _run_report(db, effective_logs_base, args)
        print(report)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(report)
    finally:
        db.close()
