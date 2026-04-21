"""End-to-end integration tests for the full claude-standup pipeline.

These tests exercise the full flow: discover files -> parse JSONL ->
classify with mocked Anthropic client -> store in SQLite -> query ->
generate report with mocked Anthropic client.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_standup.cache import CacheDB
from claude_standup.classifier import classify_session
from claude_standup.models import Activity, FileInfo
from claude_standup.parser import discover_jsonl_files, parse_jsonl_file, derive_project_name
from claude_standup.reporter import generate_report

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _make_mock_backend(response_text: str) -> MagicMock:
    """Return a mock LLMBackend that returns *response_text*."""
    backend = MagicMock()
    backend.query.return_value = response_text
    return backend


# ---------------------------------------------------------------------------
# TestFullPipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Integration tests covering discover -> parse -> classify -> store -> report."""

    def test_parse_classify_report(self, tmp_path: Path) -> None:
        """Full pipeline: discover, parse, classify (mock), store, query, report (mock)."""
        # 1. Create project directory structure mimicking Claude Code logs
        project_dir = tmp_path / "-Users-dev-workspace-my-app"
        project_dir.mkdir()
        dest = project_dir / "sess-001.jsonl"
        shutil.copy(FIXTURES_DIR / "valid_session.jsonl", dest)

        # 2. Discover files
        files = discover_jsonl_files(tmp_path)
        assert len(files) == 1
        assert files[0].path.endswith("sess-001.jsonl")

        # 3. Parse the JSONL file
        project_name = derive_project_name(project_dir.name)
        entries = parse_jsonl_file(files[0].path, project_name)
        assert len(entries) > 0  # valid_session.jsonl has user + assistant entries

        # 4. Classify with mocked backend
        classifier_backend = _make_mock_backend(json.dumps({"activities": [
            {
                "classification": "FEATURE",
                "summary": "Implemented login feature with OAuth2",
                "files_mentioned": ["auth.py"],
                "technologies": ["Python", "OAuth2"],
                "time_spent_minutes": 30,
                "prompt_indices": [0, 1],
            }
        ]}))
        activities = classify_session(classifier_backend, entries, git_org="acme", git_repo="my-app")
        assert len(activities) == 1
        assert activities[0].classification == "FEATURE"

        # 5. Store in CacheDB (in-memory)
        db = CacheDB(":memory:")
        db.store_activities(activities)

        # 6. Query back
        results = db.query_activities("2026-04-21", "2026-04-21")
        assert len(results) == 1
        assert results[0].classification == "FEATURE"
        assert "login" in results[0].summary.lower()

        # 7. Generate report with mocked reporter client
        report_text = (
            "## 2026-04-21\n\n"
            "### Yesterday\n"
            "- Implemented login feature with OAuth2 (acme/my-app) ~30min\n\n"
            "### Today\n"
            "- Continue login integration testing\n\n"
            "### Blockers\n"
            "- None identified"
        )
        reporter_backend = _make_mock_backend(report_text)
        report = generate_report(reporter_backend, results, lang="es", output_format="markdown")

        assert "2026-04-21" in report
        assert "login" in report.lower()

        db.close()

    def test_pipeline_with_manual_entries(self) -> None:
        """Store a classified activity and a manual MEETING entry, verify both are returned."""
        db = CacheDB(":memory:")

        # Store a FEATURE activity from a parsed session
        feature = Activity(
            session_id="sess-001",
            day="2026-04-21",
            project="my-app",
            git_org="acme",
            git_repo="my-app",
            classification="FEATURE",
            summary="Implemented login feature",
            files_mentioned=["auth.py"],
            technologies=["Python"],
            time_spent_minutes=30,
            raw_prompts=["Implement login"],
        )
        db.store_activities([feature])

        # Store a manual MEETING entry
        db.store_manual_entry(
            summary="Sprint planning meeting",
            classification="MEETING",
            git_org=None,
            git_repo=None,
        )

        # Query all activities for a wide date range
        results = db.query_activities("2000-01-01", "2099-12-31")
        assert len(results) == 2

        classifications = {r.classification for r in results}
        assert "FEATURE" in classifications
        assert "MEETING" in classifications

        db.close()

    def test_pipeline_org_filter(self) -> None:
        """Store activities with different git_orgs, query with org filter."""
        db = CacheDB(":memory:")

        acme_activity = Activity(
            session_id="sess-001",
            day="2026-04-21",
            project="app-a",
            git_org="acme",
            git_repo="app-a",
            classification="FEATURE",
            summary="Acme feature work",
        )
        beta_activity = Activity(
            session_id="sess-002",
            day="2026-04-21",
            project="app-b",
            git_org="beta",
            git_repo="app-b",
            classification="BUGFIX",
            summary="Beta bugfix work",
        )
        db.store_activities([acme_activity, beta_activity])

        # Filter to only "acme" org
        results = db.query_activities("2026-04-21", "2026-04-21", orgs=["acme"])
        assert len(results) == 1
        assert results[0].git_org == "acme"
        assert results[0].summary == "Acme feature work"

        db.close()

    def test_incremental_processing(self, tmp_path: Path) -> None:
        """Discover files, mark processed, verify unprocessed count drops to 0."""
        # Set up a project directory with one JSONL file
        project_dir = tmp_path / "-Users-dev-workspace-my-app"
        project_dir.mkdir()
        shutil.copy(FIXTURES_DIR / "valid_session.jsonl", project_dir / "sess-001.jsonl")

        # Discover
        files = discover_jsonl_files(tmp_path)
        assert len(files) == 1

        db = CacheDB(":memory:")

        # All files are unprocessed initially
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 1

        # Mark the file as processed
        db.mark_file_processed(files[0].path, files[0].mtime)

        # Now none should be unprocessed
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 0

        db.close()

    def test_reprocess_flag(self, tmp_path: Path) -> None:
        """Mark a file processed, clear tracking, verify it appears unprocessed again."""
        # Set up a project directory with one JSONL file
        project_dir = tmp_path / "-Users-dev-workspace-my-app"
        project_dir.mkdir()
        shutil.copy(FIXTURES_DIR / "valid_session.jsonl", project_dir / "sess-001.jsonl")

        files = discover_jsonl_files(tmp_path)
        assert len(files) == 1

        db = CacheDB(":memory:")

        # Mark processed
        db.mark_file_processed(files[0].path, files[0].mtime)
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 0

        # Clear file tracking (simulates --reprocess flag)
        db.clear_file_tracking()

        # File should appear unprocessed again
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 1

        db.close()

    def test_empty_logs_directory(self, tmp_path: Path) -> None:
        """Empty directory returns no files; empty activities returns 'No se encontro actividad'."""
        # Discover on an empty directory
        files = discover_jsonl_files(tmp_path)
        assert files == []

        # generate_report with empty activities returns the Spanish no-activity message
        report = generate_report(MagicMock(), [], lang="es", output_format="markdown")
        assert "No se encontró actividad" in report
