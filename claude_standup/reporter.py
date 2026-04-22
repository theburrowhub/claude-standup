"""Reporter module — generates standup reports from classified activities.

Two modes:
- Template: instant local report (generate_template_report)
- LLM: one `claude -p` call to polish the report (generate_llm_report)
"""

from __future__ import annotations

import itertools
import operator
import shutil
import subprocess

from claude_standup.models import Activity


# ---------------------------------------------------------------------------
# Template reporter — local, instant report (no LLM)
# ---------------------------------------------------------------------------

def _format_activity_line(act: Activity) -> str:
    """Format a single activity into a human-readable bullet line."""
    org_repo = ""
    if act.git_org and act.git_repo:
        org_repo = f"({act.git_org}/{act.git_repo})"
    elif act.git_org:
        org_repo = f"({act.git_org})"
    time_part = f" ~{act.time_spent_minutes}min" if act.time_spent_minutes else ""
    return f"[{act.classification}]{org_repo} {act.summary}{time_part}"


def _template_markdown(
    activities: list[Activity],
    pending_count: int,
    lang: str,
) -> str:
    """Render activities as a Markdown template report."""
    sections: list[str] = []

    sorted_acts = sorted(activities, key=operator.attrgetter("day"))
    for day, group in itertools.groupby(sorted_acts, key=operator.attrgetter("day")):
        lines: list[str] = [f"## {day}", ""]
        lines.append("### Done" if lang == "en" else "### Done")
        for act in group:
            lines.append(f"- {_format_activity_line(act)}")
        sections.append("\n".join(lines))

    body = "\n\n".join(sections)

    if pending_count > 0:
        if lang == "en":
            pending_section = (
                f"### Pending classification\n"
                f"- {pending_count} sessions pending classification"
            )
        else:
            pending_section = (
                f"### Pendiente de clasificación\n"
                f"- {pending_count} sesiones pendientes de clasificación"
            )
        if body:
            body = f"{body}\n\n{pending_section}"
        else:
            body = pending_section

    return body


def _template_slack(
    activities: list[Activity],
    pending_count: int,
    lang: str,
) -> str:
    """Render activities as a Slack-formatted template report."""
    sections: list[str] = []

    sorted_acts = sorted(activities, key=operator.attrgetter("day"))
    for day, group in itertools.groupby(sorted_acts, key=operator.attrgetter("day")):
        lines: list[str] = [f"*{day}*", ""]
        lines.append("*Done*" if lang == "en" else "*Done*")
        for act in group:
            lines.append(f"\u2022 {_format_activity_line(act)}")
        sections.append("\n".join(lines))

    body = "\n\n".join(sections)

    if pending_count > 0:
        if lang == "en":
            pending_section = (
                f"*Pending classification*\n"
                f"\u2022 {pending_count} sessions pending classification"
            )
        else:
            pending_section = (
                f"*Pendiente de clasificación*\n"
                f"\u2022 {pending_count} sesiones pendientes de clasificación"
            )
        if body:
            body = f"{body}\n\n{pending_section}"
        else:
            body = pending_section

    return body


def generate_template_report(
    activities: list[Activity],
    output_format: str = "markdown",
    pending_count: int = 0,
    lang: str = "es",
) -> str:
    """Generate an instant standup report — no LLM calls, pure template."""
    if not activities and pending_count <= 0:
        if lang == "en":
            return "No activity found for the requested period."
        return "No se encontró actividad para el período solicitado."

    if output_format == "slack":
        return _template_slack(activities, pending_count, lang)
    return _template_markdown(activities, pending_count, lang)


# ---------------------------------------------------------------------------
# LLM-polished report — one `claude -p` call
# ---------------------------------------------------------------------------

_LLM_REPORT_PROMPT = """\
You are generating a daily standup report for a software developer.

Below is raw classified activity data from their Claude Code sessions. Your job:

1. Write a clean, concise activity summary. One section: what was done, grouped by project.
2. IGNORE noise: subagent instructions ("You are implementing...", "You are reviewing..."), \
   internal tool output ("List project directory", "Check git status"), JSON artifacts, and \
   anything that looks like Claude Code internal machinery rather than actual developer work
3. MERGE only truly redundant entries (exact same task mentioned multiple times). \
   When there are multiple distinct deliverables (e.g. several PRs, several services, \
   several bugs fixed), LIST EACH ONE by name. Never collapse distinct items into \
   a single vague summary. Bad: "Created new inference services". \
   Good: "Created kling-4k-t2v and kling-4k-i2v inference services". \
   Extract specific names from the context data (tool descriptions, assistant messages).
4. Include org/repo when available. Do NOT include time estimates or durations.
5. Write in {lang}
6. Use {format} formatting
7. Do NOT include "Next steps", "Blockers", or "TODO" sections. Only summarize what was done.
{context_section}
Raw activity data:
{activities}
"""


def generate_llm_report(
    activities: list[Activity],
    output_format: str = "markdown",
    lang: str = "es",
    context_activities: list[Activity] | None = None,
) -> str:
    """Generate a polished standup report using one claude -p call.

    *context_activities*: activities from previous days (e.g. yesterday) so the LLM
    knows what's already done and doesn't suggest completed work as "Next".
    """
    if not activities:
        if lang == "en":
            return "No activity found for the requested period."
        return "No se encontró actividad para el período solicitado."

    # Build raw data for the LLM — include all prompts so it can see completion signals
    lines = []
    for act in activities:
        org_repo = f"({act.git_org}/{act.git_repo})" if act.git_org and act.git_repo else ""
        lines.append(f"- [{act.day}] [{act.classification}]{org_repo} {act.summary}")
        # Include raw prompts (truncated) so LLM can see the full story
        for prompt in act.raw_prompts[:15]:  # max 15 context lines per activity
            truncated = prompt.strip().split("\n")[0][:120]
            if len(truncated) > 15:
                lines.append(f"    > {truncated}")

    # Build context section from previous days
    context_section = ""
    if context_activities:
        ctx_lines = []
        for act in context_activities:
            org_repo = f"({act.git_org}/{act.git_repo})" if act.git_org and act.git_repo else ""
            ctx_lines.append(f"- [{act.day}] [{act.classification}]{org_repo} {act.summary}")
        context_section = (
            "\nContext — activities from previous days (already completed, do NOT suggest as Next):\n"
            + "\n".join(ctx_lines) + "\n\n"
        )

    lang_name = "Spanish" if lang == "es" else "English"
    fmt_name = "Slack (*bold*, • bullets)" if output_format == "slack" else "Markdown (## headers, - bullets)"

    prompt = _LLM_REPORT_PROMPT.format(
        lang=lang_name,
        format=fmt_name,
        activities="\n".join(lines),
        context_section=context_section,
    )

    claude_path = shutil.which("claude")
    if not claude_path:
        # Fallback to template if claude not available
        return generate_template_report(activities, output_format=output_format, lang=lang)

    try:
        result = subprocess.run(
            [claude_path, "-p", prompt, "--output-format", "text", "--no-session-persistence"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Fallback to template on any failure
    return generate_template_report(activities, output_format=output_format, lang=lang)
