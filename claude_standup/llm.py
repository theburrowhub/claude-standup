"""LLM backend: calls Claude via the Claude Code CLI in headless mode.

This avoids the need for API keys — Claude Code is already authenticated.
Falls back to the anthropic SDK if ANTHROPIC_API_KEY is set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import anthropic

from claude_standup.models import Activity, LogEntry


def get_llm_backend() -> "LLMBackend":
    """Return the best available LLM backend.

    Priority:
    1. ANTHROPIC_API_KEY env var → use anthropic SDK directly
    2. claude CLI available → use headless mode
    3. Raise RuntimeError
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return AnthropicSDKBackend(api_key)

    claude_path = shutil.which("claude")
    if claude_path:
        return ClaudeCLIBackend(claude_path)

    raise RuntimeError(
        "No LLM backend available.\n"
        "Either set ANTHROPIC_API_KEY or install Claude Code (https://claude.ai/claude-code)."
    )


class LLMBackend:
    """Abstract base for LLM backends."""

    def query(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        raise NotImplementedError


class ClaudeCLIBackend(LLMBackend):
    """Uses `claude -p` (headless mode) as the LLM backend."""

    def __init__(self, claude_path: str):
        self.claude_path = claude_path

    def query(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        result = subprocess.run(
            [self.claude_path, "-p", full_prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude CLI failed (exit {result.returncode}): {result.stderr[:500]}")
        return result.stdout.strip()


class AnthropicSDKBackend(LLMBackend):
    """Uses the anthropic Python SDK directly."""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def query(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        message = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in message.content:
            if block.type == "text":
                return block.text
        return ""
