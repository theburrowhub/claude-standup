"""Classify Claude Code log entries into structured development activities.

Uses an LLM backend (Claude CLI or Anthropic SDK) to analyze user prompts
from coding sessions and group them into meaningful activities.
"""

from __future__ import annotations

import json
import logging

from claude_standup.llm import LLMBackend
from claude_standup.models import Activity, LogEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLASSIFICATION_SYSTEM_PROMPT = """\
You are a developer-activity classifier. You will receive a numbered list of \
user prompts from a Claude Code coding session, each with a timestamp.

Your job:
1. Classify each prompt into a development activity.
2. Group related prompts that belong to the same logical activity.
3. Extract mentioned files and technologies.
4. Estimate time spent on each activity based on timestamp gaps.

Categories (pick exactly one per activity):
  FEATURE   — new feature implementation
  BUGFIX    — fixing a bug or defect
  REFACTOR  — restructuring existing code without changing behaviour
  DEBUGGING — investigating or diagnosing issues
  EXPLORATION — exploring, researching, or learning
  REVIEW    — code review or PR review
  SUPPORT   — helping others, answering questions
  MEETING   — meeting-related activity
  OTHER     — anything that does not fit above

Rules:
- Ignore trivial prompts that carry no meaningful intent (e.g. "yes", "ok", \
"continue", "y", "go ahead", "sure").
- Each non-trivial prompt must appear in exactly one activity's prompt_indices.
- prompt_indices are zero-based and refer to the position in the input list.

Return ONLY valid JSON with this exact structure (no markdown fences):
{"activities": [\
{"classification": "CATEGORY", \
"summary": "One-sentence summary of the activity", \
"files_mentioned": ["file1.py", "file2.ts"], \
"technologies": ["Python", "React"], \
"time_spent_minutes": 25, \
"prompt_indices": [0, 1, 2]}\
]}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_session(
    backend: LLMBackend,
    entries: list[LogEntry],
    git_org: str | None = None,
    git_repo: str | None = None,
) -> list[Activity]:
    """Classify a list of log entries into structured activities."""
    user_entries = [e for e in entries if e.entry_type == "user_prompt"]
    if not user_entries:
        return []

    prompt = _build_classification_prompt(user_entries)

    try:
        response_text = backend.query(CLASSIFICATION_SYSTEM_PROMPT, prompt)
    except Exception:
        logger.warning("Classification API call failed.", exc_info=True)
        return []

    if not response_text:
        return []

    project = user_entries[0].project
    return _parse_classification_response(
        response_text, user_entries, project, git_org, git_repo
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_classification_prompt(entries: list[LogEntry]) -> str:
    """Format log entries into a numbered prompt list for the classifier."""
    lines: list[str] = []
    for idx, entry in enumerate(entries):
        lines.append(f"[{idx}] ({entry.timestamp}) {entry.content}")
    return "\n".join(lines)


def _parse_classification_response(
    response_text: str,
    entries: list[LogEntry],
    project: str,
    git_org: str | None,
    git_repo: str | None,
) -> list[Activity]:
    """Parse the JSON response from the classifier into Activity objects."""
    # Strip markdown fences if present
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON in classification response.")
        return []

    if "activities" not in data:
        logger.warning("Missing 'activities' key in classification response.")
        return []

    activities: list[Activity] = []
    for item in data["activities"]:
        indices = item.get("prompt_indices", [])
        valid_indices = [i for i in indices if 0 <= i < len(entries)]
        raw_prompts = [entries[i].content for i in valid_indices]

        # Use the first referenced entry for session_id and day
        ref_entry = entries[valid_indices[0]] if valid_indices else (entries[0] if entries else None)
        if ref_entry is None:
            continue

        activities.append(
            Activity(
                session_id=ref_entry.session_id,
                day=ref_entry.timestamp[:10],
                project=project,
                git_org=git_org,
                git_repo=git_repo,
                classification=item.get("classification", "OTHER"),
                summary=item.get("summary", ""),
                files_mentioned=item.get("files_mentioned", []),
                technologies=item.get("technologies", []),
                time_spent_minutes=item.get("time_spent_minutes", 0),
                raw_prompts=raw_prompts,
            )
        )

    return activities
