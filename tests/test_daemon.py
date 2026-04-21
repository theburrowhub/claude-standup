"""Tests for claude_standup.daemon module."""

from __future__ import annotations

import json
import os
import signal
import tempfile

import pytest
from unittest.mock import MagicMock

from claude_standup.cache import CacheDB
from claude_standup.daemon import (
    DaemonRunner,
    _looks_like_subagent_dir,
    is_daemon_running,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
)


# ---------------------------------------------------------------------------
# TestPidFile
# ---------------------------------------------------------------------------


class TestPidFile:
    """Tests for PID file read/write/remove helpers."""

    def test_write_and_read(self, tmp_path):
        pid_path = str(tmp_path / "daemon.pid")
        write_pid_file(pid_path, 12345)
        assert read_pid_file(pid_path) == 12345

    def test_read_nonexistent(self, tmp_path):
        pid_path = str(tmp_path / "nonexistent.pid")
        assert read_pid_file(pid_path) is None

    def test_remove(self, tmp_path):
        pid_path = str(tmp_path / "daemon.pid")
        write_pid_file(pid_path, 12345)
        remove_pid_file(pid_path)
        assert not os.path.exists(pid_path)

    def test_remove_nonexistent(self, tmp_path):
        pid_path = str(tmp_path / "nonexistent.pid")
        # Should not raise
        remove_pid_file(pid_path)


# ---------------------------------------------------------------------------
# TestIsDaemonRunning
# ---------------------------------------------------------------------------


class TestIsDaemonRunning:
    """Tests for is_daemon_running using real PID checks."""

    def test_no_pid_file(self, tmp_path):
        pid_path = str(tmp_path / "nonexistent.pid")
        assert is_daemon_running(pid_path) is False

    def test_stale_pid(self, tmp_path):
        pid_path = str(tmp_path / "daemon.pid")
        write_pid_file(pid_path, 999999999)
        assert is_daemon_running(pid_path) is False

    def test_running_pid(self, tmp_path):
        pid_path = str(tmp_path / "daemon.pid")
        write_pid_file(pid_path, os.getpid())
        assert is_daemon_running(pid_path) is True


# ---------------------------------------------------------------------------
# TestLooksLikeSubagentDir
# ---------------------------------------------------------------------------


class TestLooksLikeSubagentDir:
    """Tests for the _looks_like_subagent_dir helper."""

    def test_valid_uuid(self):
        assert _looks_like_subagent_dir("a1b2c3d4-e5f6-7890-abcd-ef1234567890") is True

    def test_not_uuid(self):
        assert _looks_like_subagent_dir("-Users-dev-workspace-myapp") is False

    def test_empty_string(self):
        assert _looks_like_subagent_dir("") is False


# ---------------------------------------------------------------------------
# TestDaemonRunner
# ---------------------------------------------------------------------------


class TestDaemonRunner:
    """Tests for the DaemonRunner class."""

    def test_single_cycle_parses_files(self, tmp_path):
        """Create a tmp dir with fake JSONL, call run_once(), verify DB entries."""
        # Set up a fake Claude Code log directory structure:
        # logs_base / -Users-dev-workspace-myproject / <uuid> / <jsonl>
        project_dir = tmp_path / "logs" / "-Users-dev-workspace-myproject"
        session_dir = project_dir / "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        session_dir.mkdir(parents=True)

        entry = {
            "type": "user",
            "message": {"role": "user", "content": "Implement the login feature"},
            "timestamp": "2026-04-21T10:00:00.000Z",
            "sessionId": "abc-123",
            "cwd": "/Users/dev/workspace/myproject",
            "gitBranch": "feat/login",
        }
        jsonl_file = session_dir / "conversation.jsonl"
        jsonl_file.write_text(json.dumps(entry) + "\n")

        db_path = str(tmp_path / "test.db")
        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path / "logs"), backend=None)
        count = runner.run_once()

        assert count == 1

        # Verify DB has the file and session
        db = CacheDB(db_path)
        file_row = db.conn.execute("SELECT COUNT(*) FROM files").fetchone()
        assert file_row[0] == 1

        session_row = db.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        assert session_row[0] == 1
        db.close()

    def test_classify_pending_sessions(self, tmp_path):
        """Pre-populate DB, mock backend, call classify_pending(), verify results."""
        db_path = str(tmp_path / "test.db")
        db = CacheDB(db_path)

        # Store an unclassified session
        db.store_session(
            "sess-001", "my-app", "acme", "my-app",
            "2026-04-21T08:00:00Z", "2026-04-21T09:00:00Z",
        )
        # Store raw prompts for the session
        db.store_raw_prompts("sess-001", [
            ("2026-04-21T08:00:00Z", "Implement the login feature"),
            ("2026-04-21T08:30:00Z", "Add OAuth2 support"),
        ])
        db.close()

        mock_backend = MagicMock()
        mock_backend.query.return_value = json.dumps({
            "activities": [{
                "classification": "FEATURE",
                "summary": "Built login",
                "files_mentioned": [],
                "technologies": [],
                "time_spent_minutes": 60,
                "prompt_indices": [0],
            }],
        })

        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path / "logs"), backend=mock_backend)
        count = runner.classify_pending()

        assert count == 1

        # Verify activities stored and session marked classified
        db = CacheDB(db_path)
        activities = db.query_activities("2026-04-21", "2026-04-21")
        assert len(activities) == 1
        assert activities[0].classification == "FEATURE"
        assert activities[0].summary == "Built login"

        session_row = db.conn.execute(
            "SELECT classified FROM sessions WHERE session_id = ?", ("sess-001",)
        ).fetchone()
        assert session_row[0] == 1
        db.close()

    def test_graceful_shutdown(self):
        """Verify should_run starts True and handle_signal sets it False."""
        runner = DaemonRunner(db_path=":memory:", logs_base="/tmp", backend=None)
        assert runner.should_run is True
        runner.handle_signal(signal.SIGTERM, None)
        assert runner.should_run is False

    def test_skips_classification_when_no_backend(self, tmp_path):
        """When backend is None, classify_pending should not raise."""
        db_path = str(tmp_path / "test.db")
        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path / "logs"), backend=None)
        count = runner.classify_pending()
        assert count == 0

    def test_empty_classification_marks_done(self, tmp_path):
        """When classification returns no activities, session is marked classified."""
        db_path = str(tmp_path / "test.db")
        db = CacheDB(db_path)

        db.store_session(
            "sess-002", "my-app", "acme", "my-app",
            "2026-04-21T08:00:00Z", "2026-04-21T09:00:00Z",
        )
        db.store_raw_prompts("sess-002", [
            ("2026-04-21T08:00:00Z", "Fix the bug"),
        ])
        db.close()

        mock_backend = MagicMock()
        # classify_session catches backend errors internally and returns []
        mock_backend.query.side_effect = RuntimeError("API failed")

        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path / "logs"), backend=mock_backend)
        count = runner.classify_pending()

        # Empty classification result → marked as classified to avoid infinite retry
        assert count == 1

        db = CacheDB(db_path)
        session_row = db.conn.execute(
            "SELECT classified FROM sessions WHERE session_id = ?", ("sess-002",)
        ).fetchone()
        assert session_row[0] == 1
        db.close()
