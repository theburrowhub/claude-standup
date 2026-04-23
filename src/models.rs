/// Shared data models used across all modules.

/// A discovered JSONL file with its modification time.
#[derive(Debug, Clone)]
pub struct FileInfo {
    pub path: String,
    pub mtime: f64,
}

/// GitHub organization and repository extracted from a git remote URL.
#[derive(Debug, Clone, Default)]
pub struct GitInfo {
    pub org: Option<String>,
    pub repo: Option<String>,
}

/// An away_summary extracted from a Claude Code session.
#[derive(Debug, Clone)]
pub struct SessionSummary {
    pub timestamp: String,
    pub session_id: String,
    pub project: String,
    pub content: String,
    pub cwd: String,
    pub git_branch: Option<String>,
}

/// A classified development activity (stored in cache).
#[derive(Debug, Clone)]
pub struct Activity {
    pub session_id: String,
    pub day: String,
    pub project: String,
    pub git_org: Option<String>,
    pub git_repo: Option<String>,
    pub classification: String,
    pub summary: String,
}
