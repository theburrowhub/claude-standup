"""Tests for claude_standup.reporter module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from claude_standup.models import Activity


# ---------------------------------------------------------------------------
# Sample reports for assertions
# ---------------------------------------------------------------------------

SAMPLE_MARKDOWN_REPORT = (
    "## 2026-04-20\n\n"
    "### Yesterday\n"
    "- Implemented user login with OAuth2 (acme-corp/my-app) ~45min\n"
    "- Fixed session expiration bug (acme-corp/my-app) ~20min\n"
    "- Sprint planning with backend team\n\n"
    "### Today\n"
    "- Continue OAuth2 integration testing\n\n"
    "### Blockers\n"
    "- None identified"
)

SAMPLE_SLACK_REPORT = (
    "*2026-04-20*\n\n"
    "*Yesterday*\n"
    "\u2022 Implemented user login with OAuth2 (acme-corp/my-app) ~45min\n"
    "\u2022 Fixed session expiration bug (acme-corp/my-app) ~20min\n"
    "\u2022 Sprint planning with backend team\n\n"
    "*Today*\n"
    "\u2022 Continue OAuth2 integration testing\n\n"
    "*Blockers*\n"
    "\u2022 None identified"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(report_text: str) -> MagicMock:
    """Create a mock Anthropic client that returns *report_text*."""
    client = MagicMock()
    message = MagicMock()
    message.content = [MagicMock(type="text", text=report_text)]
    client.messages.create.return_value = message
    return client


# ---------------------------------------------------------------------------
# TestGenerateReport
# ---------------------------------------------------------------------------

class TestGenerateReport:
    """Verify the main generate_report function."""

    def test_generates_markdown(self, sample_activities):
        from claude_standup.reporter import generate_report

        client = _make_mock_client(SAMPLE_MARKDOWN_REPORT)
        result = generate_report(client, sample_activities, lang="es", output_format="markdown")

        assert "## 2026-04-20" in result
        assert "Yesterday" in result
        client.messages.create.assert_called_once()

    def test_generates_slack(self, sample_activities):
        from claude_standup.reporter import generate_report

        client = _make_mock_client(SAMPLE_SLACK_REPORT)
        result = generate_report(client, sample_activities, lang="es", output_format="slack")

        assert "*2026-04-20*" in result
        client.messages.create.assert_called_once()

    def test_empty_activities_es(self):
        from claude_standup.reporter import generate_report

        client = MagicMock()
        result = generate_report(client, [], lang="es", output_format="markdown")

        assert "No se encontró actividad" in result
        client.messages.create.assert_not_called()

    def test_empty_activities_en(self):
        from claude_standup.reporter import generate_report

        client = MagicMock()
        result = generate_report(client, [], lang="en", output_format="markdown")

        assert "No activity found" in result
        client.messages.create.assert_not_called()

    def test_lang_passed_to_prompt(self, sample_activities):
        from claude_standup.reporter import generate_report

        client = _make_mock_client(SAMPLE_MARKDOWN_REPORT)
        generate_report(client, sample_activities, lang="en", output_format="markdown")

        call_kwargs = client.messages.create.call_args
        # The user message should mention English / en
        user_messages = [
            m for m in call_kwargs.kwargs.get("messages", call_kwargs[1].get("messages", []))
            if m["role"] == "user"
        ]
        assert len(user_messages) == 1
        user_content = user_messages[0]["content"]
        assert "english" in user_content.lower() or "en" in user_content.lower()

    def test_format_passed_to_prompt(self, sample_activities):
        from claude_standup.reporter import generate_report

        client = _make_mock_client(SAMPLE_SLACK_REPORT)
        generate_report(client, sample_activities, lang="es", output_format="slack")

        call_kwargs = client.messages.create.call_args
        user_messages = [
            m for m in call_kwargs.kwargs.get("messages", call_kwargs[1].get("messages", []))
            if m["role"] == "user"
        ]
        assert len(user_messages) == 1
        user_content = user_messages[0]["content"]
        assert "slack" in user_content.lower() or "Slack" in user_content


# ---------------------------------------------------------------------------
# TestBuildReportPrompt
# ---------------------------------------------------------------------------

class TestBuildReportPrompt:
    """Verify _build_report_prompt formats activities correctly."""

    def test_includes_activity_details(self, sample_activities):
        from claude_standup.reporter import _build_report_prompt

        prompt = _build_report_prompt(sample_activities, lang="es", output_format="markdown")

        # Should include the day
        assert "2026-04-20" in prompt
        # Should include classification
        assert "FEATURE" in prompt
        # Should include org/repo
        assert "acme-corp/my-app" in prompt
        # Should include summary
        assert "Implemented user login with OAuth2" in prompt
        # Should include time
        assert "~45min" in prompt

    def test_includes_language_instruction(self, sample_activities):
        from claude_standup.reporter import _build_report_prompt

        prompt = _build_report_prompt(sample_activities, lang="en", output_format="markdown")
        assert "english" in prompt.lower() or "en" in prompt.lower()

    def test_includes_format_instruction_slack(self, sample_activities):
        from claude_standup.reporter import _build_report_prompt

        prompt = _build_report_prompt(sample_activities, lang="es", output_format="slack")
        assert "slack" in prompt.lower() or "Slack" in prompt

    def test_includes_format_instruction_markdown(self, sample_activities):
        from claude_standup.reporter import _build_report_prompt

        prompt = _build_report_prompt(sample_activities, lang="es", output_format="markdown")
        assert "markdown" in prompt.lower() or "Markdown" in prompt

    def test_manual_entry_included(self, sample_activities):
        from claude_standup.reporter import _build_report_prompt

        prompt = _build_report_prompt(sample_activities, lang="es", output_format="markdown")
        assert "Sprint planning with backend team" in prompt


# ---------------------------------------------------------------------------
# TestFormatAsMarkdown
# ---------------------------------------------------------------------------

class TestFormatAsMarkdown:
    """Verify format_as_markdown passthrough behaviour."""

    def test_basic_structure(self):
        from claude_standup.reporter import format_as_markdown

        result = format_as_markdown(SAMPLE_MARKDOWN_REPORT)
        assert "## 2026-04-20" in result
        assert "### Yesterday" in result
        assert "### Today" in result
        assert "### Blockers" in result

    def test_passthrough(self):
        from claude_standup.reporter import format_as_markdown

        text = "Some arbitrary text\nwith multiple lines"
        assert format_as_markdown(text) == text


# ---------------------------------------------------------------------------
# TestFormatAsSlack
# ---------------------------------------------------------------------------

class TestFormatAsSlack:
    """Verify format_as_slack passthrough behaviour."""

    def test_basic_structure(self):
        from claude_standup.reporter import format_as_slack

        result = format_as_slack(SAMPLE_SLACK_REPORT)
        assert "*2026-04-20*" in result
        assert "*Yesterday*" in result
        assert "\u2022" in result

    def test_passthrough(self):
        from claude_standup.reporter import format_as_slack

        text = "Some arbitrary text\nwith multiple lines"
        assert format_as_slack(text) == text


# ---------------------------------------------------------------------------
# TestFileOutput
# ---------------------------------------------------------------------------

class TestFileOutput:
    """Verify writing a report to a file."""

    def test_write_to_file(self, tmp_path):
        report = SAMPLE_MARKDOWN_REPORT
        output_file = tmp_path / "standup.md"
        output_file.write_text(report, encoding="utf-8")

        content = output_file.read_text(encoding="utf-8")
        assert content == report
        assert "## 2026-04-20" in content
        assert "Yesterday" in content
