"""Classify Claude Code log entries into structured development activities.

Uses the Anthropic API (Claude) to analyze user prompts from coding sessions
and group them into meaningful activities with classifications, summaries,
and metadata.
"""

from __future__ import annotations

import json
import logging
import time

import anthropic

from claude_standup.models import Activity, LogEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-6-20250414"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0

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
    client: anthropic.Anthropic,
    entries: list[LogEntry],
    git_org: str | None = None,
    git_repo: str | None = None,
) -> list[Activity]:
    """Classify a list of log entries into structured activities.

    Parameters
    ----------
    client:
        An initialised Anthropic API client.
    entries:
        Log entries to classify (only ``user_prompt`` entries are used).
    git_org:
        GitHub organisation name to attach to resulting activities.
    git_repo:
        GitHub repository name to attach to resulting activities.

    Returns
    -------
    list[Activity]
        Classified activities.  Returns ``[]`` when *entries* is empty or the
        API call fails gracefully.
    """
    # Filter to user_prompt entries only
    user_entries = [e for e in entries if e.entry_type == "user_prompt"]
    if not user_entries:
        return []

    prompt = _build_classification_prompt(user_entries)
    response_text = _call_api_with_retry(client, prompt)
    if response_text is None:
        return []

    # Derive project and day from the first entry
    project = user_entries[0].project
    return _parse_classification_response(
        response_text, user_entries, project, git_org, git_repo
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_classification_prompt(entries: list[LogEntry]) -> str:
    """Format log entries into a numbered prompt list for the classifier.

    Each line is formatted as:
        [index] (timestamp) content
    """
    lines: list[str] = []
    for idx, entry in enumerate(entries):
        lines.append(f"[{idx}] ({entry.timestamp}) {entry.content}")
    return "\n".join(lines)


def _call_api_with_retry(client: anthropic.Anthropic, prompt: str) -> str | None:
    """Call the Anthropic API with exponential-backoff retry on rate limits.

    Parameters
    ----------
    client:
        An initialised Anthropic API client.
    prompt:
        The user-facing prompt to send.

    Returns
    -------
    str | None
        The text content of the response, or ``None`` if all retries are
        exhausted.

    Raises
    ------
    anthropic.APIError
        Re-raised immediately for non-rate-limit API errors.
    """
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=CLASSIFICATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            # Extract text from the first text block
            for block in message.content:
                if block.type == "text":
                    return block.text
            return None  # pragma: no cover
        except anthropic.RateLimitError:
            if attempt == MAX_RETRIES - 1:
                logger.warning(
                    "Rate-limited after %d retries; giving up.", MAX_RETRIES
                )
                return None
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.info(
                "Rate-limited (attempt %d/%d). Retrying in %.1fs…",
                attempt + 1,
                MAX_RETRIES,
                delay,
            )
            time.sleep(delay)
    return None  # pragma: no cover — unreachable


def _parse_classification_response(
    response_text: str,
    entries: list[LogEntry],
    project: str,
    git_org: str | None,
    git_repo: str | None,
) -> list[Activity]:
    """Parse the JSON response from the classifier into Activity objects.

    Handles malformed JSON and missing keys gracefully by returning ``[]``.
    """
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON in classification response.")
        return []

    if "activities" not in data:
        logger.warning("Missing 'activities' key in classification response.")
        return []

    # Derive the day from the first entry's timestamp (YYYY-MM-DD)
    day = entries[0].timestamp[:10] if entries else ""

    activities: list[Activity] = []
    for item in data["activities"]:
        # Gather raw prompts from indices
        indices = item.get("prompt_indices", [])
        raw_prompts = [
            entries[i].content
            for i in indices
            if 0 <= i < len(entries)
        ]

        activities.append(
            Activity(
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
            )
        )

    return activities
