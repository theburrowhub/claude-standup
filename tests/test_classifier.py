"""Tests for claude_standup.classifier module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from claude_standup.models import Activity, LogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(response_json: dict) -> MagicMock:
    client = MagicMock()
    message = MagicMock()
    message.content = [MagicMock(type="text", text=json.dumps(response_json))]
    client.messages.create.return_value = message
    return client


def _make_entries(
    prompts: list[str],
    session_id: str = "sess-001",
    cwd: str = "/projects/app",
    day: str = "2026-04-21",
) -> list[LogEntry]:
    entries = []
    hour = 9
    for i, prompt in enumerate(prompts):
        entries.append(
            LogEntry(
                timestamp=f"{day}T{hour:02d}:{i * 5:02d}:00.000Z",
                session_id=session_id,
                project="app",
                entry_type="user_prompt",
                content=prompt,
                cwd=cwd,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# TestClassifySession
# ---------------------------------------------------------------------------

class TestClassifySession:
    """End-to-end tests for classify_session."""

    def test_classify_feature_session(self):
        from claude_standup.classifier import classify_session

        response = {
            "activities": [
                {
                    "classification": "FEATURE",
                    "summary": "Implemented user authentication",
                    "files_mentioned": ["auth.py"],
                    "technologies": ["Python", "JWT"],
                    "time_spent_minutes": 30,
                    "prompt_indices": [0, 1],
                }
            ]
        }
        client = _make_mock_client(response)
        entries = _make_entries(["Add login endpoint", "Wire up JWT tokens"])

        activities = classify_session(
            client, entries, git_org="acme-corp", git_repo="my-app"
        )

        assert len(activities) == 1
        act = activities[0]
        assert act.classification == "FEATURE"
        assert act.summary == "Implemented user authentication"
        assert act.git_org == "acme-corp"
        assert act.git_repo == "my-app"
        assert act.day == "2026-04-21"

    def test_classify_mixed_session(self):
        from claude_standup.classifier import classify_session

        response = {
            "activities": [
                {
                    "classification": "BUGFIX",
                    "summary": "Fixed null pointer in parser",
                    "files_mentioned": ["parser.py"],
                    "technologies": ["Python"],
                    "time_spent_minutes": 15,
                    "prompt_indices": [0],
                },
                {
                    "classification": "REVIEW",
                    "summary": "Reviewed auth module PR",
                    "files_mentioned": ["auth.py"],
                    "technologies": ["Python"],
                    "time_spent_minutes": 10,
                    "prompt_indices": [1],
                },
            ]
        }
        client = _make_mock_client(response)
        entries = _make_entries(["Fix the null pointer bug", "Review auth PR #42"])

        activities = classify_session(client, entries)

        assert len(activities) == 2
        assert activities[0].classification == "BUGFIX"
        assert activities[1].classification == "REVIEW"

    def test_api_called_with_correct_model(self):
        from claude_standup.classifier import MODEL, classify_session

        response = {
            "activities": [
                {
                    "classification": "FEATURE",
                    "summary": "Added feature",
                    "files_mentioned": [],
                    "technologies": [],
                    "time_spent_minutes": 10,
                    "prompt_indices": [0],
                }
            ]
        }
        client = _make_mock_client(response)
        entries = _make_entries(["Implement feature X"])

        classify_session(client, entries)

        call_kwargs = client.messages.create.call_args
        assert call_kwargs.kwargs.get("model") == MODEL
        assert MODEL == "claude-opus-4-6-20250414"

    def test_empty_entries(self):
        from claude_standup.classifier import classify_session

        client = MagicMock()
        activities = classify_session(client, [])

        assert activities == []
        client.messages.create.assert_not_called()


# ---------------------------------------------------------------------------
# TestBuildPrompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    """Tests for _build_classification_prompt."""

    def test_includes_prompts(self):
        from claude_standup.classifier import _build_classification_prompt

        entries = _make_entries(["Implement login", "Add OAuth2"])
        prompt = _build_classification_prompt(entries)

        assert "Implement login" in prompt
        assert "Add OAuth2" in prompt

    def test_includes_timestamps(self):
        from claude_standup.classifier import _build_classification_prompt

        entries = _make_entries(["Hello world"], day="2026-04-21")
        prompt = _build_classification_prompt(entries)

        assert "2026-04-21" in prompt


# ---------------------------------------------------------------------------
# TestParseResponse
# ---------------------------------------------------------------------------

class TestParseResponse:
    """Tests for _parse_classification_response."""

    def test_valid_response(self):
        from claude_standup.classifier import _parse_classification_response

        entries = _make_entries(["Implement login", "Add OAuth2", "Write tests"])
        response_json = {
            "activities": [
                {
                    "classification": "FEATURE",
                    "summary": "Implemented login with OAuth2",
                    "files_mentioned": ["auth.py"],
                    "technologies": ["Python", "OAuth2"],
                    "time_spent_minutes": 30,
                    "prompt_indices": [0, 1],
                }
            ]
        }

        activities = _parse_classification_response(
            json.dumps(response_json),
            entries,
            project="app",
            git_org="acme",
            git_repo="my-app",
        )

        assert len(activities) == 1
        act = activities[0]
        assert act.classification == "FEATURE"
        assert act.summary == "Implemented login with OAuth2"
        assert act.files_mentioned == ["auth.py"]
        assert act.technologies == ["Python", "OAuth2"]
        assert act.time_spent_minutes == 30
        assert act.raw_prompts == ["Implement login", "Add OAuth2"]
        assert act.project == "app"
        assert act.git_org == "acme"
        assert act.git_repo == "my-app"

    def test_malformed_json(self):
        from claude_standup.classifier import _parse_classification_response

        entries = _make_entries(["Hello"])
        activities = _parse_classification_response(
            "this is not json {{{",
            entries,
            project="app",
            git_org=None,
            git_repo=None,
        )
        assert activities == []

    def test_missing_activities_key(self):
        from claude_standup.classifier import _parse_classification_response

        entries = _make_entries(["Hello"])
        activities = _parse_classification_response(
            json.dumps({"results": []}),
            entries,
            project="app",
            git_org=None,
            git_repo=None,
        )
        assert activities == []


# ---------------------------------------------------------------------------
# TestRetryAndErrorHandling
# ---------------------------------------------------------------------------

class TestRetryAndErrorHandling:
    """Tests for _call_api_with_retry error handling and retry logic."""

    @patch("claude_standup.classifier.time.sleep")
    def test_rate_limit_retry(self, mock_sleep):
        from claude_standup.classifier import _call_api_with_retry

        client = MagicMock()
        success_message = MagicMock()
        success_message.content = [MagicMock(type="text", text='{"activities": []}')]

        rate_limit_error = anthropic.RateLimitError(
            message="rate limited",
            response=MagicMock(status_code=429, headers={}),
            body={"error": {"type": "rate_limit_error", "message": "rate limited"}},
        )

        client.messages.create.side_effect = [rate_limit_error, success_message]

        result = _call_api_with_retry(client, "test prompt")

        assert result == '{"activities": []}'
        assert client.messages.create.call_count == 2
        mock_sleep.assert_called_once()

    def test_fatal_error_raises(self):
        from claude_standup.classifier import _call_api_with_retry

        client = MagicMock()

        api_error = anthropic.APIError(
            message="server error",
            request=MagicMock(),
            body={"error": {"type": "api_error", "message": "server error"}},
        )

        client.messages.create.side_effect = api_error

        with pytest.raises(anthropic.APIError):
            _call_api_with_retry(client, "test prompt")
