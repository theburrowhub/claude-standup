"""Tests for the parser module."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from claude_standup.parser import (
    discover_jsonl_files,
    parse_jsonl_file,
    derive_project_name,
    parse_remote_url,
    resolve_git_remote,
)
from claude_standup.models import GitInfo

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# TestDiscoverJsonlFiles
# ---------------------------------------------------------------------------


class TestDiscoverJsonlFiles:
    """Tests for discover_jsonl_files."""

    def test_finds_jsonl_files(self, tmp_path: Path) -> None:
        """Recursively finds .jsonl files and ignores .txt files."""
        # Create nested structure
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.jsonl").write_text("{}")
        (sub / "b.jsonl").write_text("{}")
        (tmp_path / "c.txt").write_text("not a jsonl")

        result = discover_jsonl_files(tmp_path)
        paths = {fi.path for fi in result}

        assert len(result) == 2
        assert str(tmp_path / "a.jsonl") in paths
        assert str(sub / "b.jsonl") in paths

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Returns empty list for directory with no JSONL files."""
        result = discover_jsonl_files(tmp_path)
        assert result == []

    def test_returns_mtime(self, tmp_path: Path) -> None:
        """Each FileInfo includes a valid mtime float."""
        (tmp_path / "x.jsonl").write_text("{}")
        result = discover_jsonl_files(tmp_path)

        assert len(result) == 1
        assert isinstance(result[0].mtime, float)
        assert result[0].mtime > 0

    def test_nonexistent_directory(self) -> None:
        """Returns empty list for a path that does not exist."""
        result = discover_jsonl_files("/nonexistent/path/abc123")
        assert result == []


# ---------------------------------------------------------------------------
# TestParseJsonlFile
# ---------------------------------------------------------------------------


class TestParseJsonlFile:
    """Tests for parse_jsonl_file."""

    def test_parse_valid_session(self) -> None:
        """Parses valid_session.jsonl: 2 user prompts, assistant texts, tool uses."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "my-app")

        user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
        assistant_texts = [e for e in entries if e.entry_type == "assistant_text"]
        tool_uses = [e for e in entries if e.entry_type == "tool_use"]

        assert len(user_prompts) == 2
        assert user_prompts[0].content == "Implement the login feature with OAuth2"
        assert user_prompts[1].content == "Now add unit tests for the login"

        assert len(assistant_texts) >= 2
        assert len(tool_uses) >= 1

    def test_skip_queue_operations(self) -> None:
        """Queue-operation entries are skipped."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "test")
        types = {e.entry_type for e in entries}
        # No entry should be derived from queue-operation lines
        contents = [e.content for e in entries]
        assert not any("enqueue" in c for c in contents if c)

    def test_skip_attachments(self) -> None:
        """Attachment entries are skipped."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "test")
        # Attachment line should not produce any entry
        for e in entries:
            assert "deferred_tools_delta" not in e.content

    def test_skip_tool_results(self) -> None:
        """User entries with toolUseResult=true are skipped."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "test")
        user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
        # None of the user prompts should be tool results
        for prompt in user_prompts:
            assert "tool_result" not in prompt.content.lower()
            assert "File edited" not in prompt.content

    def test_skip_thinking_blocks(self) -> None:
        """Thinking blocks in assistant content are not included in text output."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "test")
        assistant_texts = [e for e in entries if e.entry_type == "assistant_text"]
        for entry in assistant_texts:
            assert "I need to implement OAuth2 login" not in entry.content

    def test_extract_tool_use_names(self) -> None:
        """Tool use names (Edit, Write) are extracted correctly."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "test")
        tool_uses = [e for e in entries if e.entry_type == "tool_use"]

        all_tool_names = []
        for tu in tool_uses:
            all_tool_names.extend(tu.tool_names)

        assert "Edit" in all_tool_names
        assert "Write" in all_tool_names

    def test_extract_metadata(self) -> None:
        """Session ID, cwd, and git_branch are extracted from entries."""
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "test")

        first = entries[0]
        assert first.session_id == "sess-001"
        assert first.cwd == "/Users/dev/projects/my-app"
        assert first.git_branch == "feat/login"

    def test_malformed_lines(self) -> None:
        """Malformed JSON lines are skipped; 2 valid user prompts from 5 lines."""
        entries = parse_jsonl_file(
            str(FIXTURES_DIR / "malformed_lines.jsonl"), "api"
        )
        user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
        assert len(user_prompts) == 2
        assert user_prompts[0].content == "Fix the bug"
        assert user_prompts[1].content == "Great, commit it"

    def test_empty_file(self) -> None:
        """Empty JSONL file returns empty list."""
        entries = parse_jsonl_file(
            str(FIXTURES_DIR / "empty_session.jsonl"), "empty"
        )
        assert entries == []

    def test_multi_session(self) -> None:
        """Multi-session file contains entries from sess-010 and sess-011."""
        entries = parse_jsonl_file(
            str(FIXTURES_DIR / "multi_session.jsonl"), "multi"
        )
        session_ids = {e.session_id for e in entries}
        assert "sess-010" in session_ids
        assert "sess-011" in session_ids


# ---------------------------------------------------------------------------
# TestDeriveProjectName
# ---------------------------------------------------------------------------


class TestDeriveProjectName:
    """Tests for derive_project_name."""

    def test_standard_path(self) -> None:
        """Extracts 'ai-gateway' from a standard Claude dir name."""
        result = derive_project_name(
            "-Users-jamuriano-personal-workspace-ai-gateway"
        )
        assert result == "ai-gateway"

    def test_nested_path(self) -> None:
        """Extracts 'overmind' from a nested workspace dir name."""
        result = derive_project_name(
            "-Users-jamuriano-personal-workspace-overmind"
        )
        assert result == "overmind"

    def test_deep_nested(self) -> None:
        """Extracts 'overmind-admin' from deep nested path."""
        result = derive_project_name(
            "-Users-jamuriano-personal-workspace-overmind-admin"
        )
        assert result == "overmind-admin"

    def test_simple(self) -> None:
        """Extracts 'my-app' from a simple workspace path."""
        result = derive_project_name(
            "-Users-dev-workspace-my-app"
        )
        assert result == "my-app"

    def test_single_segment(self) -> None:
        """Returns empty string for a single-segment name with no workspace."""
        result = derive_project_name("foo")
        assert result == ""


# ---------------------------------------------------------------------------
# TestParseRemoteUrl
# ---------------------------------------------------------------------------


class TestParseRemoteUrl:
    """Tests for parse_remote_url."""

    def test_ssh_url(self) -> None:
        """Parses SSH git remote URL."""
        info = parse_remote_url("git@github.com:acme-corp/my-app.git")
        assert info.org == "acme-corp"
        assert info.repo == "my-app"

    def test_https_url(self) -> None:
        """Parses HTTPS git remote URL."""
        info = parse_remote_url("https://github.com/acme-corp/my-app.git")
        assert info.org == "acme-corp"
        assert info.repo == "my-app"

    def test_https_url_no_dot_git(self) -> None:
        """Parses HTTPS URL without .git suffix."""
        info = parse_remote_url("https://github.com/acme-corp/my-app")
        assert info.org == "acme-corp"
        assert info.repo == "my-app"

    def test_invalid_url(self) -> None:
        """Returns empty GitInfo for invalid URL."""
        info = parse_remote_url("not-a-url")
        assert info.org is None
        assert info.repo is None

    def test_empty_string(self) -> None:
        """Returns empty GitInfo for empty string."""
        info = parse_remote_url("")
        assert info.org is None
        assert info.repo is None


# ---------------------------------------------------------------------------
# TestResolveGitRemote
# ---------------------------------------------------------------------------


class TestResolveGitRemote:
    """Tests for resolve_git_remote."""

    @patch("claude_standup.parser.subprocess.run")
    def test_resolves_remote(self, mock_run: MagicMock) -> None:
        """Resolves remote URL via subprocess and parses it."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="git@github.com:acme-corp/backend.git\n",
        )
        cache: dict[str, GitInfo] = {}
        info = resolve_git_remote("/some/path", cache)

        assert info.org == "acme-corp"
        assert info.repo == "backend"
        mock_run.assert_called_once_with(
            ["git", "-C", "/some/path", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )

    @patch("claude_standup.parser.subprocess.run")
    def test_no_remote(self, mock_run: MagicMock) -> None:
        """Returns empty GitInfo when git command fails."""
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        cache: dict[str, GitInfo] = {}
        info = resolve_git_remote("/no/git/repo", cache)

        assert info.org is None
        assert info.repo is None

    @patch("claude_standup.parser.subprocess.run")
    def test_cached_result(self, mock_run: MagicMock) -> None:
        """Returns cached result without calling subprocess again."""
        cached_info = GitInfo(org="cached-org", repo="cached-repo")
        cache: dict[str, GitInfo] = {"/cached/path": cached_info}

        info = resolve_git_remote("/cached/path", cache)

        assert info.org == "cached-org"
        assert info.repo == "cached-repo"
        mock_run.assert_not_called()

    @patch("claude_standup.parser.subprocess.run")
    def test_nonexistent_directory(self, mock_run: MagicMock) -> None:
        """Returns empty GitInfo when directory does not exist."""
        mock_run.side_effect = FileNotFoundError("git not found")
        cache: dict[str, GitInfo] = {}
        info = resolve_git_remote("/nonexistent", cache)

        assert info.org is None
        assert info.repo is None
        # Result should still be cached
        assert "/nonexistent" in cache
