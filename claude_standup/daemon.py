"""Background daemon for continuous log processing and classification.

Discovers new Claude Code JSONL log files, parses them into sessions,
and classifies sessions into structured development activities using an
LLM backend.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import time

from claude_standup.cache import CacheDB
from claude_standup.classifier import classify_session
from claude_standup.models import LogEntry
from claude_standup.parser import (
    derive_project_name,
    discover_jsonl_files,
    parse_jsonl_file,
    resolve_git_remote,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PID file helpers
# ---------------------------------------------------------------------------


def write_pid_file(path: str, pid: int) -> None:
    """Write *pid* to *path*."""
    with open(path, "w") as f:
        f.write(str(pid))


def read_pid_file(path: str) -> int | None:
    """Read a PID from *path*. Return ``None`` if the file is missing or invalid."""
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def remove_pid_file(path: str) -> None:
    """Delete the PID file at *path*, ignoring errors if it doesn't exist."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def is_daemon_running(pid_path: str) -> bool:
    """Return ``True`` if a PID file exists at *pid_path* AND the process is alive."""
    pid = read_pid_file(pid_path)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_subagent_dir(name: str) -> bool:
    """Return ``True`` if *name* looks like a UUID (sub-agent session directory)."""
    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            name,
        )
    )


# ---------------------------------------------------------------------------
# DaemonRunner
# ---------------------------------------------------------------------------


class DaemonRunner:
    """Continuously discovers, parses, and classifies Claude Code log files."""

    def __init__(self, db_path: str, logs_base: str, backend) -> None:
        self.db_path = db_path
        self.logs_base = logs_base
        self.backend = backend
        self.should_run: bool = True

    # -- signal handling ----------------------------------------------------

    def handle_signal(self, signum, frame) -> None:  # noqa: ANN001
        """Signal handler that triggers a graceful shutdown."""
        logger.info("Received signal %s — shutting down.", signum)
        self.should_run = False

    # -- main loop ----------------------------------------------------------

    def run_forever(self, interval: int = 60) -> None:
        """Register signal handlers and loop: run_once -> classify_pending -> sleep."""
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        logger.info("Daemon started (interval=%ds).", interval)

        while self.should_run:
            try:
                processed = self.run_once()
                classified = self.classify_pending()
                if processed or classified:
                    logger.info(
                        "Cycle complete: %d files processed, %d sessions classified.",
                        processed,
                        classified,
                    )
            except Exception:
                logger.exception("Error during daemon cycle.")

            # Sleep in 1-second increments so we can react to signals quickly
            for _ in range(interval):
                if not self.should_run:
                    break
                time.sleep(1)

        logger.info("Daemon stopped.")

    # -- single processing cycle --------------------------------------------

    def run_once(self) -> int:
        """Discover and parse unprocessed JSONL files. Returns the count of files processed."""
        all_files = discover_jsonl_files(self.logs_base)
        if not all_files:
            return 0

        db = CacheDB(self.db_path)
        try:
            unprocessed = db.get_unprocessed_files(all_files)
            if not unprocessed:
                return 0

            git_cache: dict = {}
            count = 0

            for file_info in unprocessed:
                try:
                    self._process_file(db, file_info, git_cache)
                    count += 1
                except Exception:
                    logger.warning("Failed to process %s", file_info.path, exc_info=True)

            return count
        finally:
            db.close()

    def _process_file(self, db: CacheDB, file_info, git_cache: dict) -> None:
        """Parse a single file and store its sessions and raw prompts."""
        from pathlib import Path

        path = file_info.path

        # Derive project name from the directory structure.
        # The log directory structure is: logs_base / <project-dir> / [<uuid>/] <file>.jsonl
        # We need to find the project-level directory (the one directly under logs_base).
        rel = os.path.relpath(path, self.logs_base)
        parts = Path(rel).parts
        # The first part is the project directory name
        dir_name = parts[0] if parts else ""

        # Check if the immediate parent is a subagent UUID directory
        parent_name = os.path.basename(os.path.dirname(path))
        if _looks_like_subagent_dir(parent_name):
            # The project dir is one level above the subagent dir
            if len(parts) >= 2:
                dir_name = parts[0]

        project_name = derive_project_name(dir_name)

        entries = parse_jsonl_file(path, project_name)
        if not entries:
            db.mark_file_processed(path, file_info.mtime)
            return

        # Group entries by session_id
        sessions: dict[str, list[LogEntry]] = {}
        for entry in entries:
            sessions.setdefault(entry.session_id, []).append(entry)

        for session_id, session_entries in sessions.items():
            # Resolve git remote from the first entry's cwd
            first_entry = session_entries[0]
            git_info = resolve_git_remote(first_entry.cwd, git_cache)

            timestamps = [e.timestamp for e in session_entries if e.timestamp]
            first_ts = min(timestamps) if timestamps else ""
            last_ts = max(timestamps) if timestamps else ""

            db.store_session(
                session_id=session_id,
                project=project_name,
                git_org=git_info.org,
                git_repo=git_info.repo,
                first_ts=first_ts,
                last_ts=last_ts,
            )

            # Store raw user prompts
            user_prompts = [
                (e.timestamp, e.content)
                for e in session_entries
                if e.entry_type == "user_prompt"
            ]
            if user_prompts:
                db.store_raw_prompts(session_id, user_prompts)

        db.mark_file_processed(path, file_info.mtime)

    # -- classification cycle -----------------------------------------------

    def classify_pending(self) -> int:
        """Classify all unclassified sessions. Returns count of sessions classified."""
        if self.backend is None:
            return 0

        db = CacheDB(self.db_path)
        try:
            sessions = db.get_unclassified_sessions("2000-01-01", "2099-12-31")
            if not sessions:
                return 0

            count = 0
            for sess in sessions:
                if not self.should_run:
                    break

                session_id = sess["session_id"]
                project = sess["project"]
                git_org = sess["git_org"]
                git_repo = sess["git_repo"]

                # Reconstruct LogEntry objects from stored raw prompts
                raw_prompts = db.get_raw_prompts(session_id)
                if not raw_prompts:
                    # No prompts to classify — mark classified to avoid infinite retries
                    db.mark_session_classified(session_id)
                    count += 1
                    continue

                entries = [
                    LogEntry(
                        timestamp=ts,
                        session_id=session_id,
                        project=project,
                        entry_type="user_prompt",
                        content=content,
                        cwd="",
                    )
                    for ts, content in raw_prompts
                ]

                try:
                    activities = classify_session(
                        self.backend, entries, git_org, git_repo
                    )
                except Exception:
                    logger.warning(
                        "Failed to classify session %s — will retry next cycle.",
                        session_id,
                        exc_info=True,
                    )
                    continue

                if not activities:
                    # Empty result — could be legitimate (noise-only session)
                    # or transient failure. Mark as classified to avoid infinite retry.
                    logger.info(
                        "No activities for session %s — marking as classified.",
                        session_id,
                    )
                    db.mark_session_classified(session_id)
                    count += 1
                    continue

                db.store_activities(activities)
                db.mark_session_classified(session_id)
                count += 1

            return count
        finally:
            db.close()
