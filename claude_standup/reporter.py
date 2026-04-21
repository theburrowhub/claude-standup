"""Reporter module — generates standup reports from classified activities."""

from __future__ import annotations

from claude_standup.llm import LLMBackend
from claude_standup.models import Activity

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORTER_SYSTEM_PROMPT = """\
You are a concise standup-report generator.  You receive a list of classified \
developer activities and produce a professional daily standup report.

Rules:
1. Group items by day (YYYY-MM-DD).
2. For each day, produce three sections: **Yesterday**, **Today**, and **Blockers**.
3. Infer *Today* items from work that appears incomplete or naturally continues.
4. Identify *Blockers* from activities classified as failures, confusion, or \
   repeated debugging.  If there are none, write "None identified".
5. Each activity line must include the GitHub org/repo when available \
   (e.g. acme-corp/my-app) and an approximate duration (e.g. ~45min).
6. Be concise — one line per activity, no filler text.
7. Include manual entries (meetings, support, etc.) as-is.
8. Respect the requested output language and formatting style.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report(
    backend: LLMBackend,
    activities: list[Activity],
    lang: str = "es",
    output_format: str = "markdown",
) -> str:
    """Generate a standup report from *activities* using an LLM backend."""
    if not activities:
        if lang == "en":
            return "No activity found for the requested period."
        return "No se encontró actividad para el período solicitado."

    user_prompt = _build_report_prompt(activities, lang, output_format)
    text = backend.query(REPORTER_SYSTEM_PROMPT, user_prompt, max_tokens=2048)

    if output_format == "slack":
        return format_as_slack(text)
    return format_as_markdown(text)


def _build_report_prompt(
    activities: list[Activity],
    lang: str,
    output_format: str,
) -> str:
    """Build the user-message prompt sent to the model."""
    lines: list[str] = []

    for act in activities:
        org_repo = ""
        if act.git_org and act.git_repo:
            org_repo = f"({act.git_org}/{act.git_repo})"
        elif act.git_org:
            org_repo = f"({act.git_org})"
        elif act.git_repo:
            org_repo = f"({act.git_repo})"

        time_part = f" ~{act.time_spent_minutes}min" if act.time_spent_minutes else ""
        lines.append(
            f"- [{act.day}] [{act.classification}]{org_repo} {act.summary}{time_part}"
        )

    activities_block = "\n".join(lines)

    lang_name = "English" if lang == "en" else "Spanish"
    lang_instruction = f"Write the report in {lang_name}."

    if output_format == "slack":
        fmt_instruction = (
            "Use Slack formatting: *bold* for headings, \u2022 (bullet) for list items. "
            "Do NOT use Markdown headings (## or ###)."
        )
    else:
        fmt_instruction = (
            "Use Markdown formatting: ## for day headings, ### for section headings, "
            "- for list items."
        )

    return (
        f"Here are the classified developer activities:\n\n"
        f"{activities_block}\n\n"
        f"{lang_instruction}\n"
        f"Output format: {output_format}. {fmt_instruction}"
    )


# ---------------------------------------------------------------------------
# Format helpers (passthrough — Claude generates the correct format)
# ---------------------------------------------------------------------------

def format_as_markdown(text: str) -> str:
    """Return *text* unchanged (Claude already generates Markdown)."""
    return text


def format_as_slack(text: str) -> str:
    """Return *text* unchanged (Claude already generates Slack format)."""
    return text
