"""Tests for claude_standup.cache module."""

from __future__ import annotations

import sqlite3

import pytest

from claude_standup.models import Activity, FileInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cache(tmp_path=None):
    """Create a CacheDB instance (in-memory or on disk)."""
    from claude_standup.cache import CacheDB

    if tmp_path is not None:
        return CacheDB(str(tmp_path / "test.db"))
    return CacheDB(":memory:")


# ---------------------------------------------------------------------------
# TestSchemaCreation
# ---------------------------------------------------------------------------

class TestSchemaCreation:
    """Verify that the DB schema is created correctly on init."""

    def test_creates_tables(self, tmp_path):
        db = _make_cache(tmp_path)
        cursor = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = sorted(row[0] for row in cursor.fetchall())
        assert "activities" in tables
        assert "files" in tables
        assert "sessions" in tables
        db.close()

    def test_in_memory_db(self):
        db = _make_cache()
        cursor = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = sorted(row[0] for row in cursor.fetchall())
        assert "activities" in tables
        assert "files" in tables
        assert "sessions" in tables
        db.close()


# ---------------------------------------------------------------------------
# TestFileTracking
# ---------------------------------------------------------------------------

class TestFileTracking:
    """Verify mark / detect / clear flow for processed files."""

    def test_mark_file_processed(self):
        db = _make_cache()
        db.mark_file_processed("/tmp/a.jsonl", 1000.0)
        row = db.conn.execute("SELECT path, mtime FROM files").fetchone()
        assert row == ("/tmp/a.jsonl", 1000.0)
        db.close()

    def test_detect_new_files(self):
        db = _make_cache()
        files = [
            FileInfo(path="/tmp/a.jsonl", mtime=1000.0),
            FileInfo(path="/tmp/b.jsonl", mtime=2000.0),
        ]
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 2
        assert unprocessed[0].path == "/tmp/a.jsonl"
        assert unprocessed[1].path == "/tmp/b.jsonl"
        db.close()

    def test_detect_modified_files(self):
        db = _make_cache()
        db.mark_file_processed("/tmp/a.jsonl", 1000.0)
        # Same path, new mtime => should be considered unprocessed
        files = [FileInfo(path="/tmp/a.jsonl", mtime=2000.0)]
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 1
        assert unprocessed[0].mtime == 2000.0
        db.close()

    def test_skip_unchanged_files(self):
        db = _make_cache()
        db.mark_file_processed("/tmp/a.jsonl", 1000.0)
        files = [FileInfo(path="/tmp/a.jsonl", mtime=1000.0)]
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 0
        db.close()

    def test_reprocess_clears_file_tracking(self):
        db = _make_cache()
        db.mark_file_processed("/tmp/a.jsonl", 1000.0)
        db.mark_file_processed("/tmp/b.jsonl", 2000.0)
        db.clear_file_tracking()
        # After clearing, all files should appear unprocessed
        files = [
            FileInfo(path="/tmp/a.jsonl", mtime=1000.0),
            FileInfo(path="/tmp/b.jsonl", mtime=2000.0),
        ]
        unprocessed = db.get_unprocessed_files(files)
        assert len(unprocessed) == 2
        db.close()


# ---------------------------------------------------------------------------
# TestSessionStorage
# ---------------------------------------------------------------------------

class TestSessionStorage:
    """Verify session INSERT and ON CONFLICT UPDATE behaviour."""

    def test_store_session(self):
        db = _make_cache()
        db.store_session("s1", "my-app", "acme", "my-app", "2026-04-21T08:00:00Z", "2026-04-21T09:00:00Z")
        row = db.conn.execute("SELECT * FROM sessions WHERE session_id = ?", ("s1",)).fetchone()
        assert row is not None
        assert row[0] == "s1"       # session_id
        assert row[1] == "my-app"   # project
        assert row[2] == "acme"     # git_org
        assert row[3] == "my-app"   # git_repo
        assert row[4] == "2026-04-21T08:00:00Z"  # first_ts
        assert row[5] == "2026-04-21T09:00:00Z"  # last_ts
        db.close()

    def test_update_session_timestamps(self):
        db = _make_cache()
        db.store_session("s1", "my-app", "acme", "my-app", "2026-04-21T08:00:00Z", "2026-04-21T09:00:00Z")
        # Re-insert same session_id with a later last_ts
        db.store_session("s1", "my-app", "acme", "my-app", "2026-04-21T08:00:00Z", "2026-04-21T12:00:00Z")
        row = db.conn.execute("SELECT last_ts FROM sessions WHERE session_id = ?", ("s1",)).fetchone()
        assert row[0] == "2026-04-21T12:00:00Z"
        db.close()


# ---------------------------------------------------------------------------
# TestActivityStorage
# ---------------------------------------------------------------------------

class TestActivityStorage:
    """Verify storing and querying activities, including JSON serialization."""

    def _make_activity(self, **overrides) -> Activity:
        defaults = dict(
            session_id="s1",
            day="2026-04-21",
            project="my-app",
            git_org="acme",
            git_repo="my-app",
            classification="FEATURE",
            summary="Implemented login",
            files_mentioned=["auth.py", "login.html"],
            technologies=["Python", "OAuth2"],
            time_spent_minutes=45,
            raw_prompts=["Implement login", "Add OAuth2"],
        )
        defaults.update(overrides)
        return Activity(**defaults)

    def test_store_and_retrieve_activities(self):
        db = _make_cache()
        activity = self._make_activity()
        db.store_activities([activity])

        results = db.query_activities("2026-04-21", "2026-04-21")
        assert len(results) == 1
        r = results[0]
        assert r.session_id == "s1"
        assert r.day == "2026-04-21"
        assert r.project == "my-app"
        assert r.git_org == "acme"
        assert r.git_repo == "my-app"
        assert r.classification == "FEATURE"
        assert r.summary == "Implemented login"
        assert r.files_mentioned == ["auth.py", "login.html"]
        assert r.technologies == ["Python", "OAuth2"]
        assert r.time_spent_minutes == 45
        assert r.raw_prompts == ["Implement login", "Add OAuth2"]
        db.close()

    def test_query_by_date_range(self):
        db = _make_cache()
        db.store_activities([
            self._make_activity(day="2026-04-20", summary="day20"),
            self._make_activity(day="2026-04-21", summary="day21"),
            self._make_activity(day="2026-04-22", summary="day22"),
        ])
        results = db.query_activities("2026-04-20", "2026-04-21")
        assert len(results) == 2
        assert results[0].summary == "day20"
        assert results[1].summary == "day21"
        db.close()

    def test_query_by_org(self):
        db = _make_cache()
        db.store_activities([
            self._make_activity(git_org="acme", summary="acme-work"),
            self._make_activity(git_org="other-corp", summary="other-work"),
        ])
        results = db.query_activities("2026-04-21", "2026-04-21", orgs=["acme"])
        assert len(results) == 1
        assert results[0].summary == "acme-work"
        db.close()

    def test_query_by_multiple_orgs(self):
        db = _make_cache()
        db.store_activities([
            self._make_activity(git_org="acme", summary="acme-work"),
            self._make_activity(git_org="beta-inc", summary="beta-work"),
            self._make_activity(git_org="gamma-llc", summary="gamma-work"),
        ])
        results = db.query_activities("2026-04-21", "2026-04-21", orgs=["acme", "beta-inc"])
        assert len(results) == 2
        summaries = {r.summary for r in results}
        assert summaries == {"acme-work", "beta-work"}
        db.close()

    def test_query_by_repo(self):
        db = _make_cache()
        db.store_activities([
            self._make_activity(git_repo="my-app", summary="app-work"),
            self._make_activity(git_repo="other-repo", summary="other-work"),
        ])
        results = db.query_activities("2026-04-21", "2026-04-21", repos=["my-app"])
        assert len(results) == 1
        assert results[0].summary == "app-work"
        db.close()

    def test_query_by_org_and_repo(self):
        db = _make_cache()
        db.store_activities([
            self._make_activity(git_org="acme", git_repo="app-a", summary="a"),
            self._make_activity(git_org="acme", git_repo="app-b", summary="b"),
            self._make_activity(git_org="other", git_repo="app-a", summary="c"),
        ])
        results = db.query_activities(
            "2026-04-21", "2026-04-21", orgs=["acme"], repos=["app-a"]
        )
        assert len(results) == 1
        assert results[0].summary == "a"
        db.close()


# ---------------------------------------------------------------------------
# TestManualEntries
# ---------------------------------------------------------------------------

class TestManualEntries:
    """Verify store_manual_entry produces correct records."""

    def test_store_manual_entry(self):
        db = _make_cache()
        db.store_manual_entry("Sprint planning", "MEETING", None, None)

        results = db.query_activities("2000-01-01", "2099-12-31")
        assert len(results) == 1
        r = results[0]
        assert r.session_id == "manual"
        assert r.classification == "MEETING"
        assert r.summary == "Sprint planning"
        assert r.git_org is None
        assert r.git_repo is None
        db.close()

    def test_manual_entry_with_org_repo(self):
        db = _make_cache()
        db.store_manual_entry("Code review sync", "REVIEW", "acme", "my-app")

        results = db.query_activities("2000-01-01", "2099-12-31")
        assert len(results) == 1
        r = results[0]
        assert r.session_id == "manual"
        assert r.classification == "REVIEW"
        assert r.git_org == "acme"
        assert r.git_repo == "my-app"
        db.close()
