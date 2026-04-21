# claude-standup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that parses Claude Code JSONL logs and generates daily standup reports using the Claude API.

**Architecture:** Pipeline of 5 modules: `parser` reads JSONL files and extracts relevant entries, `cache` stores processed data in SQLite for incremental processing, `classifier` uses Claude API to categorize activities, `reporter` uses Claude API to generate standup reports in markdown/slack format, and `cli` wires everything together with argparse.

**Tech Stack:** Python 3.11+, anthropic SDK, SQLite (stdlib), pytest + pytest-cov

**Spec:** `docs/superpowers/specs/2026-04-21-claude-standup-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `pyproject.toml` | Package config, dependencies, entry point |
| `claude_standup/__init__.py` | Package init, version |
| `claude_standup/models.py` | Shared data classes used across modules |
| `claude_standup/parser.py` | JSONL discovery, parsing, git org/repo resolution |
| `claude_standup/cache.py` | SQLite cache: file tracking, sessions, activities, queries |
| `claude_standup/classifier.py` | Claude API classification of session prompts into activities |
| `claude_standup/reporter.py` | Claude API report generation + markdown/slack formatting |
| `claude_standup/cli.py` | argparse CLI, command routing, pipeline orchestration |
| `tests/conftest.py` | Shared fixtures: tmp JSONL dirs, cache DBs, mock clients |
| `tests/fixtures/*.jsonl` | Sample JSONL data files mimicking real Claude Code logs |
| `tests/test_parser.py` | Parser unit tests |
| `tests/test_cache.py` | Cache unit tests |
| `tests/test_classifier.py` | Classifier unit tests (mocked API) |
| `tests/test_reporter.py` | Reporter unit tests (mocked API) |
| `tests/test_cli.py` | CLI unit tests |
| `tests/test_integration.py` | End-to-end pipeline tests |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `claude_standup/__init__.py`
- Create: `claude_standup/models.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "claude-standup"
version = "0.1.0"
description = "Generate daily standup reports from Claude Code activity logs"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.40.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[project.scripts]
claude-standup = "claude_standup.cli:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create claude_standup/__init__.py**

```python
"""claude-standup: Generate daily standup reports from Claude Code activity logs."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create claude_standup/models.py**

```python
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
```

- [ ] **Step 4: Create tests/__init__.py and tests/conftest.py**

`tests/__init__.py`: empty file.

`tests/conftest.py`:

```python
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
```

- [ ] **Step 5: Install in dev mode and verify**

Run: `cd /Users/jamuriano/personal-workspace/claude-standup && pip install -e ".[dev]"`
Expected: successful installation, `claude-standup` command registered (will fail at runtime since cli.py doesn't exist yet — that's fine).

- [ ] **Step 6: Verify imports work**

Run: `python -c "from claude_standup.models import LogEntry, Activity, FileInfo, GitInfo; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml claude_standup/__init__.py claude_standup/models.py tests/__init__.py tests/conftest.py
git commit -m "feat: project scaffolding with models and test fixtures"
```

---

### Task 2: Test Fixture JSONL Files

**Files:**
- Create: `tests/fixtures/valid_session.jsonl`
- Create: `tests/fixtures/malformed_lines.jsonl`
- Create: `tests/fixtures/empty_session.jsonl`
- Create: `tests/fixtures/multi_session.jsonl`
- Create: `tests/fixtures/tool_heavy_session.jsonl`

- [ ] **Step 1: Create tests/fixtures/valid_session.jsonl**

A well-formed session with user prompts, assistant responses, tool uses, and entries to skip:

```jsonl
{"type":"queue-operation","operation":"enqueue","timestamp":"2026-04-21T09:00:00.000Z","sessionId":"sess-001","content":"Implement login"}
{"type":"queue-operation","operation":"dequeue","timestamp":"2026-04-21T09:00:00.000Z","sessionId":"sess-001"}
{"type":"user","message":{"role":"user","content":"Implement the login feature with OAuth2"},"timestamp":"2026-04-21T09:00:01.000Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app","gitBranch":"feat/login"}
{"type":"attachment","attachment":{"type":"deferred_tools_delta","addedNames":["Edit","Bash"]},"timestamp":"2026-04-21T09:00:01.500Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"I need to implement OAuth2 login..."},{"type":"text","text":"I'll implement the login feature with OAuth2 support."},{"type":"tool_use","id":"tool_1","name":"Edit","input":{"file":"auth.py"}}]},"timestamp":"2026-04-21T09:00:10.000Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app","gitBranch":"feat/login"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tool_1","content":"File edited successfully"}]},"toolUseResult":true,"timestamp":"2026-04-21T09:00:15.000Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Login feature implemented. The auth.py file now handles OAuth2 flow."}]},"timestamp":"2026-04-21T09:00:20.000Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app","gitBranch":"feat/login"}
{"type":"user","message":{"role":"user","content":"Now add unit tests for the login"},"timestamp":"2026-04-21T09:30:00.000Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app","gitBranch":"feat/login"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll write the unit tests for login."},{"type":"tool_use","id":"tool_2","name":"Write","input":{"file":"tests/test_auth.py"}}]},"timestamp":"2026-04-21T09:30:05.000Z","sessionId":"sess-001","cwd":"/Users/dev/projects/my-app","gitBranch":"feat/login"}
```

- [ ] **Step 2: Create tests/fixtures/malformed_lines.jsonl**

Mix of valid and malformed lines:

```jsonl
{"type":"user","message":{"role":"user","content":"Fix the bug"},"timestamp":"2026-04-21T10:00:00.000Z","sessionId":"sess-002","cwd":"/Users/dev/projects/api","gitBranch":"fix/bug"}
this is not valid json at all
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I found the bug."}]},"timestamp":"2026-04-21T10:00:05.000Z","sessionId":"sess-002","cwd":"/Users/dev/projects/api","gitBranch":"fix/bug"}
{"broken json": true, missing closing
{"type":"user","message":{"role":"user","content":"Great, commit it"},"timestamp":"2026-04-21T10:05:00.000Z","sessionId":"sess-002","cwd":"/Users/dev/projects/api","gitBranch":"fix/bug"}
```

- [ ] **Step 3: Create tests/fixtures/empty_session.jsonl**

```
```

(Empty file — zero bytes.)

- [ ] **Step 4: Create tests/fixtures/multi_session.jsonl**

Two different sessions interleaved (different sessionIds):

```jsonl
{"type":"user","message":{"role":"user","content":"Refactor the database layer"},"timestamp":"2026-04-20T14:00:00.000Z","sessionId":"sess-010","cwd":"/Users/dev/projects/backend","gitBranch":"refactor/db"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll refactor the database layer to use the repository pattern."}]},"timestamp":"2026-04-20T14:00:10.000Z","sessionId":"sess-010","cwd":"/Users/dev/projects/backend","gitBranch":"refactor/db"}
{"type":"user","message":{"role":"user","content":"Review PR #42 in the frontend repo"},"timestamp":"2026-04-20T14:05:00.000Z","sessionId":"sess-011","cwd":"/Users/dev/projects/frontend","gitBranch":"main"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll review PR #42 now."},{"type":"tool_use","id":"tool_5","name":"Bash","input":{"command":"gh pr view 42"}}]},"timestamp":"2026-04-20T14:05:05.000Z","sessionId":"sess-011","cwd":"/Users/dev/projects/frontend","gitBranch":"main"}
{"type":"user","message":{"role":"user","content":"Continue with the repository pattern"},"timestamp":"2026-04-20T14:10:00.000Z","sessionId":"sess-010","cwd":"/Users/dev/projects/backend","gitBranch":"refactor/db"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Continuing with the repository pattern implementation."}]},"timestamp":"2026-04-20T14:10:05.000Z","sessionId":"sess-010","cwd":"/Users/dev/projects/backend","gitBranch":"refactor/db"}
```

- [ ] **Step 5: Create tests/fixtures/tool_heavy_session.jsonl**

Session with many tool_use blocks and tool_results (mostly noise):

```jsonl
{"type":"user","message":{"role":"user","content":"Debug the API timeout issue"},"timestamp":"2026-04-21T11:00:00.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api","gitBranch":"fix/timeout"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Let me investigate the timeout."},{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"grep -r timeout"}},{"type":"tool_use","id":"t2","name":"Read","input":{"file":"config.py"}},{"type":"tool_use","id":"t3","name":"Grep","input":{"pattern":"timeout"}}]},"timestamp":"2026-04-21T11:00:05.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api","gitBranch":"fix/timeout"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"config.py:10: timeout=30"},{"type":"tool_result","tool_use_id":"t2","content":"TIMEOUT=30\nRETRY=3"},{"type":"tool_result","tool_use_id":"t3","content":"5 matches"}]},"toolUseResult":true,"timestamp":"2026-04-21T11:00:10.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Found it. The timeout is set to 30s but the upstream API takes 45s. I'll increase it."},{"type":"tool_use","id":"t4","name":"Edit","input":{"file":"config.py","old":"timeout=30","new":"timeout=60"}}]},"timestamp":"2026-04-21T11:00:15.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api","gitBranch":"fix/timeout"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t4","content":"File edited"}]},"toolUseResult":true,"timestamp":"2026-04-21T11:00:20.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api"}
{"type":"user","message":{"role":"user","content":"yes"},"timestamp":"2026-04-21T11:00:25.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api","gitBranch":"fix/timeout"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Done. Timeout increased to 60s."}]},"timestamp":"2026-04-21T11:00:30.000Z","sessionId":"sess-020","cwd":"/Users/dev/projects/api","gitBranch":"fix/timeout"}
```

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/
git commit -m "test: add JSONL fixture files for parser tests"
```

---

### Task 3: Parser Module

**Files:**
- Create: `claude_standup/parser.py`
- Create: `tests/test_parser.py`

- [ ] **Step 1: Write failing tests for file discovery**

`tests/test_parser.py`:

```python
"""Tests for claude_standup.parser module."""

from __future__ import annotations

import json
from pathlib import Path

from claude_standup.parser import discover_jsonl_files, parse_jsonl_file, derive_project_name, parse_remote_url


class TestDiscoverJsonlFiles:
    def test_finds_jsonl_files(self, tmp_path: Path):
        project_dir = tmp_path / "-Users-dev-project-alpha"
        project_dir.mkdir()
        (project_dir / "session1.jsonl").write_text("{}\n")
        (project_dir / "session2.jsonl").write_text("{}\n")
        (project_dir / "session1" / "subagents").mkdir(parents=True)
        (project_dir / "session1" / "subagents" / "agent-abc.jsonl").write_text("{}\n")
        (project_dir / "notes.txt").write_text("not a jsonl")

        files = discover_jsonl_files(tmp_path)
        paths = [f.path for f in files]
        assert len(paths) == 3
        assert all(p.endswith(".jsonl") for p in paths)

    def test_empty_directory(self, tmp_path: Path):
        files = discover_jsonl_files(tmp_path)
        assert files == []

    def test_returns_mtime(self, tmp_path: Path):
        project_dir = tmp_path / "-project"
        project_dir.mkdir()
        f = project_dir / "s.jsonl"
        f.write_text("{}\n")

        files = discover_jsonl_files(tmp_path)
        assert len(files) == 1
        assert files[0].mtime > 0
        assert files[0].path == str(f)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parser.py -v`
Expected: `ImportError: cannot import name 'discover_jsonl_files' from 'claude_standup.parser'`

- [ ] **Step 3: Implement discover_jsonl_files**

`claude_standup/parser.py`:

```python
"""Parser module: discovers and reads Claude Code JSONL log files."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from claude_standup.models import FileInfo, GitInfo, LogEntry


def discover_jsonl_files(base_path: Path | str) -> list[FileInfo]:
    """Recursively find all .jsonl files under base_path and return with mtime."""
    base = Path(base_path)
    if not base.exists():
        return []
    results = []
    for root, _dirs, files in os.walk(base):
        for fname in files:
            if fname.endswith(".jsonl"):
                full_path = os.path.join(root, fname)
                mtime = os.path.getmtime(full_path)
                results.append(FileInfo(path=full_path, mtime=mtime))
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_parser.py::TestDiscoverJsonlFiles -v`
Expected: 3 passed

- [ ] **Step 5: Write failing tests for JSONL parsing**

Append to `tests/test_parser.py`:

```python
FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestParseJsonlFile:
    def test_parse_valid_session(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project-my-app")
        user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
        assistant_texts = [e for e in entries if e.entry_type == "assistant_text"]
        tool_uses = [e for e in entries if e.entry_type == "tool_use"]

        assert len(user_prompts) == 2
        assert user_prompts[0].content == "Implement the login feature with OAuth2"
        assert user_prompts[1].content == "Now add unit tests for the login"

        assert len(assistant_texts) >= 2
        assert "OAuth2" in assistant_texts[0].content

        assert len(tool_uses) >= 1

    def test_skip_queue_operations(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project")
        types = {e.entry_type for e in entries}
        assert "queue-operation" not in types

    def test_skip_attachments(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project")
        for e in entries:
            assert "deferred_tools" not in e.content

    def test_skip_tool_results(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project")
        for e in entries:
            if e.entry_type == "user_prompt":
                assert "tool_result" not in e.content

    def test_skip_thinking_blocks(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project")
        for e in entries:
            assert "I need to implement OAuth2" not in e.content

    def test_extract_tool_use_names(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project")
        tool_entries = [e for e in entries if e.entry_type == "tool_use"]
        all_tools = []
        for e in tool_entries:
            all_tools.extend(e.tool_names)
        assert "Edit" in all_tools
        assert "Write" in all_tools

    def test_extract_metadata(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "valid_session.jsonl"), "-project")
        first = entries[0]
        assert first.session_id == "sess-001"
        assert first.cwd == "/Users/dev/projects/my-app"
        assert first.git_branch == "feat/login"
        assert first.timestamp.startswith("2026-04-21")

    def test_malformed_lines(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "malformed_lines.jsonl"), "-project")
        user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
        assert len(user_prompts) == 2  # 2 valid user prompts, 2 malformed lines skipped

    def test_empty_file(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "empty_session.jsonl"), "-project")
        assert entries == []

    def test_multi_session(self):
        entries = parse_jsonl_file(str(FIXTURES_DIR / "multi_session.jsonl"), "-project")
        session_ids = {e.session_id for e in entries}
        assert "sess-010" in session_ids
        assert "sess-011" in session_ids
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_parser.py::TestParseJsonlFile -v`
Expected: `ImportError: cannot import name 'parse_jsonl_file'`

- [ ] **Step 7: Implement parse_jsonl_file**

Append to `claude_standup/parser.py`:

```python
def parse_jsonl_file(file_path: str, project_name: str) -> list[LogEntry]:
    """Parse a single JSONL file and return normalized LogEntry records.

    Extracts user prompts, assistant text blocks, and tool_use names.
    Skips queue-operation, attachment, thinking blocks, and tool_result entries.
    """
    entries: list[LogEntry] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = obj.get("type")
            if entry_type not in ("user", "assistant"):
                continue

            timestamp = obj.get("timestamp", "")
            session_id = obj.get("sessionId", "")
            cwd = obj.get("cwd", "")
            git_branch = obj.get("gitBranch")
            message = obj.get("message", {})
            content = message.get("content")

            if entry_type == "user":
                if obj.get("toolUseResult"):
                    continue
                if not isinstance(content, str):
                    continue
                entries.append(LogEntry(
                    timestamp=timestamp,
                    session_id=session_id,
                    project=project_name,
                    entry_type="user_prompt",
                    content=content,
                    cwd=cwd,
                    git_branch=git_branch,
                ))

            elif entry_type == "assistant":
                if not isinstance(content, list):
                    continue
                text_parts = []
                tool_names = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_use":
                        tool_names.append(block.get("name", "unknown"))

                if text_parts:
                    entries.append(LogEntry(
                        timestamp=timestamp,
                        session_id=session_id,
                        project=project_name,
                        entry_type="assistant_text",
                        content="\n".join(text_parts),
                        cwd=cwd,
                        git_branch=git_branch,
                    ))
                if tool_names:
                    entries.append(LogEntry(
                        timestamp=timestamp,
                        session_id=session_id,
                        project=project_name,
                        entry_type="tool_use",
                        content="",
                        cwd=cwd,
                        git_branch=git_branch,
                        tool_names=tool_names,
                    ))

    return entries
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_parser.py::TestParseJsonlFile -v`
Expected: all passed

- [ ] **Step 9: Write failing tests for derive_project_name and parse_remote_url**

Append to `tests/test_parser.py`:

```python
class TestDeriveProjectName:
    def test_standard_path(self):
        assert derive_project_name("-Users-jamuriano-workspace-ai-gateway") == "ai-gateway"

    def test_nested_path(self):
        assert derive_project_name("-Users-jamuriano-personal-workspace-overmind") == "overmind"

    def test_deep_nested(self):
        assert derive_project_name("-Users-jamuriano-personal-workspace-overmind-admin") == "overmind-admin"

    def test_simple(self):
        assert derive_project_name("-Users-dev-my-app") == "my-app"

    def test_single_segment(self):
        assert derive_project_name("-") == ""


class TestParseRemoteUrl:
    def test_ssh_url(self):
        info = parse_remote_url("git@github.com:freepik-company/ai-gateway.git")
        assert info.org == "freepik-company"
        assert info.repo == "ai-gateway"

    def test_https_url(self):
        info = parse_remote_url("https://github.com/overmind-swarm/bot-data.git")
        assert info.org == "overmind-swarm"
        assert info.repo == "bot-data"

    def test_https_url_no_dot_git(self):
        info = parse_remote_url("https://github.com/acme/repo")
        assert info.org == "acme"
        assert info.repo == "repo"

    def test_invalid_url(self):
        info = parse_remote_url("not-a-url")
        assert info.org is None
        assert info.repo is None

    def test_empty_string(self):
        info = parse_remote_url("")
        assert info.org is None
        assert info.repo is None
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `pytest tests/test_parser.py::TestDeriveProjectName tests/test_parser.py::TestParseRemoteUrl -v`
Expected: failures

- [ ] **Step 11: Implement derive_project_name and parse_remote_url**

Append to `claude_standup/parser.py`:

```python
def derive_project_name(dir_name: str) -> str:
    """Convert a Claude Code project directory name to a readable project name.

    Input: "-Users-jamuriano-personal-workspace-overmind-admin"
    Output: "overmind-admin"

    Strategy: strip the leading user path segments (everything before "workspace-"
    or the last 1-2 segments if no workspace marker is found).
    """
    # Strip leading dash
    name = dir_name.lstrip("-")
    parts = name.split("-")

    # Find the last "workspace" segment and take everything after it
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "workspace":
            remainder = parts[i + 1:]
            if remainder:
                return "-".join(remainder)
            break

    # Fallback: take everything after the username (3rd segment onward)
    # Pattern: Users-<username>-<rest>
    if len(parts) > 2:
        return "-".join(parts[2:])
    return ""


def parse_remote_url(url: str) -> GitInfo:
    """Extract GitHub org and repo from a git remote URL.

    Supports:
    - git@github.com:org/repo.git
    - https://github.com/org/repo.git
    - https://github.com/org/repo
    """
    if not url:
        return GitInfo()

    # SSH format: git@github.com:org/repo.git
    ssh_match = re.match(r"git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return GitInfo(org=ssh_match.group(1), repo=ssh_match.group(2))

    # HTTPS format: https://github.com/org/repo.git
    https_match = re.match(r"https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return GitInfo(org=https_match.group(1), repo=https_match.group(2))

    return GitInfo()
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `pytest tests/test_parser.py -v`
Expected: all passed

- [ ] **Step 13: Write failing test for resolve_git_remote with caching**

Append to `tests/test_parser.py`:

```python
from unittest.mock import patch, MagicMock
from claude_standup.parser import resolve_git_remote


class TestResolveGitRemote:
    def test_resolves_remote(self):
        with patch("claude_standup.parser.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="git@github.com:acme-corp/my-app.git\n",
            )
            info = resolve_git_remote("/Users/dev/projects/my-app", cache={})
            assert info.org == "acme-corp"
            assert info.repo == "my-app"
            mock_run.assert_called_once()

    def test_no_remote(self):
        with patch("claude_standup.parser.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            info = resolve_git_remote("/tmp/no-git", cache={})
            assert info.org is None
            assert info.repo is None

    def test_cached_result(self):
        cache = {"/Users/dev/projects/my-app": GitInfo(org="acme", repo="app")}
        with patch("claude_standup.parser.subprocess.run") as mock_run:
            info = resolve_git_remote("/Users/dev/projects/my-app", cache=cache)
            assert info.org == "acme"
            assert info.repo == "app"
            mock_run.assert_not_called()

    def test_nonexistent_directory(self):
        with patch("claude_standup.parser.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("No such directory")
            info = resolve_git_remote("/nonexistent/path", cache={})
            assert info.org is None
            assert info.repo is None
```

- [ ] **Step 14: Run test to verify it fails**

Run: `pytest tests/test_parser.py::TestResolveGitRemote -v`
Expected: `ImportError: cannot import name 'resolve_git_remote'`

- [ ] **Step 15: Implement resolve_git_remote**

Append to `claude_standup/parser.py`:

```python
def resolve_git_remote(cwd: str, cache: dict[str, GitInfo]) -> GitInfo:
    """Get GitHub org/repo by running git remote get-url origin on the given cwd.

    Results are cached per cwd path in the provided cache dict.
    """
    if cwd in cache:
        return cache[cwd]

    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            info = parse_remote_url(result.stdout.strip())
        else:
            info = GitInfo()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        info = GitInfo()

    cache[cwd] = info
    return info
```

- [ ] **Step 16: Run all parser tests**

Run: `pytest tests/test_parser.py -v`
Expected: all passed

- [ ] **Step 17: Commit**

```bash
git add claude_standup/parser.py tests/test_parser.py
git commit -m "feat: implement parser module with JSONL parsing and git remote resolution"
```

---

### Task 4: Cache Module

**Files:**
- Create: `claude_standup/cache.py`
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write failing tests for schema creation and file tracking**

`tests/test_cache.py`:

```python
"""Tests for claude_standup.cache module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_standup.cache import CacheDB
from claude_standup.models import Activity


class TestSchemaCreation:
    def test_creates_tables(self, tmp_path: Path):
        db = CacheDB(str(tmp_path / "test.db"))
        cursor = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "activities" in tables
        assert "files" in tables
        assert "sessions" in tables
        db.close()

    def test_in_memory_db(self):
        db = CacheDB(":memory:")
        cursor = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert len(tables) == 3
        db.close()


class TestFileTracking:
    def test_mark_file_processed(self, tmp_path: Path):
        db = CacheDB(":memory:")
        db.mark_file_processed("/path/to/file.jsonl", 1234567.0)
        row = db.conn.execute("SELECT * FROM files WHERE path = ?", ("/path/to/file.jsonl",)).fetchone()
        assert row is not None
        assert row[1] == 1234567.0  # mtime
        db.close()

    def test_detect_new_files(self, tmp_path: Path):
        db = CacheDB(":memory:")
        from claude_standup.models import FileInfo
        files = [
            FileInfo(path="/a.jsonl", mtime=100.0),
            FileInfo(path="/b.jsonl", mtime=200.0),
        ]
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 2
        db.close()

    def test_detect_modified_files(self, tmp_path: Path):
        db = CacheDB(":memory:")
        db.mark_file_processed("/a.jsonl", 100.0)
        from claude_standup.models import FileInfo
        files = [FileInfo(path="/a.jsonl", mtime=200.0)]  # mtime changed
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 1
        db.close()

    def test_skip_unchanged_files(self, tmp_path: Path):
        db = CacheDB(":memory:")
        db.mark_file_processed("/a.jsonl", 100.0)
        from claude_standup.models import FileInfo
        files = [FileInfo(path="/a.jsonl", mtime=100.0)]  # same mtime
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 0
        db.close()

    def test_reprocess_clears_file_tracking(self):
        db = CacheDB(":memory:")
        db.mark_file_processed("/a.jsonl", 100.0)
        db.clear_file_tracking()
        from claude_standup.models import FileInfo
        files = [FileInfo(path="/a.jsonl", mtime=100.0)]
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 1
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py -v`
Expected: `ModuleNotFoundError: No module named 'claude_standup.cache'`

- [ ] **Step 3: Implement CacheDB with schema and file tracking**

`claude_standup/cache.py`:

```python
"""Cache module: SQLite-backed storage for processed sessions and activities."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from claude_standup.models import Activity, FileInfo


class CacheDB:
    """SQLite cache for tracking processed files, sessions, and classified activities."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime REAL,
                processed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project TEXT,
                git_org TEXT,
                git_repo TEXT,
                first_ts TEXT,
                last_ts TEXT
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                day TEXT,
                project TEXT,
                git_org TEXT,
                git_repo TEXT,
                classification TEXT,
                summary TEXT,
                files_mentioned TEXT,
                technologies TEXT,
                time_spent_minutes INTEGER,
                raw_prompts TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
        """)
        self.conn.commit()

    def mark_file_processed(self, path: str, mtime: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime, processed_at) VALUES (?, ?, ?)",
            (path, mtime, now),
        )
        self.conn.commit()

    def get_unprocessed_files(self, files: list[FileInfo]) -> list[FileInfo]:
        """Return files that are new or have a changed mtime."""
        unprocessed = []
        for f in files:
            row = self.conn.execute(
                "SELECT mtime FROM files WHERE path = ?", (f.path,)
            ).fetchone()
            if row is None or row[0] != f.mtime:
                unprocessed.append(f)
        return unprocessed

    def clear_file_tracking(self) -> None:
        self.conn.execute("DELETE FROM files")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -v`
Expected: all passed

- [ ] **Step 5: Write failing tests for session and activity storage and queries**

Append to `tests/test_cache.py`:

```python
class TestSessionStorage:
    def test_store_session(self):
        db = CacheDB(":memory:")
        db.store_session("sess-001", "my-app", "acme", "my-app", "2026-04-21T09:00:00Z", "2026-04-21T10:00:00Z")
        row = db.conn.execute("SELECT * FROM sessions WHERE session_id = ?", ("sess-001",)).fetchone()
        assert row is not None
        assert row[1] == "my-app"  # project
        assert row[2] == "acme"    # git_org
        assert row[3] == "my-app"  # git_repo
        db.close()

    def test_update_session_timestamps(self):
        db = CacheDB(":memory:")
        db.store_session("sess-001", "my-app", "acme", "my-app", "2026-04-21T09:00:00Z", "2026-04-21T09:30:00Z")
        db.store_session("sess-001", "my-app", "acme", "my-app", "2026-04-21T09:00:00Z", "2026-04-21T10:00:00Z")
        row = db.conn.execute("SELECT last_ts FROM sessions WHERE session_id = ?", ("sess-001",)).fetchone()
        assert row[0] == "2026-04-21T10:00:00Z"
        db.close()


class TestActivityStorage:
    def test_store_and_retrieve_activities(self):
        db = CacheDB(":memory:")
        activities = [
            Activity(
                session_id="sess-001", day="2026-04-21", project="my-app",
                git_org="acme", git_repo="my-app", classification="FEATURE",
                summary="Added login", files_mentioned=["auth.py"],
                technologies=["Python"], time_spent_minutes=30,
                raw_prompts=["Implement login"],
            ),
        ]
        db.store_activities(activities)
        result = db.query_activities(date_from="2026-04-21", date_to="2026-04-21")
        assert len(result) == 1
        assert result[0].summary == "Added login"
        assert result[0].files_mentioned == ["auth.py"]
        assert result[0].technologies == ["Python"]
        db.close()

    def test_query_by_date_range(self):
        db = CacheDB(":memory:")
        activities = [
            Activity(session_id="s1", day="2026-04-19", project="p", git_org=None, git_repo=None, classification="FEATURE", summary="Day 19"),
            Activity(session_id="s1", day="2026-04-20", project="p", git_org=None, git_repo=None, classification="BUGFIX", summary="Day 20"),
            Activity(session_id="s1", day="2026-04-21", project="p", git_org=None, git_repo=None, classification="REFACTOR", summary="Day 21"),
        ]
        db.store_activities(activities)
        result = db.query_activities(date_from="2026-04-20", date_to="2026-04-20")
        assert len(result) == 1
        assert result[0].summary == "Day 20"
        db.close()

    def test_query_by_org(self):
        db = CacheDB(":memory:")
        activities = [
            Activity(session_id="s1", day="2026-04-21", project="p1", git_org="acme", git_repo="app1", classification="FEATURE", summary="Acme work"),
            Activity(session_id="s2", day="2026-04-21", project="p2", git_org="other", git_repo="app2", classification="BUGFIX", summary="Other work"),
        ]
        db.store_activities(activities)
        result = db.query_activities(date_from="2026-04-21", date_to="2026-04-21", orgs=["acme"])
        assert len(result) == 1
        assert result[0].summary == "Acme work"
        db.close()

    def test_query_by_multiple_orgs(self):
        db = CacheDB(":memory:")
        activities = [
            Activity(session_id="s1", day="2026-04-21", project="p1", git_org="acme", git_repo="app1", classification="FEATURE", summary="A"),
            Activity(session_id="s2", day="2026-04-21", project="p2", git_org="beta", git_repo="app2", classification="BUGFIX", summary="B"),
            Activity(session_id="s3", day="2026-04-21", project="p3", git_org="gamma", git_repo="app3", classification="OTHER", summary="C"),
        ]
        db.store_activities(activities)
        result = db.query_activities(date_from="2026-04-21", date_to="2026-04-21", orgs=["acme", "beta"])
        assert len(result) == 2
        summaries = {a.summary for a in result}
        assert summaries == {"A", "B"}
        db.close()

    def test_query_by_repo(self):
        db = CacheDB(":memory:")
        activities = [
            Activity(session_id="s1", day="2026-04-21", project="p1", git_org="acme", git_repo="gateway", classification="FEATURE", summary="GW"),
            Activity(session_id="s2", day="2026-04-21", project="p2", git_org="acme", git_repo="frontend", classification="BUGFIX", summary="FE"),
        ]
        db.store_activities(activities)
        result = db.query_activities(date_from="2026-04-21", date_to="2026-04-21", repos=["gateway"])
        assert len(result) == 1
        assert result[0].summary == "GW"
        db.close()

    def test_query_by_org_and_repo(self):
        db = CacheDB(":memory:")
        activities = [
            Activity(session_id="s1", day="2026-04-21", project="p1", git_org="acme", git_repo="gateway", classification="FEATURE", summary="A-GW"),
            Activity(session_id="s2", day="2026-04-21", project="p2", git_org="beta", git_repo="gateway", classification="BUGFIX", summary="B-GW"),
            Activity(session_id="s3", day="2026-04-21", project="p3", git_org="acme", git_repo="frontend", classification="OTHER", summary="A-FE"),
        ]
        db.store_activities(activities)
        result = db.query_activities(date_from="2026-04-21", date_to="2026-04-21", orgs=["acme"], repos=["gateway"])
        assert len(result) == 1
        assert result[0].summary == "A-GW"
        db.close()


class TestManualEntries:
    def test_store_manual_entry(self):
        db = CacheDB(":memory:")
        db.store_manual_entry(
            summary="Sprint planning",
            classification="MEETING",
            git_org=None,
            git_repo=None,
        )
        result = db.query_activities(date_from="2026-04-01", date_to="2026-12-31")
        assert len(result) == 1
        assert result[0].session_id == "manual"
        assert result[0].classification == "MEETING"
        assert result[0].summary == "Sprint planning"
        db.close()

    def test_manual_entry_with_org_repo(self):
        db = CacheDB(":memory:")
        db.store_manual_entry(
            summary="Helped with deploy",
            classification="SUPPORT",
            git_org="acme",
            git_repo="infra",
        )
        result = db.query_activities(date_from="2026-04-01", date_to="2026-12-31", orgs=["acme"])
        assert len(result) == 1
        assert result[0].git_org == "acme"
        assert result[0].git_repo == "infra"
        db.close()
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_cache.py::TestSessionStorage tests/test_cache.py::TestActivityStorage tests/test_cache.py::TestManualEntries -v`
Expected: `AttributeError: 'CacheDB' object has no attribute 'store_session'`

- [ ] **Step 7: Implement session storage, activity storage, and queries**

Append to `claude_standup/cache.py`:

```python
    def store_session(self, session_id: str, project: str, git_org: str | None,
                      git_repo: str | None, first_ts: str, last_ts: str) -> None:
        self.conn.execute(
            """INSERT INTO sessions (session_id, project, git_org, git_repo, first_ts, last_ts)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   last_ts = excluded.last_ts""",
            (session_id, project, git_org, git_repo, first_ts, last_ts),
        )
        self.conn.commit()

    def store_activities(self, activities: list[Activity]) -> None:
        for a in activities:
            self.conn.execute(
                """INSERT INTO activities
                   (session_id, day, project, git_org, git_repo, classification,
                    summary, files_mentioned, technologies, time_spent_minutes, raw_prompts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a.session_id, a.day, a.project, a.git_org, a.git_repo,
                    a.classification, a.summary,
                    json.dumps(a.files_mentioned), json.dumps(a.technologies),
                    a.time_spent_minutes, json.dumps(a.raw_prompts),
                ),
            )
        self.conn.commit()

    def store_manual_entry(self, summary: str, classification: str,
                           git_org: str | None, git_repo: str | None) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        activity = Activity(
            session_id="manual",
            day=today,
            project="",
            git_org=git_org,
            git_repo=git_repo,
            classification=classification,
            summary=summary,
        )
        self.store_activities([activity])

    def query_activities(self, date_from: str, date_to: str,
                         orgs: list[str] | None = None,
                         repos: list[str] | None = None) -> list[Activity]:
        query = "SELECT * FROM activities WHERE day >= ? AND day <= ?"
        params: list = [date_from, date_to]

        if orgs:
            placeholders = ",".join("?" for _ in orgs)
            query += f" AND git_org IN ({placeholders})"
            params.extend(orgs)

        if repos:
            placeholders = ",".join("?" for _ in repos)
            query += f" AND git_repo IN ({placeholders})"
            params.extend(repos)

        query += " ORDER BY day, id"
        rows = self.conn.execute(query, params).fetchall()

        results = []
        for row in rows:
            results.append(Activity(
                session_id=row[1],
                day=row[2],
                project=row[3],
                git_org=row[4],
                git_repo=row[5],
                classification=row[6],
                summary=row[7],
                files_mentioned=json.loads(row[8]) if row[8] else [],
                technologies=json.loads(row[9]) if row[9] else [],
                time_spent_minutes=row[10] or 0,
                raw_prompts=json.loads(row[11]) if row[11] else [],
            ))
        return results
```

- [ ] **Step 8: Run all cache tests**

Run: `pytest tests/test_cache.py -v`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add claude_standup/cache.py tests/test_cache.py
git commit -m "feat: implement cache module with SQLite storage and query filtering"
```

---

### Task 5: Classifier Module

**Files:**
- Create: `claude_standup/classifier.py`
- Create: `tests/test_classifier.py`

- [ ] **Step 1: Write failing tests for classify_session**

`tests/test_classifier.py`:

```python
"""Tests for claude_standup.classifier module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from claude_standup.classifier import classify_session, _build_classification_prompt, _parse_classification_response
from claude_standup.models import Activity, LogEntry


def _make_mock_client(response_json: dict) -> MagicMock:
    """Create a mock Anthropic client that returns the given JSON as a text response."""
    client = MagicMock()
    message = MagicMock()
    message.content = [MagicMock(type="text", text=json.dumps(response_json))]
    client.messages.create.return_value = message
    return client


def _make_entries(prompts: list[str], session_id: str = "sess-001",
                  cwd: str = "/projects/app", day: str = "2026-04-21") -> list[LogEntry]:
    """Build a list of LogEntry objects from prompt strings."""
    entries = []
    hour = 9
    for i, prompt in enumerate(prompts):
        entries.append(LogEntry(
            timestamp=f"{day}T{hour:02d}:{i*5:02d}:00.000Z",
            session_id=session_id,
            project="app",
            entry_type="user_prompt",
            content=prompt,
            cwd=cwd,
        ))
    return entries


class TestClassifySession:
    def test_classify_feature_session(self):
        response = {
            "activities": [
                {
                    "classification": "FEATURE",
                    "summary": "Implemented user authentication with OAuth2",
                    "files_mentioned": ["auth.py", "login.html"],
                    "technologies": ["Python", "OAuth2"],
                    "time_spent_minutes": 45,
                    "prompt_indices": [0, 1],
                }
            ]
        }
        client = _make_mock_client(response)
        entries = _make_entries(["Implement login with OAuth2", "Add the callback handler"])

        activities = classify_session(client, entries, git_org="acme", git_repo="app")

        assert len(activities) == 1
        assert activities[0].classification == "FEATURE"
        assert activities[0].summary == "Implemented user authentication with OAuth2"
        assert activities[0].git_org == "acme"
        assert activities[0].git_repo == "app"
        assert activities[0].day == "2026-04-21"

    def test_classify_mixed_session(self):
        response = {
            "activities": [
                {
                    "classification": "BUGFIX",
                    "summary": "Fixed timeout issue",
                    "files_mentioned": ["config.py"],
                    "technologies": ["Python"],
                    "time_spent_minutes": 15,
                    "prompt_indices": [0],
                },
                {
                    "classification": "REVIEW",
                    "summary": "Reviewed PR #42",
                    "files_mentioned": [],
                    "technologies": [],
                    "time_spent_minutes": 10,
                    "prompt_indices": [1],
                },
            ]
        }
        client = _make_mock_client(response)
        entries = _make_entries(["Fix the timeout bug", "Review PR #42"])

        activities = classify_session(client, entries, git_org=None, git_repo=None)
        assert len(activities) == 2
        classifications = {a.classification for a in activities}
        assert classifications == {"BUGFIX", "REVIEW"}

    def test_api_called_with_correct_model(self):
        response = {"activities": []}
        client = _make_mock_client(response)
        entries = _make_entries(["test"])

        classify_session(client, entries)
        call_kwargs = client.messages.create.call_args
        assert "claude-opus-4-6-20250414" in call_kwargs.kwargs.get("model", "")

    def test_empty_entries(self):
        client = MagicMock()
        activities = classify_session(client, [])
        assert activities == []
        client.messages.create.assert_not_called()


class TestBuildPrompt:
    def test_includes_prompts(self):
        entries = _make_entries(["Implement login", "Add tests"])
        prompt = _build_classification_prompt(entries)
        assert "Implement login" in prompt
        assert "Add tests" in prompt

    def test_includes_timestamps(self):
        entries = _make_entries(["Do something"])
        prompt = _build_classification_prompt(entries)
        assert "2026-04-21" in prompt


class TestParseResponse:
    def test_valid_response(self):
        response_text = json.dumps({
            "activities": [
                {
                    "classification": "FEATURE",
                    "summary": "Built feature X",
                    "files_mentioned": ["x.py"],
                    "technologies": ["Python"],
                    "time_spent_minutes": 30,
                    "prompt_indices": [0],
                }
            ]
        })
        entries = _make_entries(["Build feature X"])
        activities = _parse_classification_response(response_text, entries, "proj", None, None)
        assert len(activities) == 1
        assert activities[0].raw_prompts == ["Build feature X"]

    def test_malformed_json(self):
        activities = _parse_classification_response("not json", [], "proj", None, None)
        assert activities == []

    def test_missing_activities_key(self):
        activities = _parse_classification_response('{"data": []}', [], "proj", None, None)
        assert activities == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_classifier.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement classifier module**

`claude_standup/classifier.py`:

```python
"""Classifier module: uses Claude API to classify session activities."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import anthropic

from claude_standup.models import Activity, LogEntry

if TYPE_CHECKING:
    pass

CLASSIFICATION_SYSTEM_PROMPT = """\
You are a development activity classifier. You receive a list of user prompts from a Claude Code \
session with timestamps, and you must:

1. Group related prompts into logical activities (merge sequences about the same task).
2. Classify each activity into one of: FEATURE, BUGFIX, REFACTOR, DEBUGGING, EXPLORATION, REVIEW, SUPPORT, MEETING, OTHER.
3. Extract file paths mentioned in the prompts.
4. Detect technologies used (programming languages, frameworks, tools).
5. Estimate time spent per activity based on timestamp gaps between prompts.

Ignore trivial prompts: single-word confirmations ("yes", "y", "ok", "continue", "sí"), \
retries, and very short responses that don't represent meaningful work. Merge them into the \
parent activity.

Respond with a JSON object (no markdown fences) with this exact structure:
{
  "activities": [
    {
      "classification": "FEATURE",
      "summary": "One-line description of what was done",
      "files_mentioned": ["path/to/file.py"],
      "technologies": ["Python", "FastAPI"],
      "time_spent_minutes": 30,
      "prompt_indices": [0, 1, 2]
    }
  ]
}

prompt_indices refers to the zero-based indices of the prompts that belong to this activity.
If there are no meaningful activities, return {"activities": []}.
"""

MODEL = "claude-opus-4-6-20250414"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0


def classify_session(
    client: anthropic.Anthropic,
    entries: list[LogEntry],
    git_org: str | None = None,
    git_repo: str | None = None,
) -> list[Activity]:
    """Classify a session's log entries into structured activities using the Claude API."""
    user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
    if not user_prompts:
        return []

    prompt = _build_classification_prompt(user_prompts)

    response_text = _call_api_with_retry(client, prompt)
    if response_text is None:
        return []

    project = user_prompts[0].project if user_prompts else ""
    return _parse_classification_response(response_text, user_prompts, project, git_org, git_repo)


def _build_classification_prompt(entries: list[LogEntry]) -> str:
    """Build the user message containing all session prompts with timestamps."""
    lines = []
    for i, entry in enumerate(entries):
        lines.append(f"[{i}] ({entry.timestamp}) {entry.content}")
    return "Classify these development session prompts:\n\n" + "\n".join(lines)


def _call_api_with_retry(client: anthropic.Anthropic, prompt: str) -> str | None:
    """Call the Claude API with exponential backoff retry on rate limit errors."""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=CLASSIFICATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            for block in response.content:
                if block.type == "text":
                    return block.text
            return None
        except anthropic.RateLimitError:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_BASE_DELAY * (2 ** attempt))
    return None


def _parse_classification_response(
    response_text: str,
    entries: list[LogEntry],
    project: str,
    git_org: str | None,
    git_repo: str | None,
) -> list[Activity]:
    """Parse the JSON response from Claude into Activity objects."""
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return []

    if "activities" not in data:
        return []

    activities = []
    for item in data["activities"]:
        indices = item.get("prompt_indices", [])
        raw_prompts = [entries[i].content for i in indices if i < len(entries)]

        # Determine day from the first prompt in this activity
        day = ""
        if indices and indices[0] < len(entries):
            day = entries[indices[0]].timestamp[:10]
        elif entries:
            day = entries[0].timestamp[:10]

        activities.append(Activity(
            session_id=entries[0].session_id if entries else "",
            day=day,
            project=project,
            git_org=git_org,
            git_repo=git_repo,
            classification=item.get("classification", "OTHER"),
            summary=item.get("summary", ""),
            files_mentioned=item.get("files_mentioned", []),
            technologies=item.get("technologies", []),
            time_spent_minutes=item.get("time_spent_minutes", 0),
            raw_prompts=raw_prompts,
        ))

    return activities
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_classifier.py -v`
Expected: all passed

- [ ] **Step 5: Write failing tests for retry and truncation**

Append to `tests/test_classifier.py`:

```python
class TestRetryAndErrorHandling:
    def test_rate_limit_retry(self):
        client = MagicMock()
        response_ok = MagicMock()
        response_ok.content = [MagicMock(type="text", text='{"activities": []}')]

        client.messages.create.side_effect = [
            anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body={"error": {"type": "rate_limit_error", "message": "rate limited"}},
            ),
            response_ok,
        ]
        entries = _make_entries(["test prompt"])

        with patch("claude_standup.classifier.time.sleep"):
            activities = classify_session(client, entries)

        assert activities == []
        assert client.messages.create.call_count == 2

    def test_fatal_error_raises(self):
        client = MagicMock()
        client.messages.create.side_effect = anthropic.APIError(
            message="server error",
            request=MagicMock(),
            body={"error": {"type": "api_error", "message": "server error"}},
        )
        entries = _make_entries(["test prompt"])

        with pytest.raises(anthropic.APIError):
            classify_session(client, entries)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_classifier.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add claude_standup/classifier.py tests/test_classifier.py
git commit -m "feat: implement classifier module with Claude API integration and retry logic"
```

---

### Task 6: Reporter Module

**Files:**
- Create: `claude_standup/reporter.py`
- Create: `tests/test_reporter.py`

- [ ] **Step 1: Write failing tests for report generation**

`tests/test_reporter.py`:

```python
"""Tests for claude_standup.reporter module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_standup.models import Activity
from claude_standup.reporter import generate_report, format_as_slack, format_as_markdown


SAMPLE_MARKDOWN_REPORT = """\
## 2026-04-20

### Yesterday
- Implemented user login with OAuth2 (acme/my-app) ~45min
- Fixed session expiration bug (acme/my-app) ~20min
- Sprint planning with backend team

### Today
- Continue OAuth2 integration testing
- Deploy session fix to staging

### Blockers
- None identified
"""

SAMPLE_SLACK_REPORT = """\
*2026-04-20*

*Yesterday*
• Implemented user login with OAuth2 (acme/my-app) ~45min
• Fixed session expiration bug (acme/my-app) ~20min
• Sprint planning with backend team

*Today*
• Continue OAuth2 integration testing
• Deploy session fix to staging

*Blockers*
• None identified
"""


def _make_mock_client(report_text: str) -> MagicMock:
    client = MagicMock()
    message = MagicMock()
    message.content = [MagicMock(type="text", text=report_text)]
    client.messages.create.return_value = message
    return client


class TestGenerateReport:
    def test_generates_markdown(self, sample_activities: list[Activity]):
        client = _make_mock_client(SAMPLE_MARKDOWN_REPORT)
        report = generate_report(
            client, sample_activities,
            lang="es", output_format="markdown",
        )
        assert "## 2026-04-20" in report
        assert "### Yesterday" in report or "### Ayer" in report
        client.messages.create.assert_called_once()

    def test_generates_slack(self, sample_activities: list[Activity]):
        client = _make_mock_client(SAMPLE_SLACK_REPORT)
        report = generate_report(
            client, sample_activities,
            lang="es", output_format="slack",
        )
        assert "*2026-04-20*" in report
        client.messages.create.assert_called_once()

    def test_empty_activities(self):
        client = MagicMock()
        report = generate_report(client, [], lang="es", output_format="markdown")
        assert "No activity found" in report or "No se encontró actividad" in report
        client.messages.create.assert_not_called()

    def test_lang_passed_to_prompt(self, sample_activities: list[Activity]):
        client = _make_mock_client("report")
        generate_report(client, sample_activities, lang="en", output_format="markdown")
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "English" in user_msg or "en" in user_msg

    def test_format_passed_to_prompt(self, sample_activities: list[Activity]):
        client = _make_mock_client("report")
        generate_report(client, sample_activities, lang="es", output_format="slack")
        call_args = client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Slack" in user_msg or "slack" in user_msg


class TestFormatAsMarkdown:
    def test_basic_structure(self):
        raw = "## 2026-04-20\n\n### Yesterday\n- Did X\n\n### Today\n- Do Y\n\n### Blockers\n- None"
        result = format_as_markdown(raw)
        assert "## 2026-04-20" in result
        assert "- Did X" in result

    def test_passthrough(self):
        raw = "Already formatted markdown"
        assert format_as_markdown(raw) == raw


class TestFormatAsSlack:
    def test_basic_structure(self):
        raw = "*2026-04-20*\n\n*Yesterday*\n• Did X\n\n*Today*\n• Do Y"
        result = format_as_slack(raw)
        assert "*2026-04-20*" in result

    def test_passthrough(self):
        raw = "Already formatted"
        assert format_as_slack(raw) == raw


class TestFileOutput:
    def test_write_to_file(self, tmp_path: Path, sample_activities: list[Activity]):
        client = _make_mock_client("## Report\n- Done")
        report = generate_report(client, sample_activities, lang="es", output_format="markdown")
        output_file = tmp_path / "standup.md"
        output_file.write_text(report)
        assert output_file.read_text() == report
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reporter.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement reporter module**

`claude_standup/reporter.py`:

```python
"""Reporter module: generates standup reports using the Claude API."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import anthropic

from claude_standup.models import Activity

if TYPE_CHECKING:
    pass

MODEL = "claude-opus-4-6-20250414"

REPORTER_SYSTEM_PROMPT = """\
You are a standup report generator for a software developer. You receive a list of classified \
development activities with summaries, and you must produce a clean daily standup report.

Rules:
- Group activities by day.
- For each day, produce three sections: Yesterday (what was done), Today (inferred next steps), Blockers.
- Infer "Today" from incomplete work, patterns, and what logically follows.
- Identify blockers from: repeated failures, long debugging sessions, confusion, or unresolved issues.
- If an activity has org/repo info, include it in parentheses: "Did X (org/repo)".
- If an activity has time_spent_minutes > 0, append "~Xmin" to the bullet.
- Be concise: prefer fewer high-quality bullets over many low-value ones.
- Include manual entries (meetings, support) alongside development activities.
- If no blockers are found, write "None identified".
"""


def generate_report(
    client: anthropic.Anthropic,
    activities: list[Activity],
    lang: str = "es",
    output_format: str = "markdown",
) -> str:
    """Generate a standup report from classified activities using the Claude API."""
    if not activities:
        if lang == "es":
            return "No se encontró actividad para este periodo."
        return "No activity found for this period."

    prompt = _build_report_prompt(activities, lang, output_format)

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=REPORTER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    report_text = ""
    for block in response.content:
        if block.type == "text":
            report_text = block.text
            break

    if output_format == "slack":
        return format_as_slack(report_text)
    return format_as_markdown(report_text)


def _build_report_prompt(activities: list[Activity], lang: str, output_format: str) -> str:
    """Build the user message for the reporter API call."""
    lang_name = "Spanish" if lang == "es" else "English"
    format_desc = "Slack (use *bold* for headers, • for bullets)" if output_format == "slack" \
        else "Markdown (use ## for day headers, ### for sections, - for bullets)"

    lines = [
        f"Generate a standup report in {lang_name} using {format_desc} formatting.",
        "",
        "Activities:",
        "",
    ]

    for a in activities:
        org_repo = ""
        if a.git_org and a.git_repo:
            org_repo = f" ({a.git_org}/{a.git_repo})"
        elif a.git_org:
            org_repo = f" ({a.git_org})"

        time_str = f" ~{a.time_spent_minutes}min" if a.time_spent_minutes else ""
        lines.append(
            f"- [{a.day}] [{a.classification}]{org_repo} {a.summary}{time_str}"
        )

    return "\n".join(lines)


def format_as_markdown(text: str) -> str:
    """Ensure the text is valid Markdown. Currently a passthrough since Claude generates Markdown."""
    return text


def format_as_slack(text: str) -> str:
    """Ensure the text uses Slack formatting. Currently a passthrough since Claude generates Slack format."""
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reporter.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add claude_standup/reporter.py tests/test_reporter.py
git commit -m "feat: implement reporter module with markdown and slack output"
```

---

### Task 7: CLI Module

**Files:**
- Create: `claude_standup/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for argument parsing**

`tests/test_cli.py`:

```python
"""Tests for claude_standup.cli module."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_standup.cli import parse_args, resolve_date_range, main


class TestParseArgs:
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
        args = parse_args(["log", "Sprint planning meeting"])
        assert args.command == "log"
        assert args.message == "Sprint planning meeting"

    def test_default_command(self):
        args = parse_args([])
        assert args.command == "today"

    def test_from_to_flags(self):
        args = parse_args(["--from", "2026-04-14", "--to", "2026-04-21"])
        assert args.date_from == "2026-04-14"
        assert args.date_to == "2026-04-21"

    def test_org_filter_single(self):
        args = parse_args(["--org", "acme"])
        assert args.org == "acme"

    def test_repo_filter_single(self):
        args = parse_args(["--repo", "gateway"])
        assert args.repo == "gateway"

    def test_org_filter_multiple(self):
        args = parse_args(["--org", "acme,beta"])
        assert args.org == "acme,beta"

    def test_repo_filter_multiple(self):
        args = parse_args(["--repo", "gw,fe"])
        assert args.repo == "gw,fe"

    def test_lang_flag(self):
        args = parse_args(["--lang", "en"])
        assert args.lang == "en"

    def test_lang_default(self):
        args = parse_args([])
        assert args.lang == "es"

    def test_format_flag(self):
        args = parse_args(["--format", "slack"])
        assert args.format == "slack"

    def test_format_default(self):
        args = parse_args([])
        assert args.format == "markdown"

    def test_output_flag(self):
        args = parse_args(["--output", "report.md"])
        assert args.output == "report.md"

    def test_reprocess_flag(self):
        args = parse_args(["--reprocess"])
        assert args.reprocess is True

    def test_verbose_flag(self):
        args = parse_args(["--verbose"])
        assert args.verbose is True

    def test_log_with_type(self):
        args = parse_args(["log", "Helped team", "--type", "SUPPORT"])
        assert args.type == "SUPPORT"

    def test_log_type_default(self):
        args = parse_args(["log", "Something"])
        assert args.type == "OTHER"

    def test_log_with_org_repo(self):
        args = parse_args(["log", "Deploy fix", "--org", "acme", "--repo", "infra"])
        assert args.org == "acme"
        assert args.repo == "infra"


class TestResolveDateRange:
    def test_today(self):
        d_from, d_to = resolve_date_range("today", None, None)
        today = date.today().isoformat()
        assert d_from == today
        assert d_to == today

    def test_yesterday(self):
        d_from, d_to = resolve_date_range("yesterday", None, None)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        assert d_from == yesterday
        assert d_to == yesterday

    def test_last_7_days(self):
        d_from, d_to = resolve_date_range("last-7-days", None, None)
        expected_from = (date.today() - timedelta(days=6)).isoformat()
        expected_to = date.today().isoformat()
        assert d_from == expected_from
        assert d_to == expected_to

    def test_custom_range_overrides(self):
        d_from, d_to = resolve_date_range("today", "2026-04-14", "2026-04-21")
        assert d_from == "2026-04-14"
        assert d_to == "2026-04-21"

    def test_partial_override_from(self):
        d_from, d_to = resolve_date_range("today", "2026-04-14", None)
        assert d_from == "2026-04-14"
        assert d_to == date.today().isoformat()


class TestMain:
    def test_missing_api_key(self, tmp_path: Path, capsys):
        with patch.dict("os.environ", {}, clear=True):
            with patch("claude_standup.cli.os.environ.get", return_value=None):
                with pytest.raises(SystemExit) as exc_info:
                    main(["today"], logs_base=str(tmp_path), db_path=":memory:")
                assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.err

    def test_log_command_stores_entry(self, tmp_path: Path):
        with patch("claude_standup.cli.os.environ.get", return_value="test-key"):
            main(["log", "Team meeting", "--type", "MEETING"], logs_base=str(tmp_path), db_path=str(tmp_path / "test.db"))
        # Verify it was stored by reading the DB
        from claude_standup.cache import CacheDB
        db = CacheDB(str(tmp_path / "test.db"))
        activities = db.query_activities("2026-01-01", "2026-12-31")
        assert len(activities) == 1
        assert activities[0].summary == "Team meeting"
        assert activities[0].classification == "MEETING"
        db.close()

    def test_report_output_to_file(self, tmp_path: Path):
        output_file = tmp_path / "report.md"
        with patch("claude_standup.cli.os.environ.get", return_value="test-key"), \
             patch("claude_standup.cli._run_report_pipeline") as mock_pipeline:
            mock_pipeline.return_value = "## Report\n- Done"
            main(["today", "--output", str(output_file)], logs_base=str(tmp_path), db_path=":memory:")
        assert output_file.read_text() == "## Report\n- Done"

    def test_verbose_output(self, tmp_path: Path, capsys):
        with patch("claude_standup.cli.os.environ.get", return_value="test-key"), \
             patch("claude_standup.cli._run_report_pipeline") as mock_pipeline:
            mock_pipeline.return_value = "## Report"
            main(["today", "--verbose"], logs_base=str(tmp_path), db_path=":memory:")
        captured = capsys.readouterr()
        assert "## Report" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement cli module**

`claude_standup/cli.py`:

```python
"""CLI module: argument parsing and pipeline orchestration."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic

from claude_standup.cache import CacheDB
from claude_standup.classifier import classify_session
from claude_standup.models import GitInfo
from claude_standup.parser import (
    derive_project_name,
    discover_jsonl_files,
    parse_jsonl_file,
    resolve_git_remote,
)
from claude_standup.reporter import generate_report

DEFAULT_LOGS_BASE = str(Path.home() / ".claude" / "projects")
DEFAULT_DB_PATH = str(Path.home() / ".claude-standup" / "cache.db")

VALID_TYPES = [
    "FEATURE", "BUGFIX", "REFACTOR", "DEBUGGING", "EXPLORATION",
    "REVIEW", "SUPPORT", "MEETING", "OTHER",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="claude-standup",
        description="Generate daily standup reports from Claude Code activity logs",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Report commands
    subparsers.add_parser("today", help="Report for today")
    subparsers.add_parser("yesterday", help="Report for yesterday")
    subparsers.add_parser("last-7-days", help="Report for the last 7 days")

    # Log command
    log_parser = subparsers.add_parser("log", help="Register a manual activity")
    log_parser.add_argument("message", help="Description of the activity")
    log_parser.add_argument("--type", choices=VALID_TYPES, default="OTHER", help="Activity type")

    # Global flags
    parser.add_argument("--from", dest="date_from", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", help="End date (YYYY-MM-DD)")
    parser.add_argument("--org", help="Filter by GitHub org(s), comma-separated")
    parser.add_argument("--repo", help="Filter by repo name(s), comma-separated")
    parser.add_argument("--lang", choices=["es", "en"], default="es", help="Report language")
    parser.add_argument("--format", choices=["markdown", "slack"], default="markdown", help="Output format")
    parser.add_argument("--output", help="Write report to file")
    parser.add_argument("--reprocess", action="store_true", help="Ignore cache, reprocess all")
    parser.add_argument("--verbose", action="store_true", help="Show processing progress")

    args = parser.parse_args(argv)

    if args.command is None:
        args.command = "today"

    return args


def resolve_date_range(
    command: str,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, str]:
    """Resolve the date range from command and explicit flags."""
    today = date.today()

    if date_from and date_to:
        return date_from, date_to

    if command == "today":
        base_from = today
        base_to = today
    elif command == "yesterday":
        base_from = today - timedelta(days=1)
        base_to = today - timedelta(days=1)
    elif command == "last-7-days":
        base_from = today - timedelta(days=6)
        base_to = today
    else:
        base_from = today
        base_to = today

    return (
        date_from if date_from else base_from.isoformat(),
        date_to if date_to else base_to.isoformat(),
    )


def _run_report_pipeline(
    client: anthropic.Anthropic,
    db: CacheDB,
    logs_base: str,
    args: argparse.Namespace,
) -> str:
    """Execute the full pipeline: parse → cache → classify → report."""
    d_from, d_to = resolve_date_range(args.command, args.date_from, args.date_to)

    # Step 1: Discover files
    all_files = discover_jsonl_files(logs_base)

    if args.verbose:
        print(f"Found {len(all_files)} JSONL files", file=sys.stderr)

    # Step 2: Filter to unprocessed
    if args.reprocess:
        db.clear_file_tracking()

    unprocessed = db.get_unprocessed_files(all_files)

    if args.verbose:
        print(f"Processing {len(unprocessed)} new/modified files", file=sys.stderr)

    # Step 3: Parse and classify unprocessed files
    git_cache: dict[str, GitInfo] = {}

    for file_info in unprocessed:
        # Derive project name from directory
        parent_dir = Path(file_info.path).parent.name
        # Handle subagent paths: go up to the project directory
        file_path = Path(file_info.path)
        if "subagents" in file_path.parts:
            # Go up past subagents/ and session_id/ to project dir
            idx = file_path.parts.index("subagents")
            project_dir_name = file_path.parts[idx - 2] if idx >= 2 else parent_dir
        else:
            project_dir_name = parent_dir

        project_name = derive_project_name(project_dir_name)
        entries = parse_jsonl_file(file_info.path, project_name)

        if entries:
            # Resolve git info from the first entry's cwd
            first_cwd = entries[0].cwd
            git_info = resolve_git_remote(first_cwd, git_cache) if first_cwd else GitInfo()

            # Group entries by session
            sessions: dict[str, list] = {}
            for entry in entries:
                sessions.setdefault(entry.session_id, []).append(entry)

            for session_id, session_entries in sessions.items():
                # Store session metadata
                timestamps = [e.timestamp for e in session_entries if e.timestamp]
                if timestamps:
                    db.store_session(
                        session_id, project_name,
                        git_info.org, git_info.repo,
                        min(timestamps), max(timestamps),
                    )

                # Classify
                activities = classify_session(
                    client, session_entries,
                    git_org=git_info.org, git_repo=git_info.repo,
                )
                if activities:
                    db.store_activities(activities)

        db.mark_file_processed(file_info.path, file_info.mtime)

    # Step 4: Query and generate report
    orgs = args.org.split(",") if args.org else None
    repos = args.repo.split(",") if args.repo else None

    activities = db.query_activities(d_from, d_to, orgs=orgs, repos=repos)

    if args.verbose:
        print(f"Found {len(activities)} activities for {d_from} to {d_to}", file=sys.stderr)

    return generate_report(client, activities, lang=args.lang, output_format=args.format)


def main(argv: list[str] | None = None, logs_base: str | None = None, db_path: str | None = None) -> None:
    """Main entry point for the claude-standup CLI."""
    args = parse_args(argv)

    # Handle log command (doesn't need API key)
    if args.command == "log":
        db_dir = Path(db_path or DEFAULT_DB_PATH).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        db = CacheDB(db_path or DEFAULT_DB_PATH)
        orgs = args.org.split(",") if args.org else [None]
        repos = args.repo.split(",") if args.repo else [None]
        db.store_manual_entry(
            summary=args.message,
            classification=args.type,
            git_org=orgs[0],
            git_repo=repos[0],
        )
        db.close()
        print(f"Logged: [{args.type}] {args.message}", file=sys.stderr)
        return

    # Report commands need API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "Error: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Set it with: export ANTHROPIC_API_KEY=your-key-here",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    effective_db_path = db_path or DEFAULT_DB_PATH
    db_dir = Path(effective_db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)
    db = CacheDB(effective_db_path)

    report = _run_report_pipeline(client, db, logs_base or DEFAULT_LOGS_BASE, args)

    print(report)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report)
        if args.verbose:
            print(f"Report saved to {args.output}", file=sys.stderr)

    db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add claude_standup/cli.py tests/test_cli.py
git commit -m "feat: implement CLI module with argument parsing and pipeline orchestration"
```

---

### Task 8: Integration Tests

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

`tests/test_integration.py`:

```python
"""End-to-end integration tests for claude-standup pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_standup.cache import CacheDB
from claude_standup.classifier import classify_session
from claude_standup.models import Activity
from claude_standup.parser import discover_jsonl_files, parse_jsonl_file, derive_project_name
from claude_standup.reporter import generate_report


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_classifier_response(activities_data: list[dict]) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(type="text", text=json.dumps({"activities": activities_data}))]
    client.messages.create.return_value = response
    return client


def _make_reporter_client(report_text: str) -> MagicMock:
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(type="text", text=report_text)]
    client.messages.create.return_value = response
    return client


class TestFullPipeline:
    def test_parse_classify_report(self, tmp_path: Path):
        """Full pipeline: parse fixture → classify with mock → report with mock."""
        # Step 1: Set up directory structure
        project_dir = tmp_path / "-Users-dev-workspace-my-app"
        project_dir.mkdir()
        # Copy fixture
        fixture_content = (FIXTURES_DIR / "valid_session.jsonl").read_text()
        (project_dir / "sess-001.jsonl").write_text(fixture_content)

        # Step 2: Discover and parse
        files = discover_jsonl_files(tmp_path)
        assert len(files) == 1

        entries = parse_jsonl_file(files[0].path, derive_project_name(project_dir.name))
        user_prompts = [e for e in entries if e.entry_type == "user_prompt"]
        assert len(user_prompts) >= 2

        # Step 3: Classify with mock
        classifier_client = _make_classifier_response([
            {
                "classification": "FEATURE",
                "summary": "Implemented login with OAuth2",
                "files_mentioned": ["auth.py"],
                "technologies": ["Python", "OAuth2"],
                "time_spent_minutes": 30,
                "prompt_indices": [0, 1],
            }
        ])
        activities = classify_session(classifier_client, entries, git_org="dev", git_repo="my-app")
        assert len(activities) == 1
        assert activities[0].classification == "FEATURE"

        # Step 4: Store in cache
        db = CacheDB(":memory:")
        db.store_activities(activities)

        # Step 5: Generate report
        queried = db.query_activities("2026-04-21", "2026-04-21")
        reporter_client = _make_reporter_client("## 2026-04-21\n\n### Yesterday\n- Implemented login with OAuth2")
        report = generate_report(reporter_client, queried, lang="es", output_format="markdown")
        assert "2026-04-21" in report
        assert "login" in report.lower() or "Login" in report
        db.close()

    def test_pipeline_with_manual_entries(self, tmp_path: Path):
        """Manual entries mixed with classified activities."""
        db = CacheDB(":memory:")

        # Add a classified activity
        db.store_activities([
            Activity(
                session_id="sess-001", day="2026-04-21", project="app",
                git_org="acme", git_repo="app", classification="FEATURE",
                summary="Built dashboard", time_spent_minutes=60,
            ),
        ])

        # Add a manual entry
        db.store_manual_entry("Sprint planning", "MEETING", None, None)

        queried = db.query_activities("2026-04-01", "2026-12-31")
        assert len(queried) == 2
        classifications = {a.classification for a in queried}
        assert classifications == {"FEATURE", "MEETING"}
        db.close()

    def test_pipeline_org_filter(self, tmp_path: Path):
        """Org filter works through the pipeline."""
        db = CacheDB(":memory:")
        db.store_activities([
            Activity(session_id="s1", day="2026-04-21", project="p1",
                     git_org="acme", git_repo="app1", classification="FEATURE", summary="Acme work"),
            Activity(session_id="s2", day="2026-04-21", project="p2",
                     git_org="beta", git_repo="app2", classification="BUGFIX", summary="Beta work"),
        ])

        acme_only = db.query_activities("2026-04-21", "2026-04-21", orgs=["acme"])
        assert len(acme_only) == 1
        assert acme_only[0].git_org == "acme"
        db.close()

    def test_incremental_processing(self, tmp_path: Path):
        """Second run skips already-processed files."""
        project_dir = tmp_path / "-project"
        project_dir.mkdir()
        (project_dir / "a.jsonl").write_text(
            '{"type":"user","message":{"role":"user","content":"test"},"timestamp":"2026-04-21T10:00:00Z","sessionId":"s1","cwd":"/tmp"}\n'
        )

        db = CacheDB(":memory:")
        files = discover_jsonl_files(tmp_path)
        assert len(db.get_unprocessed_files(files)) == 1

        db.mark_file_processed(files[0].path, files[0].mtime)
        assert len(db.get_unprocessed_files(files)) == 0
        db.close()

    def test_reprocess_flag(self, tmp_path: Path):
        """Reprocess clears file tracking and re-processes everything."""
        db = CacheDB(":memory:")
        db.mark_file_processed("/a.jsonl", 100.0)

        from claude_standup.models import FileInfo
        files = [FileInfo(path="/a.jsonl", mtime=100.0)]
        assert len(db.get_unprocessed_files(files)) == 0

        db.clear_file_tracking()
        assert len(db.get_unprocessed_files(files)) == 1
        db.close()

    def test_empty_logs_directory(self, tmp_path: Path):
        """Empty directory produces no files."""
        files = discover_jsonl_files(tmp_path)
        assert files == []

        reporter_client = _make_reporter_client("")
        report = generate_report(reporter_client, [], lang="es", output_format="markdown")
        assert "No se encontró actividad" in report
```

- [ ] **Step 2: Run integration tests**

Run: `pytest tests/test_integration.py -v`
Expected: all passed

- [ ] **Step 3: Run full test suite with coverage**

Run: `pytest --cov=claude_standup --cov-report=term-missing -v`
Expected: all passed, coverage targets met (100% on parser/cache/cli, 90%+ on classifier/reporter)

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add end-to-end integration tests for full pipeline"
```

---

### Task 9: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Install and verify entry point**

Run: `pip install -e ".[dev]"`
Run: `claude-standup --help`
Expected: help text with all commands and flags displayed

- [ ] **Step 2: Verify log command works**

Run: `claude-standup log "Test manual entry" --type SUPPORT`
Expected: `Logged: [SUPPORT] Test manual entry` on stderr

- [ ] **Step 3: Run full test suite one more time**

Run: `pytest --cov=claude_standup --cov-report=term-missing -v`
Expected: all tests pass, no regressions

- [ ] **Step 4: Test with real data (requires ANTHROPIC_API_KEY)**

Run: `claude-standup yesterday --verbose`
Expected: processes real logs, outputs a standup report

- [ ] **Step 5: Final commit with any fixes**

If any fixes were needed, commit them. Then tag the release:

```bash
git tag v0.1.0
```
