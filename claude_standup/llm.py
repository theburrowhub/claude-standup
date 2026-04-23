"""LLM backend: calls Claude via the Claude Code CLI in headless mode.

Uses `claude -p` which is cross-platform and already authenticated.
No API keys, no keychain, no SDK — just the CLI.
"""

from __future__ import annotations

import shutil
import subprocess


def get_llm_backend() -> "ClaudeCLIBackend":
    """Return a Claude CLI backend. Raises RuntimeError if claude is not installed."""
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError(
            "Claude Code CLI not found.\n"
            "Install it from https://claude.ai/claude-code"
        )
    return ClaudeCLIBackend(claude_path)


class ClaudeCLIBackend:
    """Uses `claude -p` (headless mode) as the LLM backend."""

    def __init__(self, claude_path: str):
        self.claude_path = claude_path

    def query(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        result = subprocess.run(
            [
                self.claude_path, "-p", full_prompt,
                "--output-format", "text",
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI failed (exit {result.returncode}): {result.stderr[:500]}")
        return result.stdout.strip()
