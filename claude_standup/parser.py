"""Parser module: discovers and reads Claude Code JSONL log files.

Primary data source: `away_summary` entries (type=system, subtype=away_summary).
These are high-quality session recaps written by Claude Code itself.
Fallback: user prompts + tool descriptions for sessions without summaries.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from claude_standup.models import FileInfo, GitInfo, LogEntry


def discover_jsonl_files(base_path: Path | str) -> list[FileInfo]:
    """Recursively find .jsonl files under *base_path*, returning each with its mtime."""
    base = Path(base_path)
    if not base.exists():
        return []
    results: list[FileInfo] = []
    for root, _dirs, filenames in os.walk(base):
        for fname in filenames:
            if fname.endswith(".jsonl"):
                full = os.path.join(root, fname)
                results.append(FileInfo(path=full, mtime=os.path.getmtime(full)))
    return results


def parse_session_summaries(file_path: str, project_name: str) -> list[dict]:
    """Extract away_summary entries from a JSONL file.

    Returns a list of dicts: {timestamp, session_id, project, content, cwd, git_branch}
    These are the primary source for standup reports.
    """
    summaries = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") == "system" and obj.get("subtype") == "away_summary":
                content = obj.get("content", "").strip()
                if content:
                    summaries.append({
                        "timestamp": obj.get("timestamp", ""),
                        "session_id": obj.get("sessionId", ""),
                        "project": project_name,
                        "content": content,
                        "cwd": obj.get("cwd", ""),
                        "git_branch": obj.get("gitBranch"),
                    })

    return summaries


def parse_jsonl_file(file_path: str, project_name: str) -> list[LogEntry]:
    """Parse a JSONL file and return normalized LogEntry records.

    Extracts user prompts, assistant text blocks, and tool_use descriptions.
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
                if _is_noise_prompt(content):
                    continue
                entries.append(
                    LogEntry(
                        timestamp=timestamp,
                        session_id=session_id,
                        project=project_name,
                        entry_type="user_prompt",
                        content=content,
                        cwd=cwd,
                        git_branch=git_branch,
                    )
                )

            elif entry_type == "assistant":
                if not isinstance(content, list):
                    continue

                text_parts: list[str] = []
                tool_names: list[str] = []
                tool_descriptions: list[str] = []

                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_use":
                        tool_names.append(block.get("name", "unknown"))
                        desc = block.get("input", {}).get("description", "")
                        if desc:
                            tool_descriptions.append(desc)

                if text_parts:
                    entries.append(
                        LogEntry(
                            timestamp=timestamp,
                            session_id=session_id,
                            project=project_name,
                            entry_type="assistant_text",
                            content="\n".join(text_parts),
                            cwd=cwd,
                            git_branch=git_branch,
                        )
                    )
                if tool_names:
                    entries.append(
                        LogEntry(
                            timestamp=timestamp,
                            session_id=session_id,
                            project=project_name,
                            entry_type="tool_use",
                            content="\n".join(tool_descriptions),
                            cwd=cwd,
                            git_branch=git_branch,
                            tool_names=tool_names,
                        )
                    )

    return entries


# ---------------------------------------------------------------------------
# Noise filtering
# ---------------------------------------------------------------------------

_MIN_PROMPT_LENGTH = 5

_NOISE_EXACT = frozenset({
    "y", "n", "yes", "no", "ok", "sure", "continue", "go", "go ahead",
    "si", "sí", "vale", "dale", "warmup", "clear",
})

_NOISE_PREFIXES = (
    "<local-command-caveat>",
    "You receive a CLI command",
    "You are implementing ",
    "You are reviewing ",
    "You are creating ",
    "You are refactoring ",
    "You are a developer-activity classifier",
    "You are a concise standup-report generator",
    "Review the code quality",
    "Review the complete implementation",
    "Review the implementation",
    "brainstorming:",
    '{"activities"',
)


def _is_noise_prompt(content: str) -> bool:
    """Return True if *content* is a trivial/noise prompt that should be skipped."""
    stripped = content.strip()
    if len(stripped) < _MIN_PROMPT_LENGTH:
        return True
    if stripped.lower() in _NOISE_EXACT:
        return True
    for prefix in _NOISE_PREFIXES:
        if stripped.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Project name and git remote helpers
# ---------------------------------------------------------------------------

def derive_project_name(dir_name: str) -> str:
    """Convert a Claude Code project directory name to a readable project name."""
    name = dir_name.lstrip("-")
    parts = name.split("-")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "workspace":
            remainder = parts[i + 1:]
            if remainder:
                return "-".join(remainder)
            break
    if len(parts) > 2:
        return "-".join(parts[2:])
    return ""


def parse_remote_url(url: str) -> GitInfo:
    """Extract GitHub org and repo from a git remote URL."""
    if not url:
        return GitInfo()
    ssh_match = re.match(r"git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return GitInfo(org=ssh_match.group(1), repo=ssh_match.group(2))
    https_match = re.match(r"https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return GitInfo(org=https_match.group(1), repo=https_match.group(2))
    return GitInfo()


def resolve_git_remote(cwd: str, cache: dict[str, GitInfo]) -> GitInfo:
    """Get GitHub org/repo by running git remote get-url origin on the given cwd."""
    if cwd in cache:
        return cache[cwd]
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            info = parse_remote_url(result.stdout.strip())
        else:
            info = GitInfo()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        info = GitInfo()
    cache[cwd] = info
    return info
