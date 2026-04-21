# claude-standup v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a background daemon that continuously classifies sessions, a template reporter for instant reports, native OS packaging, and CI/CD — so the user never waits for classification.

**Architecture:** Background daemon (daemon.py) runs on login via launchd/systemd, continuously parsing JSONL files and classifying sessions with `claude -p`. The CLI frontend reads pre-classified data from SQLite and generates reports instantly. PyInstaller builds standalone binaries; GitHub Actions creates .pkg/.deb/.rpm installers.

**Tech Stack:** Python 3.11+, SQLite, PyInstaller, GitHub Actions, launchd (macOS), systemd (Linux)

**Spec:** `docs/superpowers/specs/2026-04-21-claude-standup-v2-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `claude_standup/daemon.py` | Background daemon: main loop, PID management, signal handling, logging |
| `claude_standup/service.py` | OS service install/uninstall/status (launchd + systemd abstraction) |
| `claude_standup/reporter.py` | Modified: add `generate_template_report()` for instant local reports |
| `claude_standup/cli.py` | Modified: add `daemon` subcommand, `--template` flag, remove sync classification |
| `installer/macos/com.claude-standup.daemon.plist` | launchd service definition |
| `installer/macos/build.sh` | PyInstaller + pkgbuild for macOS .pkg |
| `installer/macos/postinstall.sh` | .pkg postinstall: install plist + start daemon |
| `installer/macos/preinstall.sh` | .pkg preinstall: stop existing daemon |
| `installer/linux/claude-standup.service` | systemd user service definition |
| `installer/linux/build.sh` | PyInstaller + dpkg-deb for Linux .deb |
| `installer/linux/postinst` | .deb postinst: enable + start systemd service |
| `installer/linux/prerm` | .deb prerm: stop + disable systemd service |
| `installer/homebrew/claude-standup.rb` | Homebrew formula |
| `.github/workflows/release.yml` | CI/CD: test → build → release → homebrew |
| `claude-standup.spec` | PyInstaller spec file for building standalone binary |
| `tests/test_daemon.py` | Daemon unit tests |
| `tests/test_service.py` | Service install/uninstall/status tests |
| `tests/test_template_reporter.py` | Template reporter tests |

---

### Task 1: Template Reporter

**Files:**
- Modify: `claude_standup/reporter.py`
- Create: `tests/test_template_reporter.py`

- [ ] **Step 1: Write failing tests for template reporter**

`tests/test_template_reporter.py`:

```python
"""Tests for template (no-LLM) report generation."""

from __future__ import annotations

from claude_standup.models import Activity
from claude_standup.reporter import generate_template_report


def _activities() -> list[Activity]:
    return [
        Activity(
            session_id="s1", day="2026-04-21", project="my-app",
            git_org="acme", git_repo="my-app", classification="FEATURE",
            summary="Implemented login with OAuth2",
            time_spent_minutes=45,
        ),
        Activity(
            session_id="s1", day="2026-04-21", project="my-app",
            git_org="acme", git_repo="my-app", classification="BUGFIX",
            summary="Fixed session expiration",
            time_spent_minutes=20,
        ),
        Activity(
            session_id="manual", day="2026-04-21", project="",
            git_org=None, git_repo=None, classification="MEETING",
            summary="Sprint planning with backend team",
        ),
    ]


class TestMarkdownTemplate:
    def test_basic_format(self):
        report = generate_template_report(_activities(), output_format="markdown")
        assert "## 2026-04-21" in report
        assert "### Done" in report
        assert "[FEATURE](acme/my-app)" in report
        assert "~45min" in report
        assert "[MEETING]" in report

    def test_no_pending_when_zero(self):
        report = generate_template_report(_activities(), output_format="markdown", pending_count=0)
        assert "Pending" not in report

    def test_pending_warning(self):
        report = generate_template_report(_activities(), output_format="markdown", pending_count=3)
        assert "### Pending classification" in report
        assert "3 sessions" in report

    def test_empty_activities_no_pending(self):
        report = generate_template_report([], output_format="markdown", pending_count=0)
        assert "No activity found" in report or "No se encontró" in report

    def test_empty_activities_with_pending(self):
        report = generate_template_report([], output_format="markdown", pending_count=5)
        assert "5 sessions" in report

    def test_multi_day(self):
        acts = _activities() + [
            Activity(
                session_id="s2", day="2026-04-20", project="api",
                git_org="acme", git_repo="api", classification="REFACTOR",
                summary="Refactored DB layer",
                time_spent_minutes=60,
            ),
        ]
        report = generate_template_report(acts, output_format="markdown")
        assert "## 2026-04-20" in report
        assert "## 2026-04-21" in report


class TestSlackTemplate:
    def test_basic_format(self):
        report = generate_template_report(_activities(), output_format="slack")
        assert "*2026-04-21*" in report
        assert "*Done*" in report
        assert "•" in report
        assert "[FEATURE](acme/my-app)" in report

    def test_pending_warning(self):
        report = generate_template_report(_activities(), output_format="slack", pending_count=2)
        assert "*Pending*" in report
        assert "2 sessions" in report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_template_reporter.py -v`
Expected: `ImportError: cannot import name 'generate_template_report'`

- [ ] **Step 3: Implement generate_template_report**

Add to `claude_standup/reporter.py`:

```python
from itertools import groupby
from operator import attrgetter


def generate_template_report(
    activities: list[Activity],
    output_format: str = "markdown",
    pending_count: int = 0,
    lang: str = "es",
) -> str:
    """Generate a report from a local template — no LLM call.

    Groups activities by day, one bullet per activity. Includes a pending
    classification warning if *pending_count* > 0.
    """
    if not activities and pending_count == 0:
        if lang == "en":
            return "No activity found for the requested period."
        return "No se encontró actividad para el período solicitado."

    if output_format == "slack":
        return _template_slack(activities, pending_count)
    return _template_markdown(activities, pending_count)


def _format_activity_line(act: Activity) -> str:
    """Format a single activity as a summary line (shared between formats)."""
    org_repo = ""
    if act.git_org and act.git_repo:
        org_repo = f"({act.git_org}/{act.git_repo})"
    elif act.git_org:
        org_repo = f"({act.git_org})"
    time_part = f" ~{act.time_spent_minutes}min" if act.time_spent_minutes else ""
    return f"[{act.classification}]{org_repo} {act.summary}{time_part}"


def _template_markdown(activities: list[Activity], pending_count: int) -> str:
    lines: list[str] = []
    sorted_acts = sorted(activities, key=attrgetter("day"))
    for day, group in groupby(sorted_acts, key=attrgetter("day")):
        lines.append(f"## {day}")
        lines.append("")
        lines.append("### Done")
        for act in group:
            lines.append(f"- {_format_activity_line(act)}")
        if pending_count > 0:
            lines.append("")
            lines.append("### Pending classification")
            lines.append(f"- {pending_count} sessions being processed by daemon")
        lines.append("")

    if not activities and pending_count > 0:
        lines.append("### Pending classification")
        lines.append(f"- {pending_count} sessions being processed by daemon")
        lines.append("")

    return "\n".join(lines).rstrip()


def _template_slack(activities: list[Activity], pending_count: int) -> str:
    lines: list[str] = []
    sorted_acts = sorted(activities, key=attrgetter("day"))
    for day, group in groupby(sorted_acts, key=attrgetter("day")):
        lines.append(f"*{day}*")
        lines.append("")
        lines.append("*Done*")
        for act in group:
            lines.append(f"\u2022 {_format_activity_line(act)}")
        if pending_count > 0:
            lines.append("")
            lines.append("*Pending*")
            lines.append(f"\u2022 {pending_count} sessions being processed by daemon")
        lines.append("")

    if not activities and pending_count > 0:
        lines.append("*Pending*")
        lines.append(f"\u2022 {pending_count} sessions being processed by daemon")
        lines.append("")

    return "\n".join(lines).rstrip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_template_reporter.py -v`
Expected: all passed

- [ ] **Step 5: Run full suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -v`
Expected: all passed, no regressions

- [ ] **Step 6: Commit**

```bash
git add claude_standup/reporter.py tests/test_template_reporter.py
git commit -m "feat: add template reporter for instant local reports without LLM"
```

---

### Task 2: Daemon

**Files:**
- Create: `claude_standup/daemon.py`
- Create: `tests/test_daemon.py`

- [ ] **Step 1: Write failing tests for daemon**

`tests/test_daemon.py`:

```python
"""Tests for claude_standup.daemon module."""

from __future__ import annotations

import signal
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from claude_standup.daemon import (
    DaemonRunner,
    write_pid_file,
    read_pid_file,
    remove_pid_file,
    is_daemon_running,
)


class TestPidFile:
    def test_write_and_read(self, tmp_path: Path):
        pid_path = str(tmp_path / "test.pid")
        write_pid_file(pid_path, 12345)
        assert read_pid_file(pid_path) == 12345

    def test_read_nonexistent(self, tmp_path: Path):
        assert read_pid_file(str(tmp_path / "nope.pid")) is None

    def test_remove(self, tmp_path: Path):
        pid_path = str(tmp_path / "test.pid")
        write_pid_file(pid_path, 12345)
        remove_pid_file(pid_path)
        assert not Path(pid_path).exists()

    def test_remove_nonexistent(self, tmp_path: Path):
        remove_pid_file(str(tmp_path / "nope.pid"))  # should not raise


class TestIsDaemonRunning:
    def test_no_pid_file(self, tmp_path: Path):
        assert is_daemon_running(str(tmp_path / "nope.pid")) is False

    def test_stale_pid(self, tmp_path: Path):
        pid_path = str(tmp_path / "test.pid")
        write_pid_file(pid_path, 999999999)  # PID that doesn't exist
        assert is_daemon_running(pid_path) is False

    def test_running_pid(self, tmp_path: Path):
        import os
        pid_path = str(tmp_path / "test.pid")
        write_pid_file(pid_path, os.getpid())  # our own PID
        assert is_daemon_running(pid_path) is True


class TestDaemonRunner:
    def test_single_cycle_parses_files(self, tmp_path: Path):
        db_path = str(tmp_path / "cache.db")
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        # Create a fake JSONL file
        project_dir = logs_dir / "-Users-dev-workspace-app"
        project_dir.mkdir()
        (project_dir / "sess.jsonl").write_text(
            '{"type":"user","message":{"role":"user","content":"test prompt"},'
            '"timestamp":"2026-04-21T10:00:00Z","sessionId":"s1","cwd":"/tmp"}\n'
        )

        mock_backend = MagicMock()
        mock_backend.query.return_value = '{"activities":[]}'

        runner = DaemonRunner(
            db_path=db_path,
            logs_base=str(logs_dir),
            backend=mock_backend,
        )
        runner.run_once()

        # Verify file was processed
        from claude_standup.cache import CacheDB
        db = CacheDB(db_path)
        files = db.conn.execute("SELECT count(*) FROM files").fetchone()[0]
        assert files == 1

        sessions = db.conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
        assert sessions == 1
        db.close()

    def test_classify_pending_sessions(self, tmp_path: Path):
        db_path = str(tmp_path / "cache.db")

        from claude_standup.cache import CacheDB
        db = CacheDB(db_path)
        db.store_session("s1", "app", "acme", "app", "2026-04-21T10:00:00Z", "2026-04-21T11:00:00Z")
        db.store_raw_prompts("s1", [("2026-04-21T10:00:00Z", "Build login feature")])
        db.close()

        mock_backend = MagicMock()
        mock_backend.query.return_value = (
            '{"activities":[{"classification":"FEATURE","summary":"Built login",'
            '"files_mentioned":[],"technologies":[],"time_spent_minutes":60,"prompt_indices":[0]}]}'
        )

        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path), backend=mock_backend)
        runner.classify_pending()

        db = CacheDB(db_path)
        activities = db.query_activities("2026-04-21", "2026-04-21")
        assert len(activities) == 1
        assert activities[0].classification == "FEATURE"

        classified = db.conn.execute("SELECT classified FROM sessions WHERE session_id='s1'").fetchone()[0]
        assert classified == 1
        db.close()

    def test_graceful_shutdown(self, tmp_path: Path):
        runner = DaemonRunner(
            db_path=str(tmp_path / "cache.db"),
            logs_base=str(tmp_path),
            backend=MagicMock(),
        )
        assert runner.should_run is True
        runner.handle_signal(signal.SIGTERM, None)
        assert runner.should_run is False

    def test_skips_classification_when_no_backend(self, tmp_path: Path):
        db_path = str(tmp_path / "cache.db")
        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path), backend=None)
        runner.classify_pending()  # should not raise

    def test_failed_classification_retries(self, tmp_path: Path):
        db_path = str(tmp_path / "cache.db")

        from claude_standup.cache import CacheDB
        db = CacheDB(db_path)
        db.store_session("s1", "app", None, None, "2026-04-21T10:00:00Z", "2026-04-21T11:00:00Z")
        db.store_raw_prompts("s1", [("2026-04-21T10:00:00Z", "test")])
        db.close()

        mock_backend = MagicMock()
        mock_backend.query.side_effect = RuntimeError("CLI failed")

        runner = DaemonRunner(db_path=db_path, logs_base=str(tmp_path), backend=mock_backend)
        runner.classify_pending()

        # Session should still be unclassified (will retry next cycle)
        db = CacheDB(db_path)
        row = db.conn.execute("SELECT classified FROM sessions WHERE session_id='s1'").fetchone()
        assert row[0] == 0
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_daemon.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement daemon module**

`claude_standup/daemon.py`:

```python
"""Background daemon that watches Claude Code logs and classifies sessions."""

from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path

from claude_standup.cache import CacheDB
from claude_standup.classifier import classify_session
from claude_standup.models import GitInfo, LogEntry
from claude_standup.parser import (
    derive_project_name,
    discover_jsonl_files,
    parse_jsonl_file,
    resolve_git_remote,
)

logger = logging.getLogger("claude-standup-daemon")

DEFAULT_CYCLE_INTERVAL = 60  # seconds


# ---------------------------------------------------------------------------
# PID file management
# ---------------------------------------------------------------------------

def write_pid_file(path: str, pid: int) -> None:
    Path(path).write_text(str(pid))


def read_pid_file(path: str) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def remove_pid_file(path: str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def is_daemon_running(pid_path: str) -> bool:
    pid = read_pid_file(pid_path)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Daemon runner
# ---------------------------------------------------------------------------

def _looks_like_subagent_dir(name: str) -> bool:
    import re
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", name))


class DaemonRunner:
    """Runs the parse → classify loop."""

    def __init__(self, db_path: str, logs_base: str, backend):
        self.db_path = db_path
        self.logs_base = logs_base
        self.backend = backend
        self.should_run = True

    def handle_signal(self, signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        self.should_run = False

    def run_forever(self, interval: int = DEFAULT_CYCLE_INTERVAL) -> None:
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        logger.info("Daemon started. Logs: %s, DB: %s", self.logs_base, self.db_path)

        while self.should_run:
            try:
                self.run_once()
                self.classify_pending()
            except Exception:
                logger.exception("Error in daemon cycle")

            # Sleep in small increments to respond to signals quickly
            for _ in range(interval):
                if not self.should_run:
                    break
                time.sleep(1)

        logger.info("Daemon stopped.")

    def run_once(self) -> int:
        """Parse new/modified JSONL files. Returns number of files processed."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        db = CacheDB(self.db_path)
        try:
            all_files = discover_jsonl_files(self.logs_base)
            to_process = db.get_unprocessed_files(all_files)

            if not to_process:
                return 0

            logger.info("Processing %d new file(s)...", len(to_process))
            git_cache: dict[str, GitInfo] = {}

            for fi in to_process:
                dir_name = Path(fi.path).parent.name
                if _looks_like_subagent_dir(dir_name):
                    dir_name = Path(fi.path).parent.parent.name
                project = derive_project_name(dir_name)

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

                    db.store_session(session_id, project, git_info.org, git_info.repo, first_ts, last_ts)

                    user_prompts = [
                        (e.timestamp, e.content)
                        for e in session_entries
                        if e.entry_type == "user_prompt"
                    ]
                    if user_prompts:
                        db.store_raw_prompts(session_id, user_prompts)

                db.mark_file_processed(fi.path, fi.mtime)

            logger.info("Processed %d file(s).", len(to_process))
            return len(to_process)
        finally:
            db.close()

    def classify_pending(self) -> int:
        """Classify unclassified sessions one at a time. Returns count classified."""
        if self.backend is None:
            return 0

        db = CacheDB(self.db_path)
        try:
            # Get ALL unclassified sessions (no date filter — daemon classifies everything)
            unclassified = db.get_unclassified_sessions("2000-01-01", "2099-12-31")
            if not unclassified:
                return 0

            logger.info("Classifying %d pending session(s)...", len(unclassified))
            classified = 0

            for sess in unclassified:
                if not self.should_run:
                    break

                raw = db.get_raw_prompts(sess["session_id"])
                if not raw:
                    db.mark_session_classified(sess["session_id"])
                    classified += 1
                    continue

                entries = [
                    LogEntry(
                        timestamp=ts, session_id=sess["session_id"],
                        project=sess["project"], entry_type="user_prompt",
                        content=content, cwd="",
                    )
                    for ts, content in raw
                ]

                try:
                    activities = classify_session(
                        self.backend, entries,
                        git_org=sess["git_org"], git_repo=sess["git_repo"],
                    )
                    for act in activities:
                        act.project = sess["project"]
                    if activities:
                        db.store_activities(activities)
                    db.mark_session_classified(sess["session_id"])
                    classified += 1
                    logger.info("Classified session %s (%d activities)", sess["session_id"][:8], len(activities))
                except Exception:
                    logger.warning("Failed to classify session %s, will retry.", sess["session_id"][:8], exc_info=True)

            return classified
        finally:
            db.close()
```

- [ ] **Step 4: Run tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_daemon.py -v`
Expected: all passed

- [ ] **Step 5: Run full suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add claude_standup/daemon.py tests/test_daemon.py
git commit -m "feat: add background daemon for continuous log processing and classification"
```

---

### Task 3: OS Service Management

**Files:**
- Create: `claude_standup/service.py`
- Create: `installer/macos/com.claude-standup.daemon.plist`
- Create: `installer/linux/claude-standup.service`
- Create: `tests/test_service.py`

- [ ] **Step 1: Write failing tests**

`tests/test_service.py`:

```python
"""Tests for claude_standup.service module."""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from claude_standup.service import (
    get_service_manager,
    LaunchdManager,
    SystemdManager,
    DaemonStatus,
)


class TestGetServiceManager:
    def test_macos(self):
        with patch("claude_standup.service.platform.system", return_value="Darwin"):
            mgr = get_service_manager()
            assert isinstance(mgr, LaunchdManager)

    def test_linux(self):
        with patch("claude_standup.service.platform.system", return_value="Linux"):
            mgr = get_service_manager()
            assert isinstance(mgr, SystemdManager)


class TestLaunchdManager:
    def test_plist_path(self):
        mgr = LaunchdManager()
        assert "LaunchAgents" in mgr.plist_path
        assert "com.claude-standup.daemon.plist" in mgr.plist_path

    def test_install_writes_plist(self, tmp_path: Path):
        mgr = LaunchdManager()
        mgr.plist_path = str(tmp_path / "test.plist")
        with patch("claude_standup.service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mgr.install("/usr/local/bin/claude-standup")
        assert Path(mgr.plist_path).exists()
        content = Path(mgr.plist_path).read_text()
        assert "claude-standup" in content
        assert "daemon" in content
        assert "run" in content

    def test_uninstall_removes_plist(self, tmp_path: Path):
        mgr = LaunchdManager()
        plist = tmp_path / "test.plist"
        plist.write_text("<plist/>")
        mgr.plist_path = str(plist)
        with patch("claude_standup.service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mgr.uninstall()
        assert not plist.exists()


class TestSystemdManager:
    def test_service_path(self):
        mgr = SystemdManager()
        assert "systemd/user" in mgr.service_path
        assert "claude-standup.service" in mgr.service_path

    def test_install_writes_service(self, tmp_path: Path):
        mgr = SystemdManager()
        mgr.service_path = str(tmp_path / "test.service")
        with patch("claude_standup.service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            mgr.install("/usr/local/bin/claude-standup")
        assert Path(mgr.service_path).exists()
        content = Path(mgr.service_path).read_text()
        assert "claude-standup" in content
        assert "daemon run" in content


class TestDaemonStatus:
    def test_status_running(self, tmp_path: Path):
        import os
        pid_path = str(tmp_path / "daemon.pid")
        Path(pid_path).write_text(str(os.getpid()))
        status = DaemonStatus.check(pid_path)
        assert status.running is True
        assert status.pid == os.getpid()

    def test_status_not_running(self, tmp_path: Path):
        status = DaemonStatus.check(str(tmp_path / "nope.pid"))
        assert status.running is False
        assert status.pid is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_service.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement service module**

`claude_standup/service.py`:

```python
"""OS service management: install/uninstall/status for launchd and systemd."""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from claude_standup.daemon import is_daemon_running, read_pid_file

DEFAULT_PID_PATH = str(Path.home() / ".claude-standup" / "daemon.pid")
DEFAULT_LOG_PATH = str(Path.home() / ".claude-standup" / "daemon.log")


def get_service_manager() -> "LaunchdManager | SystemdManager":
    if platform.system() == "Darwin":
        return LaunchdManager()
    return SystemdManager()


@dataclass
class DaemonStatus:
    running: bool
    pid: int | None

    @classmethod
    def check(cls, pid_path: str = DEFAULT_PID_PATH) -> "DaemonStatus":
        pid = read_pid_file(pid_path)
        running = is_daemon_running(pid_path)
        return cls(running=running, pid=pid if running else None)


class LaunchdManager:
    LABEL = "com.claude-standup.daemon"

    def __init__(self):
        self.plist_path = str(
            Path.home() / "Library" / "LaunchAgents" / f"{self.LABEL}.plist"
        )

    def install(self, binary_path: str) -> None:
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary_path}</string>
        <string>daemon</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{DEFAULT_LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{DEFAULT_LOG_PATH}</string>
</dict>
</plist>
"""
        Path(self.plist_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.plist_path).write_text(plist_content)
        subprocess.run(["launchctl", "load", self.plist_path], check=False)

    def uninstall(self) -> None:
        subprocess.run(["launchctl", "unload", self.plist_path], check=False)
        try:
            Path(self.plist_path).unlink()
        except FileNotFoundError:
            pass


class SystemdManager:
    SERVICE_NAME = "claude-standup"

    def __init__(self):
        self.service_path = str(
            Path.home() / ".config" / "systemd" / "user" / f"{self.SERVICE_NAME}.service"
        )

    def install(self, binary_path: str) -> None:
        service_content = f"""[Unit]
Description=claude-standup background daemon
After=default.target

[Service]
Type=simple
ExecStart={binary_path} daemon run
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
        Path(self.service_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.service_path).write_text(service_content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", self.SERVICE_NAME], check=False)

    def uninstall(self) -> None:
        subprocess.run(["systemctl", "--user", "disable", "--now", self.SERVICE_NAME], check=False)
        try:
            Path(self.service_path).unlink()
        except FileNotFoundError:
            pass
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
```

- [ ] **Step 4: Create service definition files**

`installer/macos/com.claude-standup.daemon.plist` — same content as generated by LaunchdManager.install(), but with `/usr/local/bin/claude-standup` as the binary path. This is the template for .pkg builds.

`installer/linux/claude-standup.service` — same content as generated by SystemdManager.install(), with `/usr/local/bin/claude-standup` as ExecStart.

- [ ] **Step 5: Run tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_service.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add claude_standup/service.py tests/test_service.py installer/
git commit -m "feat: add OS service management for launchd and systemd"
```

---

### Task 4: CLI Refactor

**Files:**
- Modify: `claude_standup/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write new tests for daemon subcommand, --template flag, and read-only reports**

Add to `tests/test_cli.py`:

```python
class TestDaemonSubcommand:
    def test_daemon_status(self):
        args = parse_args(["daemon", "status"])
        assert args.command == "daemon"
        assert args.daemon_action == "status"

    def test_daemon_start(self):
        args = parse_args(["daemon", "start"])
        assert args.daemon_action == "start"

    def test_daemon_stop(self):
        args = parse_args(["daemon", "stop"])
        assert args.daemon_action == "stop"

    def test_daemon_run(self):
        args = parse_args(["daemon", "run"])
        assert args.daemon_action == "run"

    def test_daemon_uninstall(self):
        args = parse_args(["daemon", "uninstall"])
        assert args.daemon_action == "uninstall"


class TestTemplateFlag:
    def test_template_flag(self):
        args = parse_args(["today", "--template"])
        assert args.template is True

    def test_template_default(self):
        args = parse_args(["today"])
        assert args.template is False


class TestStatusCommand:
    def test_status_output(self, tmp_path, capsys):
        """status command shows processing stats."""
        from claude_standup.cache import CacheDB
        db_path = str(tmp_path / "cache.db")
        db = CacheDB(db_path)
        db.store_session("s1", "app", "acme", "app", "2026-04-21T10:00:00Z", "2026-04-21T11:00:00Z")
        db.mark_session_classified("s1")
        db.store_session("s2", "app", "acme", "app", "2026-04-21T12:00:00Z", "2026-04-21T13:00:00Z")
        db.close()

        with patch("claude_standup.cli._process_new_files", return_value=0):
            main(["status"], logs_base=str(tmp_path), db_path=db_path)
        captured = capsys.readouterr()
        assert "1" in captured.out  # 1 classified
        assert "2" in captured.out  # 2 total
        assert "50%" in captured.out  # 50% classified

    def test_status_command_parses(self):
        args = parse_args(["status"])
        assert args.command == "status"


class TestReadOnlyReports:
    def test_today_no_classification(self, tmp_path, capsys):
        """Report commands should never call classify — only read from cache."""
        mock_backend = MagicMock()
        mock_backend.query.return_value = "## Report\n- Done stuff"
        with patch("claude_standup.cli.get_llm_backend", return_value=mock_backend):
            with patch("claude_standup.cli._process_new_files", return_value=0):
                main(["today"], logs_base=str(tmp_path), db_path=":memory:")
        # Backend should be called at most once (for report generation), never for classification
        assert mock_backend.query.call_count <= 1

    def test_template_no_llm_calls(self, tmp_path, capsys):
        """--template should make zero LLM calls."""
        with patch("claude_standup.cli.get_llm_backend") as mock_get:
            with patch("claude_standup.cli._process_new_files", return_value=0):
                main(["today", "--template"], logs_base=str(tmp_path), db_path=":memory:")
        mock_get.assert_not_called()
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_cli.py::TestDaemonSubcommand tests/test_cli.py::TestTemplateFlag tests/test_cli.py::TestReadOnlyReports -v`
Expected: failures

- [ ] **Step 3: Refactor cli.py**

Major changes to `claude_standup/cli.py`:

1. Add `daemon` subcommand with `start`, `stop`, `status`, `run`, `uninstall` actions
2. Add `status` subcommand showing processing stats (sessions total, classified, pending, % complete)
3. Add `--template` flag to common parser
4. Report pipeline: remove all classification logic — only read from cache
5. `--template` mode: use `generate_template_report()`, skip LLM entirely
6. Default mode: use `generate_report()` with 1 LLM call for report text

The full refactored cli.py removes `_classify_pending_sessions` entirely (moved to daemon), removes `ThreadPoolExecutor` import, and simplifies `_run_report_pipeline` to just: parse new files → query cache → generate report.

Key changes:
- `parse_args`: add `daemon` subparser with `action` positional arg, add `status` subparser, add `--template` to common parser
- New `get_processing_stats()` method on CacheDB: returns `(total_sessions, classified, pending, total_files, processed_files)`
- `_run_report_pipeline`: remove classification step, add pending count query, support `--template`
- `main`: add `daemon` command handler (start/stop/status/run/uninstall), add `status` handler, `--template` skips `get_llm_backend()`
- Remove: `_classify_pending_sessions`, `ThreadPoolExecutor` import, `classify_session` import

**`status` command output:**
```
Processing status:
  Files:    1712 discovered, 1712 parsed (100%)
  Sessions: 245 total, 230 classified, 15 pending (94%)
  Daemon:   running (PID 12345)
```

- [ ] **Step 4: Run all tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add claude_standup/cli.py tests/test_cli.py
git commit -m "refactor: CLI reads pre-classified data, adds daemon subcommand and --template flag"
```

---

### Task 5: Packaging (PyInstaller + Installer Scripts)

**Files:**
- Create: `claude-standup.spec` (PyInstaller spec)
- Create: `installer/macos/build.sh`
- Create: `installer/macos/preinstall.sh`
- Create: `installer/macos/postinstall.sh`
- Create: `installer/linux/build.sh`
- Create: `installer/linux/postinst`
- Create: `installer/linux/prerm`
- Create: `installer/linux/control`
- Create: `installer/homebrew/claude-standup.rb`

- [ ] **Step 1: Create PyInstaller spec**

`claude-standup.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['claude_standup/cli.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['claude_standup.daemon', 'claude_standup.service'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='claude-standup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
```

- [ ] **Step 2: Create macOS installer scripts**

`installer/macos/build.sh`:

```bash
#!/bin/bash
set -euo pipefail

VERSION="${1:?Usage: build.sh VERSION}"
ARCH=$(uname -m)  # arm64 or x86_64

echo "Building claude-standup $VERSION for macOS ($ARCH)..."

# Build standalone binary
pip install pyinstaller
pyinstaller claude-standup.spec --clean --noconfirm

# Build .pkg
STAGING=$(mktemp -d)
mkdir -p "$STAGING/usr/local/bin"
cp dist/claude-standup "$STAGING/usr/local/bin/"

pkgbuild \
    --root "$STAGING" \
    --identifier com.theburrowhub.claude-standup \
    --version "$VERSION" \
    --scripts installer/macos \
    "dist/claude-standup-${VERSION}-macos-${ARCH}.pkg"

echo "Built: dist/claude-standup-${VERSION}-macos-${ARCH}.pkg"
```

`installer/macos/preinstall.sh`:

```bash
#!/bin/bash
# Stop existing daemon if running
launchctl unload ~/Library/LaunchAgents/com.claude-standup.daemon.plist 2>/dev/null || true
```

`installer/macos/postinstall.sh`:

```bash
#!/bin/bash
# Install and start the daemon
/usr/local/bin/claude-standup daemon start
```

- [ ] **Step 3: Create Linux installer scripts**

`installer/linux/build.sh`:

```bash
#!/bin/bash
set -euo pipefail

VERSION="${1:?Usage: build.sh VERSION}"
ARCH="amd64"

echo "Building claude-standup $VERSION for Linux ($ARCH)..."

# Build standalone binary
pip install pyinstaller
pyinstaller claude-standup.spec --clean --noconfirm

# Build .deb
DEB_DIR=$(mktemp -d)
mkdir -p "$DEB_DIR/usr/local/bin"
mkdir -p "$DEB_DIR/usr/lib/systemd/user"
mkdir -p "$DEB_DIR/DEBIAN"

cp dist/claude-standup "$DEB_DIR/usr/local/bin/"
cp installer/linux/claude-standup.service "$DEB_DIR/usr/lib/systemd/user/"
cp installer/linux/control "$DEB_DIR/DEBIAN/"
cp installer/linux/postinst "$DEB_DIR/DEBIAN/"
cp installer/linux/prerm "$DEB_DIR/DEBIAN/"
chmod 755 "$DEB_DIR/DEBIAN/postinst" "$DEB_DIR/DEBIAN/prerm"

sed -i "s/VERSION_PLACEHOLDER/$VERSION/" "$DEB_DIR/DEBIAN/control"

dpkg-deb --build "$DEB_DIR" "dist/claude-standup_${VERSION}_${ARCH}.deb"

echo "Built: dist/claude-standup_${VERSION}_${ARCH}.deb"
```

`installer/linux/control`:

```
Package: claude-standup
Version: VERSION_PLACEHOLDER
Section: utils
Priority: optional
Architecture: amd64
Maintainer: theburrowhub
Description: Daily standup reports from Claude Code activity logs
 Background daemon that continuously classifies development sessions
 and generates instant standup reports.
```

`installer/linux/postinst`:

```bash
#!/bin/bash
# Enable and start the user service for the installing user
SUDO_USER="${SUDO_USER:-$USER}"
su - "$SUDO_USER" -c "systemctl --user daemon-reload && systemctl --user enable --now claude-standup" || true
```

`installer/linux/prerm`:

```bash
#!/bin/bash
SUDO_USER="${SUDO_USER:-$USER}"
su - "$SUDO_USER" -c "systemctl --user disable --now claude-standup" 2>/dev/null || true
```

- [ ] **Step 4: Create Homebrew formula**

`installer/homebrew/claude-standup.rb`:

```ruby
class ClaudeStandup < Formula
  desc "Daily standup reports from Claude Code activity logs"
  homepage "https://github.com/theburrowhub/claude-standup"
  url "https://github.com/theburrowhub/claude-standup/releases/download/vVERSION/claude-standup-VERSION-macos-arm64.tar.gz"
  sha256 "SHA256_PLACEHOLDER"
  license "MIT"

  def install
    bin.install "claude-standup"
  end

  service do
    run [opt_bin/"claude-standup", "daemon", "run"]
    keep_alive true
    log_path var/"log/claude-standup.log"
    error_log_path var/"log/claude-standup.log"
  end

  test do
    assert_match "usage:", shell_output("#{bin}/claude-standup --help")
  end
end
```

- [ ] **Step 5: Make build scripts executable**

```bash
chmod +x installer/macos/build.sh installer/macos/preinstall.sh installer/macos/postinstall.sh
chmod +x installer/linux/build.sh installer/linux/postinst installer/linux/prerm
```

- [ ] **Step 6: Commit**

```bash
git add claude-standup.spec installer/ 
git commit -m "feat: add packaging scripts for macOS .pkg, Linux .deb, and Homebrew"
```

---

### Task 6: GitHub Actions CI/CD

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create the release workflow**

`.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --cov=claude_standup -v

  build-macos:
    needs: test
    strategy:
      matrix:
        include:
          - runner: macos-latest
            arch: arm64
          - runner: macos-13
            arch: x86_64
    runs-on: ${{ matrix.runner }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]" pyinstaller
      - run: bash installer/macos/build.sh ${GITHUB_REF_NAME#v}
      - uses: actions/upload-artifact@v4
        with:
          name: macos-${{ matrix.arch }}
          path: dist/*.pkg

  build-linux:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]" pyinstaller
      - run: bash installer/linux/build.sh ${GITHUB_REF_NAME#v}
      - uses: actions/upload-artifact@v4
        with:
          name: linux-amd64
          path: dist/*.deb

  release:
    needs: [build-macos, build-linux]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          path: artifacts/
      - name: Create release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          VERSION=${GITHUB_REF_NAME#v}
          gh release create "$GITHUB_REF_NAME" \
            --title "claude-standup $VERSION" \
            --generate-notes \
            artifacts/**/*
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))" 2>/dev/null || python3 -c "import json; print('YAML check skipped — no pyyaml')"`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add GitHub Actions release workflow for multi-platform builds"
```

---

### Task 7: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite with coverage**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest --cov=claude_standup --cov-report=term-missing -p pytest_cov -v`
Expected: all tests pass, coverage targets met

- [ ] **Step 2: Verify CLI help**

Run: `claude-standup --help`
Run: `claude-standup daemon --help`
Run: `claude-standup today --help` (verify `--template` appears)

- [ ] **Step 3: Test warmup (local, no LLM)**

Run: `rm -f ~/.claude-standup/cache.db && time claude-standup warmup --verbose`
Expected: completes in <10s, no LLM calls

- [ ] **Step 4: Test template report (no LLM)**

Run: `claude-standup today --template --verbose`
Expected: instant output, no LLM calls

- [ ] **Step 5: Test daemon start/status/stop**

Run: `claude-standup daemon start && sleep 2 && claude-standup daemon status && claude-standup daemon stop`
Expected: daemon starts, status shows running, daemon stops

- [ ] **Step 6: Tag release**

```bash
git tag v2.0.0
```
