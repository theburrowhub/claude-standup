"""Tests for claude_standup.llm module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from claude_standup.llm import (
    AnthropicSDKBackend,
    ClaudeCLIBackend,
    get_llm_backend,
)


class TestGetLlmBackend:
    def test_prefers_env_var(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            backend = get_llm_backend()
            assert isinstance(backend, AnthropicSDKBackend)

    def test_falls_back_to_claude_cli(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("claude_standup.llm.shutil.which", return_value="/usr/bin/claude"):
                backend = get_llm_backend()
                assert isinstance(backend, ClaudeCLIBackend)

    def test_raises_when_nothing_available(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("claude_standup.llm.shutil.which", return_value=None):
                with pytest.raises(RuntimeError, match="No LLM backend"):
                    get_llm_backend()


class TestClaudeCLIBackend:
    def test_query_success(self):
        backend = ClaudeCLIBackend("/usr/bin/claude")
        with patch("claude_standup.llm.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"activities": []}', stderr="")
            result = backend.query("system", "user prompt")
            assert result == '{"activities": []}'
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "/usr/bin/claude"
            assert "-p" in call_args

    def test_query_failure(self):
        backend = ClaudeCLIBackend("/usr/bin/claude")
        with patch("claude_standup.llm.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            with pytest.raises(RuntimeError, match="claude CLI failed"):
                backend.query("system", "user prompt")


class TestAnthropicSDKBackend:
    def test_query_success(self):
        with patch("claude_standup.llm.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_message = MagicMock()
            mock_message.content = [MagicMock(type="text", text="response text")]
            mock_client.messages.create.return_value = mock_message
            mock_anthropic.Anthropic.return_value = mock_client

            backend = AnthropicSDKBackend("sk-test")
            result = backend.query("system", "user prompt")
            assert result == "response text"
