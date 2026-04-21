"""Parser module: discovers and reads Claude Code JSONL log files."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from claude_standup.models import FileInfo, GitInfo, LogEntry

# Prompts shorter than this are almost always noise (confirmations, single words)
_MIN_PROMPT_LENGTH = 5

# Exact-match noise prompts
_NOISE_EXACT = frozenset({
    "y", "n", "yes", "no", "ok", "sure", "continue", "go", "go ahead",
    "si", "sí", "vale", "dale", "warmup", "clear",
})

# Prefix patterns that indicate system/hook prompts, not user intent
_NOISE_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "You receive a CLI command",
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


def discover_jsonl_files(base_path: Path | str) -> list[FileInfo]:
    """Recursively find .jsonl files under *base_path*, returning each with its mtime."""
    base = Path(base_path)
    if not base.exists():
        return []
    results: list[FileInfo] = []
    for root, _dirs, files in os.walk(base):
        for fname in files:
            if fname.endswith(".jsonl"):
                full_path = os.path.join(root, fname)
                mtime = os.path.getmtime(full_path)
                results.append(FileInfo(path=full_path, mtime=mtime))
    return results


def parse_jsonl_file(file_path: str, project_name: str) -> list[LogEntry]:
    """Parse a JSONL log file and return normalized ``LogEntry`` objects.

    - Extracts user prompts (string content only, skips tool_result entries).
    - Extracts assistant text blocks (skips ``thinking`` blocks).
    - Extracts tool_use names.
    - Skips ``queue-operation`` and ``attachment`` entry types entirely.
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
                # Skip tool results
                if obj.get("toolUseResult"):
                    continue
                # Only accept string content (not list/array content)
                if not isinstance(content, str):
                    continue
                # Skip noise: trivial prompts, internal commands, system hooks
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
                        # Capture tool description — rich classification signal
                        desc = block.get("input", {}).get("description", "")
                        if desc:
                            tool_descriptions.append(desc)
                    # thinking blocks are silently skipped

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


def derive_project_name(dir_name: str) -> str:
    """Convert a Claude Code directory name into a human-friendly project name.

    The convention is ``-Users-<user>-<…>-workspace-<project>`` so we find the
    last occurrence of ``"workspace"`` and take everything after it.
    """
    name = dir_name.lstrip("-")
    parts = name.split("-")

    # Walk backwards to find the last "workspace" segment
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "workspace":
            remainder = parts[i + 1 :]
            if remainder:
                return "-".join(remainder)
            break

    # Fallback: if more than two segments, drop the first two (Users, username)
    if len(parts) > 2:
        return "-".join(parts[2:])

    return ""


def parse_remote_url(url: str) -> GitInfo:
    """Extract org and repo from an SSH or HTTPS git remote URL."""
    if not url:
        return GitInfo()

    # SSH: git@github.com:org/repo.git
    ssh_match = re.match(r"git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$", url)
    if ssh_match:
        return GitInfo(org=ssh_match.group(1), repo=ssh_match.group(2))

    # HTTPS: https://github.com/org/repo.git  (or without .git)
    https_match = re.match(r"https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?$", url)
    if https_match:
        return GitInfo(org=https_match.group(1), repo=https_match.group(2))

    return GitInfo()


def resolve_git_remote(cwd: str, cache: dict[str, GitInfo]) -> GitInfo:
    """Run ``git remote get-url origin`` in *cwd* and parse the result.

    Results are cached per path so repeated calls for the same directory avoid
    extra subprocess invocations.
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
