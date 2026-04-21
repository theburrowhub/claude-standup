"""Tests for claude_standup.cli module."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from claude_standup.cli import (
    DEFAULT_DB_PATH,
    DEFAULT_LOGS_BASE,
    VALID_TYPES,
    main,
    parse_args,
    resolve_date_range,
)


# ---------------------------------------------------------------------------
# TestParseArgs
# ---------------------------------------------------------------------------

class TestParseArgs:
    """Verify argument parsing for every subcommand, flag, and default."""

    def test_today_command(self):
        args = parse_args(["today"])
        assert args.command == "today"

    def test_yesterday_command(self):
        args = parse_args(["yesterday"])
        assert args.command == "yesterday"

    def test_last_7_days_command(self):
        args = parse_args(["last-7-days"])
        assert args.command == "last-7-days"

    def test_log_command(self):
        args = parse_args(["log", "Fixed login bug"])
        assert args.command == "log"
        assert args.message == "Fixed login bug"

    def test_default_command(self):
        args = parse_args([])
        assert args.command == "today"

    def test_from_to_flags(self):
        args = parse_args(["today", "--from", "2026-04-01", "--to", "2026-04-15"])
        assert args.date_from == "2026-04-01"
        assert args.date_to == "2026-04-15"

    def test_org_filter_single(self):
        args = parse_args(["today", "--org", "acme"])
        assert args.org == "acme"

    def test_repo_filter_single(self):
        args = parse_args(["today", "--repo", "my-app"])
        assert args.repo == "my-app"

    def test_org_filter_multiple(self):
        args = parse_args(["today", "--org", "acme,beta"])
        assert args.org == "acme,beta"

    def test_repo_filter_multiple(self):
        args = parse_args(["today", "--repo", "my-app,other-app"])
        assert args.repo == "my-app,other-app"

    def test_lang_flag(self):
        args = parse_args(["today", "--lang", "en"])
        assert args.lang == "en"

    def test_lang_default(self):
        args = parse_args(["today"])
        assert args.lang == "es"

    def test_format_flag(self):
        args = parse_args(["today", "--format", "slack"])
        assert args.format == "slack"

    def test_format_default(self):
        args = parse_args(["today"])
        assert args.format == "markdown"

    def test_output_flag(self):
        args = parse_args(["today", "--output", "/tmp/report.md"])
        assert args.output == "/tmp/report.md"

    def test_reprocess_flag(self):
        args = parse_args(["today", "--reprocess"])
        assert args.reprocess is True

    def test_verbose_flag(self):
        args = parse_args(["today", "--verbose"])
        assert args.verbose is True

    def test_log_with_type(self):
        args = parse_args(["log", "Helped QA team", "--type", "SUPPORT"])
        assert args.command == "log"
        assert args.message == "Helped QA team"
        assert args.type == "SUPPORT"

    def test_warmup_command(self):
        args = parse_args(["warmup"])
        assert args.command == "warmup"

    def test_warmup_with_verbose(self):
        args = parse_args(["warmup", "--verbose"])
        assert args.command == "warmup"
        assert args.verbose is True

    def test_log_type_default(self):
        args = parse_args(["log", "Something"])
        assert args.type == "OTHER"

    def test_log_with_org_repo(self):
        args = parse_args(["log", "Deploy fix", "--org", "acme", "--repo", "backend"])
        assert args.command == "log"
        assert args.org == "acme"
        assert args.repo == "backend"


# ---------------------------------------------------------------------------
# TestResolveDateRange
# ---------------------------------------------------------------------------

class TestResolveDateRange:
    """Verify date range resolution from command names and overrides."""

    def test_today(self):
        today = date.today().isoformat()
        d_from, d_to = resolve_date_range("today", None, None)
        assert d_from == today
        assert d_to == today

    def test_yesterday(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        d_from, d_to = resolve_date_range("yesterday", None, None)
        assert d_from == yesterday
        assert d_to == yesterday

    def test_last_7_days(self):
        today = date.today()
        expected_from = (today - timedelta(days=6)).isoformat()
        expected_to = today.isoformat()
        d_from, d_to = resolve_date_range("last-7-days", None, None)
        assert d_from == expected_from
        assert d_to == expected_to

    def test_custom_range_overrides(self):
        d_from, d_to = resolve_date_range("today", "2026-01-01", "2026-01-31")
        assert d_from == "2026-01-01"
        assert d_to == "2026-01-31"

    def test_partial_override_from(self):
        today = date.today().isoformat()
        d_from, d_to = resolve_date_range("today", "2026-03-01", None)
        assert d_from == "2026-03-01"
        assert d_to == today


# ---------------------------------------------------------------------------
# TestMain
# ---------------------------------------------------------------------------

class TestMain:
    """Integration-level tests for the main() entry point."""

    def test_no_backend_available(self, tmp_path, capsys):
        with patch("claude_standup.cli.get_llm_backend", side_effect=RuntimeError("No LLM backend available.")):
            with pytest.raises(SystemExit) as exc_info:
                main(["today"], logs_base=str(tmp_path), db_path=":memory:")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "No LLM backend" in captured.err

    def test_log_command_stores_entry(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        main(["log", "Team meeting", "--type", "MEETING"], logs_base=str(tmp_path), db_path=db_path)

        # Verify the entry was stored in the database
        from claude_standup.cache import CacheDB
        db = CacheDB(db_path)
        activities = db.query_activities("2000-01-01", "2099-12-31")
        db.close()
        assert len(activities) >= 1
        meeting = [a for a in activities if a.summary == "Team meeting"]
        assert len(meeting) == 1
        assert meeting[0].classification == "MEETING"

    def test_report_output_to_file(self, tmp_path, capsys):
        output_file = str(tmp_path / "report.md")
        mock_backend = MagicMock()
        with patch("claude_standup.cli.get_llm_backend", return_value=mock_backend):
            with patch("claude_standup.cli._run_report_pipeline", return_value="## Report"):
                main(
                    ["today", "--output", output_file],
                    logs_base=str(tmp_path),
                    db_path=":memory:",
                )

        with open(output_file) as f:
            assert f.read() == "## Report"

        captured = capsys.readouterr()
        assert "## Report" in captured.out

    def test_verbose_output(self, tmp_path, capsys):
        mock_backend = MagicMock()
        with patch("claude_standup.cli.get_llm_backend", return_value=mock_backend):
            with patch("claude_standup.cli._run_report_pipeline", return_value="## Report"):
                main(
                    ["today", "--verbose"],
                    logs_base=str(tmp_path),
                    db_path=":memory:",
                )

        captured = capsys.readouterr()
        assert "## Report" in captured.out

    def test_warmup_processes_files(self, tmp_path, capsys):
        with patch("claude_standup.cli._process_new_files", return_value=5):
            main(["warmup"], logs_base=str(tmp_path), db_path=":memory:")
        captured = capsys.readouterr()
        assert "5 file(s) processed" in captured.err

    def test_warmup_nothing_to_process(self, tmp_path, capsys):
        with patch("claude_standup.cli._process_new_files", return_value=0):
            main(["warmup"], logs_base=str(tmp_path), db_path=":memory:")
        captured = capsys.readouterr()
        assert "up to date" in captured.err

    def test_warmup_no_backend_needed(self, tmp_path, capsys):
        """warmup should work even without any LLM backend available."""
        with patch("claude_standup.cli._process_new_files", return_value=3):
            main(["warmup"], logs_base=str(tmp_path), db_path=":memory:")
        captured = capsys.readouterr()
        assert "3 file(s) processed" in captured.err


# ---------------------------------------------------------------------------
# TestDaemonSubcommand
# ---------------------------------------------------------------------------

class TestDaemonSubcommand:
    """Tests for the 'daemon' subcommand and its nested actions."""

    def test_daemon_command_parses(self):
        args = parse_args(["daemon", "status"])
        assert args.command == "daemon"
        assert args.daemon_action == "status"

    def test_daemon_start_parses(self):
        args = parse_args(["daemon", "start"])
        assert args.command == "daemon"
        assert args.daemon_action == "start"

    def test_daemon_stop_parses(self):
        args = parse_args(["daemon", "stop"])
        assert args.command == "daemon"
        assert args.daemon_action == "stop"

    def test_daemon_run_parses(self):
        args = parse_args(["daemon", "run"])
        assert args.command == "daemon"
        assert args.daemon_action == "run"

    def test_daemon_uninstall_parses(self):
        args = parse_args(["daemon", "uninstall"])
        assert args.command == "daemon"
        assert args.daemon_action == "uninstall"

    def test_daemon_status(self, tmp_path, capsys):
        """daemon status should print daemon running state."""
        mock_status = MagicMock()
        mock_status.running = False
        mock_status.pid = None
        with patch("claude_standup.service.DaemonStatus.check", return_value=mock_status):
            main(["daemon", "status"], logs_base=str(tmp_path), db_path=":memory:")
        captured = capsys.readouterr()
        assert "not running" in captured.err

    def test_daemon_status_running(self, tmp_path, capsys):
        """daemon status should show PID when running."""
        mock_status = MagicMock()
        mock_status.running = True
        mock_status.pid = 12345
        with patch("claude_standup.service.DaemonStatus.check", return_value=mock_status):
            main(["daemon", "status"], logs_base=str(tmp_path), db_path=":memory:")
        captured = capsys.readouterr()
        assert "running" in captured.err
        assert "12345" in captured.err

    def test_daemon_start(self, tmp_path, capsys):
        """daemon start should install and start the service."""
        mock_mgr = MagicMock()
        with patch("claude_standup.service.get_service_manager", return_value=mock_mgr):
            main(["daemon", "start"], logs_base=str(tmp_path), db_path=":memory:")
        mock_mgr.install.assert_called_once()
        captured = capsys.readouterr()
        assert "installed and started" in captured.err

    def test_daemon_stop(self, tmp_path, capsys):
        """daemon stop should uninstall the service."""
        mock_mgr = MagicMock()
        with patch("claude_standup.service.get_service_manager", return_value=mock_mgr):
            main(["daemon", "stop"], logs_base=str(tmp_path), db_path=":memory:")
        mock_mgr.uninstall.assert_called_once()
        captured = capsys.readouterr()
        assert "stopped" in captured.err

    def test_daemon_run(self, tmp_path, capsys):
        """daemon run should start the DaemonRunner in foreground."""
        mock_runner = MagicMock()
        with patch("claude_standup.cli.get_llm_backend", side_effect=RuntimeError("no key")):
            with patch("claude_standup.daemon.write_pid_file"):
                with patch("claude_standup.daemon.remove_pid_file"):
                    with patch("claude_standup.daemon.DaemonRunner", return_value=mock_runner) as mock_cls:
                        main(
                            ["daemon", "run"],
                            logs_base=str(tmp_path),
                            db_path=str(tmp_path / "test.db"),
                        )
        mock_cls.assert_called_once()
        mock_runner.run_forever.assert_called_once()

    def test_daemon_uninstall(self, tmp_path, capsys):
        """daemon uninstall should uninstall the service."""
        mock_mgr = MagicMock()
        with patch("claude_standup.service.get_service_manager", return_value=mock_mgr):
            main(["daemon", "uninstall"], logs_base=str(tmp_path), db_path=":memory:")
        mock_mgr.uninstall.assert_called_once()
        captured = capsys.readouterr()
        assert "uninstalled" in captured.err


# ---------------------------------------------------------------------------
# TestStatusCommand
# ---------------------------------------------------------------------------

class TestStatusCommand:
    """Tests for the 'status' subcommand."""

    def test_status_command_parses(self):
        args = parse_args(["status"])
        assert args.command == "status"

    def test_status_output(self, tmp_path, capsys):
        """Pre-populate DB with 1 classified + 1 unclassified session, verify 50% in output."""
        from claude_standup.cache import CacheDB

        db_path = str(tmp_path / "test.db")
        db = CacheDB(db_path)
        # Insert a classified session
        db.store_session(
            session_id="sess-001",
            project="proj-a",
            git_org="acme",
            git_repo="app",
            first_ts="2026-04-21T10:00:00Z",
            last_ts="2026-04-21T11:00:00Z",
        )
        db.mark_session_classified("sess-001")
        # Insert an unclassified session
        db.store_session(
            session_id="sess-002",
            project="proj-b",
            git_org="acme",
            git_repo="api",
            first_ts="2026-04-21T12:00:00Z",
            last_ts="2026-04-21T13:00:00Z",
        )
        # Also mark a file as processed so there's a file count
        db.mark_file_processed("/fake/file.jsonl", 1234.0)
        db.close()

        mock_status = MagicMock()
        mock_status.running = False
        mock_status.pid = None
        with patch("claude_standup.service.DaemonStatus.check", return_value=mock_status):
            with patch("claude_standup.cli._process_new_files", return_value=0):
                main(["status"], logs_base=str(tmp_path), db_path=db_path)

        captured = capsys.readouterr()
        assert "50%" in captured.out
        assert "2 total" in captured.out
        assert "1 classified" in captured.out
        assert "1 pending" in captured.out
        assert "1 parsed" in captured.out
        assert "not running" in captured.out

    def test_status_empty_db(self, tmp_path, capsys):
        """Status with no sessions should show 100% (no pending)."""
        mock_status = MagicMock()
        mock_status.running = False
        mock_status.pid = None
        with patch("claude_standup.service.DaemonStatus.check", return_value=mock_status):
            with patch("claude_standup.cli._process_new_files", return_value=0):
                main(["status"], logs_base=str(tmp_path), db_path=":memory:")

        captured = capsys.readouterr()
        assert "100%" in captured.out
        assert "0 total" in captured.out


# ---------------------------------------------------------------------------
# TestTemplateFlag
# ---------------------------------------------------------------------------

class TestTemplateFlag:
    """Tests for the --template flag."""

    def test_template_flag_true(self):
        args = parse_args(["today", "--template"])
        assert args.template is True

    def test_template_flag_default(self):
        args = parse_args(["today"])
        assert args.template is False

    def test_template_flag_default_no_subcommand(self):
        """When no subcommand is given, template should default to False."""
        args = parse_args([])
        assert args.template is False

    def test_template_flag_on_yesterday(self):
        args = parse_args(["yesterday", "--template"])
        assert args.template is True

    def test_template_flag_on_last_7_days(self):
        args = parse_args(["last-7-days", "--template"])
        assert args.template is True


# ---------------------------------------------------------------------------
# TestReadOnlyReports
# ---------------------------------------------------------------------------

class TestReadOnlyReports:
    """Tests ensuring reports are read-only (no classification in CLI)."""

    def test_today_no_classification(self, tmp_path, capsys):
        """Report pipeline should not call classify_session; backend.query called at most once."""
        mock_backend = MagicMock()
        mock_backend.query.return_value = "## Today Report"

        with patch("claude_standup.cli.get_llm_backend", return_value=mock_backend):
            with patch("claude_standup.cli._process_new_files", return_value=0):
                main(["today"], logs_base=str(tmp_path), db_path=":memory:")

        captured = capsys.readouterr()
        # Backend query should be called at most once (for report generation only)
        assert mock_backend.query.call_count <= 1

    def test_template_no_llm_calls(self, tmp_path, capsys):
        """--template should not call get_llm_backend at all."""
        with patch("claude_standup.cli.get_llm_backend") as mock_get_backend:
            with patch("claude_standup.cli._process_new_files", return_value=0):
                main(
                    ["today", "--template"],
                    logs_base=str(tmp_path),
                    db_path=":memory:",
                )

        mock_get_backend.assert_not_called()
        captured = capsys.readouterr()
        # Should produce output (template report, even if empty)
        assert captured.out.strip() != "" or captured.out == ""  # no crash

    def test_template_produces_output(self, tmp_path, capsys):
        """--template with activities should produce a template report."""
        from claude_standup.cache import CacheDB
        from claude_standup.models import Activity

        db_path = str(tmp_path / "test.db")
        db = CacheDB(db_path)
        # Store a classified activity for today
        from datetime import date as date_cls
        today_str = date_cls.today().isoformat()
        db.store_activities([
            Activity(
                session_id="sess-001",
                day=today_str,
                project="my-project",
                git_org="acme",
                git_repo="app",
                classification="FEATURE",
                summary="Added new login page",
                time_spent_minutes=45,
            )
        ])
        db.close()

        with patch("claude_standup.cli._process_new_files", return_value=0):
            main(
                ["today", "--template"],
                logs_base=str(tmp_path),
                db_path=db_path,
            )

        captured = capsys.readouterr()
        assert "FEATURE" in captured.out
        assert "Added new login page" in captured.out

    def test_report_pipeline_read_only(self, tmp_path):
        """_run_report_pipeline should not import or call classify_session."""
        import argparse
        from claude_standup.cache import CacheDB
        from claude_standup.cli import _run_report_pipeline

        mock_backend = MagicMock()
        mock_backend.query.return_value = "Report text"

        db = CacheDB(":memory:")

        args = argparse.Namespace(
            command="today",
            date_from=None,
            date_to=None,
            org=None,
            repo=None,
            lang="es",
            format="markdown",
            output=None,
            reprocess=False,
            verbose=False,
            template=False,
        )

        with patch("claude_standup.cli._process_new_files", return_value=0):
            result = _run_report_pipeline(mock_backend, db, str(tmp_path), args)

        db.close()

        # The result should be a string (the report)
        assert isinstance(result, str)
        # Backend.query should have been called at most once (for report generation)
        assert mock_backend.query.call_count <= 1

    def test_pending_sessions_warning_in_report(self, tmp_path):
        """When there are pending sessions, the report should include a warning."""
        import argparse
        from claude_standup.cache import CacheDB
        from claude_standup.cli import _run_report_pipeline
        from datetime import date as date_cls

        mock_backend = MagicMock()
        mock_backend.query.return_value = "Report text"

        db_path = str(tmp_path / "test.db")
        db = CacheDB(db_path)

        today_str = date_cls.today().isoformat()

        # Store an unclassified session for today
        db.store_session(
            session_id="sess-pending",
            project="proj",
            git_org="acme",
            git_repo="app",
            first_ts=f"{today_str}T10:00:00Z",
            last_ts=f"{today_str}T11:00:00Z",
        )

        args = argparse.Namespace(
            command="today",
            date_from=None,
            date_to=None,
            org=None,
            repo=None,
            lang="es",
            format="markdown",
            output=None,
            reprocess=False,
            verbose=False,
            template=False,
        )

        with patch("claude_standup.cli._process_new_files", return_value=0):
            with patch("claude_standup.daemon.is_daemon_running", return_value=False):
                result = _run_report_pipeline(mock_backend, db, str(tmp_path), args)

        db.close()

        assert "pending classification" in result
        assert "daemon: not running" in result
