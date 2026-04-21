"""Tests for claude_standup.classifier module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from claude_standup.classifier import (
    classify_session,
    _build_classification_prompt,
    _parse_classification_response,
)
from claude_standup.models import Activity, LogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_backend(response_json: dict) -> MagicMock:
    """Create a mock LLMBackend that returns the given JSON as text."""
    backend = MagicMock()
    backend.query.return_value = json.dumps(response_json)
    return backend


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
    def test_classify_feature_session(self):
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
        backend = _make_mock_backend(response)
        entries = _make_entries(["Add login endpoint", "Wire up JWT tokens"])

        activities = classify_session(
            backend, entries, git_org="acme-corp", git_repo="my-app"
        )

        assert len(activities) == 1
        act = activities[0]
        assert act.classification == "FEATURE"
        assert act.summary == "Implemented user authentication"
        assert act.git_org == "acme-corp"
        assert act.git_repo == "my-app"
        assert act.day == "2026-04-21"

    def test_classify_mixed_session(self):
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
        backend = _make_mock_backend(response)
        entries = _make_entries(["Fix the null pointer bug", "Review auth PR #42"])

        activities = classify_session(backend, entries)

        assert len(activities) == 2
        assert activities[0].classification == "BUGFIX"
        assert activities[1].classification == "REVIEW"

    def test_backend_query_called(self):
        response = {"activities": []}
        backend = _make_mock_backend(response)
        entries = _make_entries(["Implement feature X"])

        classify_session(backend, entries)

        backend.query.assert_called_once()
        call_args = backend.query.call_args[0]
        assert "classifier" in call_args[0].lower() or "classify" in call_args[0].lower()

    def test_empty_entries(self):
        backend = MagicMock()
        activities = classify_session(backend, [])

        assert activities == []
        backend.query.assert_not_called()

    def test_backend_failure_returns_empty(self):
        backend = MagicMock()
        backend.query.side_effect = RuntimeError("CLI failed")
        entries = _make_entries(["test prompt"])

        activities = classify_session(backend, entries)
        assert activities == []


# ---------------------------------------------------------------------------
# TestBuildPrompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_includes_prompts(self):
        entries = _make_entries(["Implement login", "Add OAuth2"])
        prompt = _build_classification_prompt(entries)

        assert "Implement login" in prompt
        assert "Add OAuth2" in prompt

    def test_includes_timestamps(self):
        entries = _make_entries(["Hello world"], day="2026-04-21")
        prompt = _build_classification_prompt(entries)

        assert "2026-04-21" in prompt


# ---------------------------------------------------------------------------
# TestParseResponse
# ---------------------------------------------------------------------------

class TestParseResponse:
    def test_valid_response(self):
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
            json.dumps(response_json), entries, "app", "acme", "my-app",
        )

        assert len(activities) == 1
        act = activities[0]
        assert act.classification == "FEATURE"
        assert act.raw_prompts == ["Implement login", "Add OAuth2"]

    def test_malformed_json(self):
        entries = _make_entries(["Hello"])
        activities = _parse_classification_response(
            "this is not json {{{", entries, "app", None, None,
        )
        assert activities == []

    def test_missing_activities_key(self):
        entries = _make_entries(["Hello"])
        activities = _parse_classification_response(
            json.dumps({"results": []}), entries, "app", None, None,
        )
        assert activities == []

    def test_strips_markdown_fences(self):
        entries = _make_entries(["Implement X"])
        wrapped = '```json\n{"activities": [{"classification": "FEATURE", "summary": "Did X", "files_mentioned": [], "technologies": [], "time_spent_minutes": 10, "prompt_indices": [0]}]}\n```'
        activities = _parse_classification_response(
            wrapped, entries, "app", None, None,
        )
        assert len(activities) == 1
        assert activities[0].classification == "FEATURE"
