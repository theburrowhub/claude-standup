"""Classify sessions using local heuristics — no LLM calls needed.

Classification is based on:
1. User prompt text (keywords)
2. Tool names used (Edit/Write = coding, gh pr = review, etc.)
3. Tool descriptions (human-readable summaries already written by Claude)
4. Git branch name patterns (feat/, fix/, refactor/)
"""

from __future__ import annotations

import re
from itertools import groupby
from operator import attrgetter

from claude_standup.models import Activity, LogEntry


# ---------------------------------------------------------------------------
# Keyword patterns for classification
# ---------------------------------------------------------------------------

_REVIEW_SIGNALS = re.compile(
    r"(?i)(review|revis[ae]|pr\s*#?\d|pull\s*request|approve|request.changes|lgtm)",
)
_BUGFIX_SIGNALS = re.compile(
    r"(?i)(fix|bug|error|crash|broken|fail|issue|hotfix|patch|arregl)",
)
_REFACTOR_SIGNALS = re.compile(
    r"(?i)(refactor|restructur|reorganiz|clean.?up|rename|extract|split|move)",
)
_DEBUG_SIGNALS = re.compile(
    r"(?i)(debug|investigat|diagnos|traceback|log|¿por\s*qué|why\s+is|what.s\s+wrong)",
)
_EXPLORE_SIGNALS = re.compile(
    r"(?i)(explor|research|understand|how\s+does|cómo\s+funciona|analiz|check|inspect|list|find)",
)

_BRANCH_PATTERNS = {
    "FEATURE": re.compile(r"(?i)^(feat|feature)/"),
    "BUGFIX": re.compile(r"(?i)^(fix|hotfix|bugfix)/"),
    "REFACTOR": re.compile(r"(?i)^(refactor|chore)/"),
}

# Tools that strongly signal a category
_REVIEW_TOOLS = {"gh pr view", "gh pr diff", "gh pr review", "gh pr comment", "gh pr approve"}


def classify_session_local(entries: list[LogEntry]) -> list[Activity]:
    """Classify a session's entries into activities using local heuristics.

    Groups entries into logical activities and classifies each one.
    No LLM calls — purely local, instant.
    """
    if not entries:
        return []

    # Group entries by day — a session spanning midnight produces multiple activities
    from itertools import groupby
    from operator import attrgetter

    sorted_entries = sorted(entries, key=lambda e: e.timestamp[:10])
    activities = []

    for day, day_entries_iter in groupby(sorted_entries, key=lambda e: e.timestamp[:10]):
        day_entries = list(day_entries_iter)

        user_prompts = [e for e in day_entries if e.entry_type == "user_prompt"]
        tool_uses = [e for e in day_entries if e.entry_type == "tool_use"]
        assistant_texts = [e for e in day_entries if e.entry_type == "assistant_text"]

        if not user_prompts and not tool_uses:
            continue

        all_text = " ".join(e.content for e in user_prompts)
        all_tool_descriptions = " ".join(
            desc for e in tool_uses for desc in (e.content.split("\n") if e.content else [])
        )
        all_tool_names = [name for e in tool_uses for name in e.tool_names]
        tool_name_str = " ".join(all_tool_names)
        all_signals = f"{all_text} {all_tool_descriptions} {tool_name_str}"

        branch = next((e.git_branch for e in day_entries if e.git_branch), None)
        branch_class = _classify_from_branch(branch) if branch else None

        classification = _classify_from_signals(all_signals, all_tool_names, branch_class)
        summary = _build_summary(user_prompts, tool_uses, assistant_texts)

        timestamps = sorted(e.timestamp for e in day_entries if e.timestamp)
        time_minutes = _estimate_minutes(timestamps)

        # Collect context for the LLM reporter: user prompts + key assistant/tool signals
        context_lines = [e.content for e in user_prompts]
        for e in assistant_texts:
            first_line = e.content.split("\n")[0].strip()
            if len(first_line) > 20:
                context_lines.append(f"[assistant] {first_line}")
        for e in tool_uses:
            for desc in (e.content.split("\n") if e.content else []):
                if len(desc.strip()) > 10:
                    context_lines.append(f"[tool] {desc.strip()}")

        activities.append(Activity(
            session_id=entries[0].session_id,
            day=day,
            project=entries[0].project,
            git_org=None,
            git_repo=None,
            classification=classification,
            summary=summary,
            files_mentioned=_extract_files(all_signals),
            technologies=_extract_technologies(all_signals),
            time_spent_minutes=time_minutes,
            raw_prompts=context_lines,
        ))

    return activities


def _classify_from_branch(branch: str) -> str | None:
    """Classify from git branch name pattern."""
    for category, pattern in _BRANCH_PATTERNS.items():
        if pattern.search(branch):
            return category
    return None


def _classify_from_signals(text: str, tool_names: list[str], branch_hint: str | None) -> str:
    """Classify from all available text signals + tool names."""
    # REVIEW: only if there are strong review signals (PR review/approve actions, not just "fetch")
    review_actions = re.compile(r"(?i)(gh\s+pr\s+(review|approve|comment)|review\s+pr|revis[ae]\s+(pr|pull)|pull\s*request\s+review|request.changes|lgtm)")
    if review_actions.search(text):
        return "REVIEW"

    if _BUGFIX_SIGNALS.search(text):
        return branch_hint if branch_hint else "BUGFIX"

    if _REFACTOR_SIGNALS.search(text):
        return "REFACTOR"

    if _DEBUG_SIGNALS.search(text):
        return "DEBUGGING"

    # If there are Edit/Write tools, it's likely a feature
    has_edit = any(t in ("Edit", "Write", "NotebookEdit") for t in tool_names)
    if has_edit:
        return branch_hint if branch_hint else "FEATURE"

    if _EXPLORE_SIGNALS.search(text):
        return "EXPLORATION"

    return branch_hint or "OTHER"


def _build_summary(
    user_prompts: list[LogEntry],
    tool_uses: list[LogEntry],
    assistant_texts: list[LogEntry],
) -> str:
    """Build a one-line summary from the best available source."""
    # Best: first user prompt (usually the task description)
    if user_prompts:
        first = user_prompts[0].content.strip()
        # Clean up XML tags and noise
        first = re.sub(r"<[^>]+>.*?</[^>]+>", "", first, flags=re.DOTALL).strip()
        # Take only the first line/sentence
        first = first.split("\n")[0].strip()
        if len(first) > 10:
            return first[:150].rstrip()

    # Fallback: first tool description
    for e in tool_uses:
        if e.content and len(e.content) > 10:
            first_desc = e.content.split("\n")[0].strip()
            return first_desc[:150].rstrip()

    # Fallback: first assistant text
    if assistant_texts:
        first_text = assistant_texts[0].content.split("\n")[0].strip()
        return first_text[:150].rstrip()

    return "Activity"


def _estimate_minutes(timestamps: list[str]) -> int:
    """Estimate time spent from first to last timestamp."""
    if len(timestamps) < 2:
        return 0
    from datetime import datetime
    try:
        first = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
        last = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
        delta = (last - first).total_seconds() / 60
        return max(0, int(delta))
    except (ValueError, IndexError):
        return 0


def _extract_files(text: str) -> list[str]:
    """Extract file paths mentioned in the text."""
    patterns = re.findall(r"[\w./\-]+\.\w{1,10}", text)
    # Filter to likely file paths
    extensions = {".py", ".ts", ".js", ".go", ".rs", ".yaml", ".yml", ".json", ".toml", ".sh", ".sql", ".html", ".css", ".md"}
    return list({p for p in patterns if any(p.endswith(ext) for ext in extensions)})[:10]


def _extract_technologies(text: str) -> list[str]:
    """Detect technologies mentioned."""
    techs = []
    tech_patterns = {
        "Python": r"(?i)\bpython\b|\.py\b",
        "TypeScript": r"(?i)\btypescript\b|\.ts\b",
        "JavaScript": r"(?i)\bjavascript\b|\.js\b",
        "Go": r"(?i)\bgo\b|\.go\b|golang",
        "Rust": r"(?i)\brust\b|\.rs\b|cargo",
        "Docker": r"(?i)\bdocker\b|dockerfile",
        "Kubernetes": r"(?i)\bk8s\b|kubernetes|kubectl|helm",
        "React": r"(?i)\breact\b|\.tsx\b|\.jsx\b",
        "FastAPI": r"(?i)\bfastapi\b",
        "Django": r"(?i)\bdjango\b",
        "PostgreSQL": r"(?i)\bpostgres\b|postgresql",
        "Redis": r"(?i)\bredis\b",
        "GitHub": r"(?i)\bgh\s+pr\b|github",
    }
    for tech, pattern in tech_patterns.items():
        if re.search(pattern, text):
            techs.append(tech)
    return techs
