"""Cache module: SQLite-backed storage for processed sessions and activities."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from claude_standup.models import Activity, FileInfo


class CacheDB:
    """Persistent cache backed by a SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime REAL,
                processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                project TEXT,
                git_org TEXT,
                git_repo TEXT,
                first_ts TEXT,
                last_ts TEXT,
                classified INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS raw_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                timestamp TEXT,
                content TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                day TEXT,
                project TEXT,
                git_org TEXT,
                git_repo TEXT,
                classification TEXT,
                summary TEXT,
                files_mentioned TEXT,
                technologies TEXT,
                time_spent_minutes INTEGER,
                raw_prompts TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );
            """
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # File tracking
    # ------------------------------------------------------------------

    def mark_file_processed(self, path: str, mtime: float) -> None:
        """Record that *path* with the given *mtime* has been processed."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime, processed_at) VALUES (?, ?, ?)",
            (path, mtime, now),
        )
        self.conn.commit()

    def get_unprocessed_files(self, files: list[FileInfo]) -> list[FileInfo]:
        """Return files that are either new or have a changed mtime."""
        unprocessed: list[FileInfo] = []
        for f in files:
            row = self.conn.execute(
                "SELECT mtime FROM files WHERE path = ?", (f.path,)
            ).fetchone()
            if row is None or row[0] != f.mtime:
                unprocessed.append(f)
        return unprocessed

    def clear_file_tracking(self) -> None:
        """Delete all file-tracking rows so every file is re-processed."""
        self.conn.execute("DELETE FROM files")
        self.conn.commit()

    # ------------------------------------------------------------------
    # Session storage
    # ------------------------------------------------------------------

    def store_session(
        self,
        session_id: str,
        project: str,
        git_org: str | None,
        git_repo: str | None,
        first_ts: str,
        last_ts: str,
    ) -> None:
        """Insert a session or update its *last_ts* on conflict."""
        self.conn.execute(
            """
            INSERT INTO sessions (session_id, project, git_org, git_repo, first_ts, last_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET last_ts = excluded.last_ts
            """,
            (session_id, project, git_org, git_repo, first_ts, last_ts),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Raw prompt storage
    # ------------------------------------------------------------------

    def store_raw_prompts(self, session_id: str, prompts: list[tuple[str, str]]) -> None:
        """Store raw user prompts for a session. Each prompt is (timestamp, content)."""
        for ts, content in prompts:
            self.conn.execute(
                "INSERT INTO raw_prompts (session_id, timestamp, content) VALUES (?, ?, ?)",
                (session_id, ts, content),
            )
        self.conn.commit()

    def get_unclassified_sessions(self, date_from: str, date_to: str,
                                   orgs: list[str] | None = None,
                                   repos: list[str] | None = None) -> list[dict]:
        """Return unclassified sessions that overlap the given date range."""
        query = """
            SELECT session_id, project, git_org, git_repo, first_ts, last_ts
            FROM sessions
            WHERE classified = 0
              AND first_ts IS NOT NULL
              AND substr(first_ts, 1, 10) <= ?
              AND substr(last_ts, 1, 10) >= ?
        """
        params: list[str] = [date_to, date_from]

        if orgs:
            placeholders = ",".join("?" for _ in orgs)
            query += f" AND git_org IN ({placeholders})"
            params.extend(orgs)
        if repos:
            placeholders = ",".join("?" for _ in repos)
            query += f" AND git_repo IN ({placeholders})"
            params.extend(repos)

        rows = self.conn.execute(query, params).fetchall()
        return [
            {"session_id": r[0], "project": r[1], "git_org": r[2],
             "git_repo": r[3], "first_ts": r[4], "last_ts": r[5]}
            for r in rows
        ]

    def get_raw_prompts(self, session_id: str) -> list[tuple[str, str]]:
        """Return raw prompts for a session as (timestamp, content) tuples."""
        rows = self.conn.execute(
            "SELECT timestamp, content FROM raw_prompts WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def mark_session_classified(self, session_id: str) -> None:
        """Mark a session as classified."""
        self.conn.execute(
            "UPDATE sessions SET classified = 1 WHERE session_id = ?",
            (session_id,),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Activity storage
    # ------------------------------------------------------------------

    def store_activities(self, activities: list[Activity]) -> None:
        """Persist a batch of activities, JSON-serialising list fields."""
        for a in activities:
            self.conn.execute(
                """
                INSERT INTO activities
                    (session_id, day, project, git_org, git_repo, classification,
                     summary, files_mentioned, technologies, time_spent_minutes,
                     raw_prompts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a.session_id,
                    a.day,
                    a.project,
                    a.git_org,
                    a.git_repo,
                    a.classification,
                    a.summary,
                    json.dumps(a.files_mentioned),
                    json.dumps(a.technologies),
                    a.time_spent_minutes,
                    json.dumps(a.raw_prompts),
                ),
            )
        self.conn.commit()

    def store_manual_entry(
        self,
        summary: str,
        classification: str,
        git_org: str | None,
        git_repo: str | None,
    ) -> None:
        """Store a manual (non-log-derived) activity for today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        activity = Activity(
            session_id="manual",
            day=today,
            project="",
            git_org=git_org,
            git_repo=git_repo,
            classification=classification,
            summary=summary,
        )
        self.store_activities([activity])

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query_activities(
        self,
        date_from: str,
        date_to: str,
        orgs: list[str] | None = None,
        repos: list[str] | None = None,
    ) -> list[Activity]:
        """Return activities within a date range, optionally filtered by org/repo."""
        query = "SELECT * FROM activities WHERE day >= ? AND day <= ?"
        params: list[str] = [date_from, date_to]

        if orgs:
            placeholders = ",".join("?" for _ in orgs)
            query += f" AND git_org IN ({placeholders})"
            params.extend(orgs)

        if repos:
            placeholders = ",".join("?" for _ in repos)
            query += f" AND git_repo IN ({placeholders})"
            params.extend(repos)

        query += " ORDER BY day, id"
        rows = self.conn.execute(query, params).fetchall()

        results: list[Activity] = []
        for row in rows:
            results.append(
                Activity(
                    session_id=row[1],
                    day=row[2],
                    project=row[3],
                    git_org=row[4],
                    git_repo=row[5],
                    classification=row[6],
                    summary=row[7],
                    files_mentioned=json.loads(row[8]) if row[8] else [],
                    technologies=json.loads(row[9]) if row[9] else [],
                    time_spent_minutes=row[10] or 0,
                    raw_prompts=json.loads(row[11]) if row[11] else [],
                )
            )
        return results

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        self.conn.close()
