"""Shared data models used across all modules."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileInfo:
    """A discovered JSONL file with its modification time."""
    path: str
    mtime: float


@dataclass
class GitInfo:
    """GitHub organization and repository extracted from a git remote URL."""
    org: str | None = None
    repo: str | None = None


@dataclass
class LogEntry:
    """A normalized entry parsed from a Claude Code JSONL log file."""
    timestamp: str
    session_id: str
    project: str
    entry_type: str  # "user_prompt" | "assistant_text" | "tool_use"
    content: str
    cwd: str
    git_branch: str | None = None
    tool_names: list[str] = field(default_factory=list)


@dataclass
class Activity:
    """A classified development activity derived from one or more log entries."""
    session_id: str
    day: str  # YYYY-MM-DD
    project: str
    git_org: str | None
    git_repo: str | None
    classification: str  # FEATURE, BUGFIX, REFACTOR, DEBUGGING, EXPLORATION, REVIEW, SUPPORT, MEETING, OTHER
    summary: str
    files_mentioned: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    time_spent_minutes: int = 0
    raw_prompts: list[str] = field(default_factory=list)
