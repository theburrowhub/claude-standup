"""Shared test fixtures for claude-standup."""

from __future__ import annotations

import pytest

from claude_standup.models import Activity


@pytest.fixture
def sample_user_entry() -> dict:
    """A single valid user prompt JSONL entry as a Python dict."""
    return {
        "type": "user",
        "message": {"role": "user", "content": "Implement the login feature"},
        "timestamp": "2026-04-21T10:00:00.000Z",
        "sessionId": "abc-123",
        "cwd": "/Users/dev/projects/my-app",
        "gitBranch": "feat/login",
    }


@pytest.fixture
def sample_assistant_entry() -> dict:
    """A single valid assistant response JSONL entry with text and tool_use blocks."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me think about this..."},
                {"type": "text", "text": "I'll implement the login feature now."},
                {"type": "tool_use", "id": "tool_1", "name": "Edit", "input": {"file": "auth.py"}},
            ],
        },
        "timestamp": "2026-04-21T10:00:05.000Z",
        "sessionId": "abc-123",
        "cwd": "/Users/dev/projects/my-app",
        "gitBranch": "feat/login",
    }


@pytest.fixture
def sample_tool_result_entry() -> dict:
    """A user entry that is actually a tool result (should be skipped)."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tool_1", "content": "OK"}],
        },
        "toolUseResult": True,
        "timestamp": "2026-04-21T10:00:10.000Z",
        "sessionId": "abc-123",
        "cwd": "/Users/dev/projects/my-app",
    }


@pytest.fixture
def sample_activities() -> list[Activity]:
    """A list of pre-classified activities for reporter tests."""
    return [
        Activity(
            session_id="abc-123",
            day="2026-04-20",
            project="my-app",
            git_org="acme-corp",
            git_repo="my-app",
            classification="FEATURE",
            summary="Implemented user login with OAuth2",
            files_mentioned=["auth.py", "login.html"],
            technologies=["Python", "OAuth2"],
            time_spent_minutes=45,
            raw_prompts=["Implement the login feature", "Add OAuth2 support"],
        ),
        Activity(
            session_id="abc-123",
            day="2026-04-20",
            project="my-app",
            git_org="acme-corp",
            git_repo="my-app",
            classification="BUGFIX",
            summary="Fixed session expiration bug",
            files_mentioned=["session.py"],
            technologies=["Python", "Redis"],
            time_spent_minutes=20,
            raw_prompts=["Fix the session expiration issue"],
        ),
        Activity(
            session_id="manual",
            day="2026-04-20",
            project="",
            git_org=None,
            git_repo=None,
            classification="MEETING",
            summary="Sprint planning with backend team",
            time_spent_minutes=0,
            raw_prompts=[],
        ),
    ]
