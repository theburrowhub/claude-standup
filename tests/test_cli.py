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
