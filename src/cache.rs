use chrono::Utc;
use rusqlite::{params, Connection};

use crate::models::{Activity, FileInfo};

/// Persistent cache backed by a SQLite database.
pub struct CacheDB {
    pub conn: Connection,
}

impl CacheDB {
    /// Open (or create) the SQLite database at `db_path`, enable WAL mode,
    /// and ensure the schema exists.
    pub fn new(db_path: &str) -> Self {
        let conn = Connection::open(db_path).expect("failed to open SQLite database");
        conn.execute_batch("PRAGMA journal_mode=WAL;")
            .expect("failed to set journal_mode=WAL");

        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime REAL,
                processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                day TEXT,
                project TEXT,
                git_org TEXT,
                git_repo TEXT,
                classification TEXT,
                summary TEXT
            );",
        )
        .expect("failed to create schema");

        Self { conn }
    }

    /// Record that `path` with the given `mtime` has been processed.
    pub fn mark_file_processed(&self, path: &str, mtime: f64) {
        let now = Utc::now().to_rfc3339();
        self.conn
            .execute(
                "INSERT OR REPLACE INTO files (path, mtime, processed_at) VALUES (?1, ?2, ?3)",
                params![path, mtime, now],
            )
            .expect("failed to mark file processed");
    }

    /// Return files that are either new or have a changed mtime compared to
    /// what is stored in the cache.
    pub fn get_unprocessed_files(&self, files: &[FileInfo]) -> Vec<FileInfo> {
        let mut unprocessed = Vec::new();
        for f in files {
            let stored_mtime: Option<f64> = self
                .conn
                .query_row(
                    "SELECT mtime FROM files WHERE path = ?1",
                    params![f.path],
                    |row| row.get(0),
                )
                .ok();

            match stored_mtime {
                Some(m) if m == f.mtime => {} // already processed with same mtime
                _ => unprocessed.push(f.clone()),
            }
        }
        unprocessed
    }

    /// Persist a batch of activities into the database.
    pub fn store_activities(&self, activities: &[Activity]) {
        for a in activities {
            self.conn
                .execute(
                    "INSERT INTO activities (session_id, day, project, git_org, git_repo, classification, summary)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                    params![
                        a.session_id,
                        a.day,
                        a.project,
                        a.git_org,
                        a.git_repo,
                        a.classification,
                        a.summary,
                    ],
                )
                .expect("failed to insert activity");
        }
    }

    /// Store a manual (non-log-derived) activity for today (UTC).
    pub fn store_manual_entry(
        &self,
        summary: &str,
        classification: &str,
        git_org: Option<&str>,
        git_repo: Option<&str>,
    ) {
        let today = Utc::now().format("%Y-%m-%d").to_string();
        let activity = Activity {
            session_id: "manual".to_string(),
            day: today,
            project: String::new(),
            git_org: git_org.map(String::from),
            git_repo: git_repo.map(String::from),
            classification: classification.to_string(),
            summary: summary.to_string(),
        };
        self.store_activities(&[activity]);
    }

    /// Return activities within a date range, optionally filtered by org/repo.
    ///
    /// The query is built dynamically to support optional `IN (...)` clauses.
    pub fn query_activities(
        &self,
        date_from: &str,
        date_to: &str,
        orgs: Option<&[String]>,
        repos: Option<&[String]>,
    ) -> Vec<Activity> {
        // Build the query string and a parallel params vector.
        let mut query = String::from(
            "SELECT session_id, day, project, git_org, git_repo, classification, summary \
             FROM activities WHERE day >= ? AND day <= ?",
        );
        let mut param_values: Vec<Box<dyn rusqlite::types::ToSql>> = Vec::new();
        param_values.push(Box::new(date_from.to_string()));
        param_values.push(Box::new(date_to.to_string()));

        if let Some(orgs) = orgs {
            if !orgs.is_empty() {
                let placeholders: Vec<&str> = orgs.iter().map(|_| "?").collect();
                query.push_str(&format!(" AND git_org IN ({})", placeholders.join(",")));
                for o in orgs {
                    param_values.push(Box::new(o.clone()));
                }
            }
        }

        if let Some(repos) = repos {
            if !repos.is_empty() {
                let placeholders: Vec<&str> = repos.iter().map(|_| "?").collect();
                query.push_str(&format!(" AND git_repo IN ({})", placeholders.join(",")));
                for r in repos {
                    param_values.push(Box::new(r.clone()));
                }
            }
        }

        query.push_str(" ORDER BY day, id");

        let params_refs: Vec<&dyn rusqlite::types::ToSql> =
            param_values.iter().map(|p| p.as_ref()).collect();

        let mut stmt = self.conn.prepare(&query).expect("failed to prepare query");
        let rows = stmt
            .query_map(params_refs.as_slice(), |row| {
                Ok(Activity {
                    session_id: row.get(0)?,
                    day: row.get(1)?,
                    project: row.get(2)?,
                    git_org: row.get(3)?,
                    git_repo: row.get(4)?,
                    classification: row.get(5)?,
                    summary: row.get(6)?,
                })
            })
            .expect("failed to execute query");

        rows.filter_map(|r| r.ok()).collect()
    }
}
