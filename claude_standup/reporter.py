"""Reporter module — generates standup reports from classified activities.

All report generation is local (template-based). No LLM calls.
"""

from __future__ import annotations

import itertools
import operator

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
