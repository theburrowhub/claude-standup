"""CLI module: argument parsing, pipeline orchestration, and entry point."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from claude_standup.cache import CacheDB
from claude_standup.llm import get_llm_backend
from claude_standup.models import GitInfo, LogEntry
from claude_standup.parser import (
    derive_project_name,
    discover_jsonl_files,
    parse_jsonl_file,
    resolve_git_remote,
)
from claude_standup.reporter import generate_report, generate_template_report

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

    Subcommands: ``today``, ``yesterday``, ``last-7-days``, ``log``,
    ``daemon``, ``status``.
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
    common.add_argument(
        "--template", action="store_true", default=False,
        help="Use local template (no LLM call).",
    )

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

    # daemon subcommand with nested action
    daemon_parser = subparsers.add_parser("daemon", help="Manage background daemon.")
    daemon_parser.add_argument(
        "daemon_action",
        choices=["start", "stop", "status", "run", "uninstall"],
        help="Daemon action.",
    )

    # status subcommand
    subparsers.add_parser("status", parents=[common], help="Show processing status.")

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
            "template": False,
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
# Report pipeline (read-only — no classification)
# ---------------------------------------------------------------------------

def _process_new_files(
    db: CacheDB,
    logs_base: str,
    reprocess: bool = False,
    verbose: bool = False,
) -> int:
    """Parse unprocessed JSONL files and store sessions + raw prompts in cache.

    This is a local-only operation — no LLM calls. Classification is done
    by the background daemon.

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

        # Group entries by session
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

            # Store raw user prompts for lazy classification
            user_prompts = [
                (e.timestamp, e.content)
                for e in session_entries
                if e.entry_type == "user_prompt"
            ]
            if user_prompts:
                db.store_raw_prompts(session_id, user_prompts)

        db.mark_file_processed(fi.path, fi.mtime)

    return total


def _run_report_pipeline(
    backend,
    db: CacheDB,
    logs_base: str,
    args: argparse.Namespace,
) -> str:
    """Execute the read-only report pipeline and return the formatted report text.

    Classification is handled by the background daemon.  This function only
    reads pre-classified activities and counts pending sessions for a warning.
    """
    verbose = getattr(args, "verbose", False)
    use_template = getattr(args, "template", False)

    date_from, date_to = resolve_date_range(args.command, args.date_from, args.date_to)
    if verbose:
        print(f"Date range: {date_from} .. {date_to}", file=sys.stderr)

    # Step 1: Parse new files (local, fast)
    _process_new_files(db, logs_base, reprocess=args.reprocess, verbose=verbose)

    orgs = [o.strip() for o in args.org.split(",")] if args.org else None
    repos = [r.strip() for r in args.repo.split(",")] if args.repo else None

    # Step 2: Query classified activities
    activities = db.query_activities(date_from, date_to, orgs=orgs, repos=repos)

    # Step 3: Count pending sessions for warning
    unclassified = db.get_unclassified_sessions(date_from, date_to, orgs=orgs, repos=repos)
    pending_count = len(unclassified)

    if verbose:
        print(f"Activities: {len(activities)}, Pending sessions: {pending_count}", file=sys.stderr)

    # Template mode: no LLM call
    if use_template:
        return generate_template_report(
            activities,
            output_format=args.format,
            pending_count=pending_count,
            lang=args.lang,
        )

    # LLM report
    if pending_count > 0:
        from claude_standup.daemon import is_daemon_running
        from claude_standup.service import DEFAULT_PID_PATH

        warning = f"Warning: {pending_count} session(s) pending classification"
        if is_daemon_running(DEFAULT_PID_PATH):
            warning += " (daemon: running)"
        else:
            warning += " (daemon: not running -- run 'claude-standup daemon start')"

        report = generate_report(backend, activities, lang=args.lang, output_format=args.format)
        return f"{warning}\n\n{report}"

    return generate_report(backend, activities, lang=args.lang, output_format=args.format)


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
    - ``daemon`` command: manage the background classification daemon.
    - ``status`` command: show processing statistics.
    - Report commands (``today``, ``yesterday``, ``last-7-days``): require
      ``ANTHROPIC_API_KEY`` env var unless ``--template`` is used.
      Runs the pipeline, prints the report to stdout, and optionally writes to a file.
    """
    args = parse_args(argv)
    effective_db_path = db_path or DEFAULT_DB_PATH
    effective_logs_base = logs_base or DEFAULT_LOGS_BASE

    # -- daemon command: manage background daemon ---
    if args.command == "daemon":
        _handle_daemon(args, effective_db_path, effective_logs_base)
        return

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

    if effective_db_path != ":memory:":
        Path(effective_db_path).parent.mkdir(parents=True, exist_ok=True)

    db = CacheDB(effective_db_path)

    try:
        # -- warmup: parse only, no LLM needed ---
        if args.command == "warmup":
            verbose = getattr(args, "verbose", False)
            processed = _process_new_files(
                db, effective_logs_base,
                reprocess=args.reprocess,
                verbose=verbose,
            )
            if processed == 0:
                print("Cache is up to date — nothing to process.", file=sys.stderr)
            else:
                print(f"Warmup complete: {processed} file(s) processed.", file=sys.stderr)
            return

        # -- status command ---
        if args.command == "status":
            _handle_status(db, effective_logs_base)
            return

        # -- Report commands ---
        use_template = getattr(args, "template", False)

        if use_template:
            # Template mode: no LLM backend needed
            backend = None
        else:
            try:
                backend = get_llm_backend()
            except RuntimeError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

        report = _run_report_pipeline(backend, db, effective_logs_base, args)
        print(report)

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Daemon command handler
# ---------------------------------------------------------------------------

def _handle_daemon(
    args: argparse.Namespace,
    effective_db_path: str,
    effective_logs_base: str,
) -> None:
    """Handle the ``daemon`` subcommand with its nested actions."""
    action = args.daemon_action

    if action == "run":
        from claude_standup.daemon import DaemonRunner, remove_pid_file, write_pid_file
        from claude_standup.service import DEFAULT_PID_PATH

        try:
            backend = get_llm_backend()
        except RuntimeError:
            backend = None  # daemon will skip classification

        if effective_db_path != ":memory:":
            Path(effective_db_path).parent.mkdir(parents=True, exist_ok=True)

        write_pid_file(DEFAULT_PID_PATH, os.getpid())
        try:
            runner = DaemonRunner(effective_db_path, effective_logs_base, backend)
            runner.run_forever()
        finally:
            remove_pid_file(DEFAULT_PID_PATH)

    elif action == "start":
        from claude_standup.service import get_service_manager

        mgr = get_service_manager()
        binary = shutil.which("claude-standup") or sys.argv[0]
        mgr.install(binary)
        print("Daemon installed and started.", file=sys.stderr)

    elif action == "stop":
        from claude_standup.service import get_service_manager

        mgr = get_service_manager()
        mgr.uninstall()
        print("Daemon stopped and uninstalled.", file=sys.stderr)

    elif action == "status":
        from claude_standup.service import DaemonStatus

        status = DaemonStatus.check()
        if status.running:
            print(f"Daemon: running (PID {status.pid})", file=sys.stderr)
        else:
            print("Daemon: not running", file=sys.stderr)

    elif action == "uninstall":
        from claude_standup.service import get_service_manager

        mgr = get_service_manager()
        mgr.uninstall()
        print("Daemon uninstalled.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Status command handler
# ---------------------------------------------------------------------------

def _handle_status(db: CacheDB, logs_base: str) -> None:
    """Handle the ``status`` subcommand — show processing statistics."""
    _process_new_files(db, logs_base, verbose=False)

    total = db.conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    classified = db.conn.execute(
        "SELECT count(*) FROM sessions WHERE classified = 1"
    ).fetchone()[0]
    pending = total - classified
    pct = int(classified / total * 100) if total > 0 else 100
    total_files = db.conn.execute("SELECT count(*) FROM files").fetchone()[0]

    from claude_standup.service import DaemonStatus

    status = DaemonStatus.check()
    daemon_str = f"running (PID {status.pid})" if status.running else "not running"

    print("Processing status:")
    print(f"  Files:    {total_files} parsed")
    print(f"  Sessions: {total} total, {classified} classified, {pending} pending ({pct}%)")
    print(f"  Daemon:   {daemon_str}")
