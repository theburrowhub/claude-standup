"""Tests for the template reporter — local, instant report generation without LLM."""

from __future__ import annotations

import pytest

from claude_standup.models import Activity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _activities() -> list[Activity]:
    return [
        Activity(
            session_id="s1",
            day="2026-04-21",
            project="my-app",
            git_org="acme",
            git_repo="my-app",
            classification="FEATURE",
            summary="Implemented login with OAuth2",
            time_spent_minutes=45,
        ),
        Activity(
            session_id="s1",
            day="2026-04-21",
            project="my-app",
            git_org="acme",
            git_repo="my-app",
            classification="BUGFIX",
            summary="Fixed session expiration",
            time_spent_minutes=20,
        ),
        Activity(
            session_id="manual",
            day="2026-04-21",
            project="",
            git_org=None,
            git_repo=None,
            classification="MEETING",
            summary="Sprint planning with backend team",
        ),
    ]


# ---------------------------------------------------------------------------
# TestMarkdownTemplate
# ---------------------------------------------------------------------------

class TestMarkdownTemplate:
    """Verify markdown template output."""

    def test_basic_format(self, _activities):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report(_activities, output_format="markdown")

        # Day heading
        assert "## 2026-04-21" in result
        # Done section
        assert "### Done" in result
        # Classification tags
        assert "[FEATURE]" in result
        assert "[MEETING]" in result
        # Time annotation
        assert "~45min" in result
        # Org/repo present for the first activity
        assert "(acme/my-app)" in result

    def test_no_pending_when_zero(self, _activities):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report(_activities, output_format="markdown", pending_count=0)

        assert "Pending" not in result

    def test_pending_warning(self, _activities):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report(_activities, output_format="markdown", pending_count=3)

        assert "### Pending" in result or "### Pendiente" in result
        assert "3" in result
        # The word "sessions" or "sesiones" should appear
        assert "session" in result.lower() or "sesion" in result.lower() or "sesión" in result.lower()

    def test_empty_activities_no_pending(self):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report([], output_format="markdown", pending_count=0, lang="es")
        assert "No se encontr" in result

        result_en = generate_template_report([], output_format="markdown", pending_count=0, lang="en")
        assert "No activity found" in result_en

    def test_empty_activities_with_pending(self):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report([], output_format="markdown", pending_count=5)

        # Should NOT show "no activity" message
        assert "No se encontr" not in result
        assert "No activity" not in result
        # Should show pending section
        assert "5" in result
        assert "Pending" in result or "Pendiente" in result

    def test_multi_day(self, _activities):
        from claude_standup.reporter import generate_template_report

        extra = Activity(
            session_id="s2",
            day="2026-04-20",
            project="other",
            git_org="acme",
            git_repo="other",
            classification="REFACTOR",
            summary="Cleaned up utils module",
            time_spent_minutes=30,
        )
        activities = [extra] + list(_activities)
        result = generate_template_report(activities, output_format="markdown")

        assert "## 2026-04-20" in result
        assert "## 2026-04-21" in result
        # 2026-04-20 should come before 2026-04-21 (sorted)
        pos_20 = result.index("## 2026-04-20")
        pos_21 = result.index("## 2026-04-21")
        assert pos_20 < pos_21


# ---------------------------------------------------------------------------
# TestSlackTemplate
# ---------------------------------------------------------------------------

class TestSlackTemplate:
    """Verify slack template output."""

    def test_basic_format(self, _activities):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report(_activities, output_format="slack")

        # Day heading in Slack bold
        assert "*2026-04-21*" in result
        # Done section in Slack bold
        assert "*Done*" in result or "*Hecho*" in result
        # Bullet character
        assert "\u2022" in result
        # Classification tag
        assert "[FEATURE]" in result

    def test_pending_warning(self, _activities):
        from claude_standup.reporter import generate_template_report

        result = generate_template_report(_activities, output_format="slack", pending_count=2)

        assert "*Pending" in result or "*Pendiente" in result
        assert "2" in result
        assert "session" in result.lower() or "sesion" in result.lower() or "sesión" in result.lower()
