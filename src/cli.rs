//! CLI module: argument parsing, pipeline orchestration, and entry point.

use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::Path;

use chrono::{Duration, Local};
use clap::{Parser, Subcommand};

use crate::cache::CacheDB;
use crate::models::Activity;
use crate::parser::{derive_project_name, discover_jsonl_files, parse_session_summaries, resolve_git_remote};
use crate::reporter::{generate_llm_report, generate_template_report};

// ---------------------------------------------------------------------------
// Valid activity types for the `log` subcommand
// ---------------------------------------------------------------------------

const VALID_TYPES: &[&str] = &[
    "FEATURE", "BUGFIX", "REFACTOR", "DEBUGGING", "EXPLORATION",
    "REVIEW", "SUPPORT", "MEETING", "OTHER",
];

// ---------------------------------------------------------------------------
// CLI definition (clap derive)
// ---------------------------------------------------------------------------

#[derive(clap::Args, Clone, Debug)]
struct CommonArgs {
    /// Start date (YYYY-MM-DD).
    #[arg(long = "from")]
    date_from: Option<String>,

    /// End date (YYYY-MM-DD).
    #[arg(long = "to")]
    date_to: Option<String>,

    /// Filter by GitHub org (comma-separated).
    #[arg(long)]
    org: Option<String>,

    /// Filter by GitHub repo (comma-separated).
    #[arg(long)]
    repo: Option<String>,

    /// Report language (default: es).
    #[arg(long, default_value = "es")]
    lang: String,

    /// Output format (default: markdown).
    #[arg(long, default_value = "markdown")]
    format: String,

    /// Write report to this file path.
    #[arg(long)]
    output: Option<String>,

    /// Use local template only (no LLM polish).
    #[arg(long)]
    raw: bool,

    /// Print progress to stderr.
    #[arg(long)]
    verbose: bool,
}

impl Default for CommonArgs {
    fn default() -> Self {
        Self {
            date_from: None,
            date_to: None,
            org: None,
            repo: None,
            lang: "es".to_string(),
            format: "markdown".to_string(),
            output: None,
            raw: false,
            verbose: false,
        }
    }
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Report for today.
    Today {
        #[command(flatten)]
        common: CommonArgs,
    },

    /// Report for yesterday.
    Yesterday {
        #[command(flatten)]
        common: CommonArgs,
    },

    /// Report for the last 7 days.
    #[command(name = "last-7-days")]
    Last7Days {
        #[command(flatten)]
        common: CommonArgs,
    },

    /// Add a manual activity entry.
    Log {
        /// Activity description.
        message: String,

        /// Activity type (default: OTHER).
        #[arg(long, default_value = "OTHER")]
        r#type: String,

        #[command(flatten)]
        common: CommonArgs,
    },

    /// Show processing status.
    Status {
        #[command(flatten)]
        common: CommonArgs,
    },

    /// Check data source health.
    Check {
        #[command(flatten)]
        common: CommonArgs,
    },
}

#[derive(Parser, Debug)]
#[command(
    name = "claude-standup",
    about = "Daily standup reports from Claude Code activity logs"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,
}

// ---------------------------------------------------------------------------
// Date range resolution
// ---------------------------------------------------------------------------

/// Resolve effective (from, to) dates based on the command and optional overrides.
fn resolve_date_range(
    command: &str,
    date_from: Option<&str>,
    date_to: Option<&str>,
) -> (String, String) {
    let today = Local::now().date_naive();

    let (default_from, default_to) = match command {
        "yesterday" => {
            let yesterday = today - Duration::days(1);
            (yesterday, yesterday)
        }
        "last-7-days" => {
            let week_ago = today - Duration::days(6);
            (week_ago, today)
        }
        _ => (today, today),
    };

    let resolved_from = date_from
        .map(|s| s.to_string())
        .unwrap_or_else(|| default_from.format("%Y-%m-%d").to_string());

    let mut resolved_to = date_to
        .map(|s| s.to_string())
        .unwrap_or_else(|| default_to.format("%Y-%m-%d").to_string());

    // If --from is specified but --to is not, default --to to today.
    if date_from.is_some() && date_to.is_none() {
        resolved_to = today.format("%Y-%m-%d").to_string();
    }

    (resolved_from, resolved_to)
}

// ---------------------------------------------------------------------------
// Subagent detection
// ---------------------------------------------------------------------------

/// Returns `true` if `name` looks like a UUID v4 directory (subagent working dir).
fn _looks_like_subagent_dir(name: &str) -> bool {
    // UUID v4 pattern: 8-4-4-4-12 hex chars
    let bytes = name.as_bytes();
    if bytes.len() != 36 {
        return false;
    }
    for (i, &b) in bytes.iter().enumerate() {
        match i {
            8 | 13 | 18 | 23 => {
                if b != b'-' {
                    return false;
                }
            }
            _ => {
                if !b.is_ascii_hexdigit() {
                    return false;
                }
            }
        }
    }
    true
}

// ---------------------------------------------------------------------------
// File processing pipeline
// ---------------------------------------------------------------------------

/// Parse new/modified JSONL files, extract away_summaries, store as activities.
fn process_files(db: &CacheDB, logs_base: &str, verbose: bool) {
    let all_files: Vec<_> = discover_jsonl_files(logs_base)
        .into_iter()
        .filter(|f| !f.path.contains("/subagents/"))
        .collect();

    if verbose {
        eprintln!("Discovered {} JSONL file(s).", all_files.len());
    }

    let to_process = db.get_unprocessed_files(&all_files);

    if verbose {
        eprintln!("New/modified: {}", to_process.len());
    }

    let mut git_cache = HashMap::new();

    for (idx, fi) in to_process.iter().enumerate() {
        let file_path = Path::new(&fi.path);
        let parent = file_path.parent().unwrap_or(Path::new(""));
        let mut dir_name = parent
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();

        if _looks_like_subagent_dir(&dir_name) {
            dir_name = parent
                .parent()
                .and_then(|p| p.file_name())
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
        }

        let project = derive_project_name(&dir_name);

        if verbose {
            let label = if project.is_empty() { "?" } else { &project };
            eprintln!("  [{}/{}] {}", idx + 1, to_process.len(), label);
        }

        let summaries = parse_session_summaries(&fi.path, &project);

        for s in &summaries {
            let git_info = if !s.cwd.is_empty() {
                resolve_git_remote(&s.cwd, &mut git_cache)
            } else {
                crate::models::GitInfo::default()
            };

            let day = if s.timestamp.len() >= 10 {
                s.timestamp[..10].to_string()
            } else {
                String::new()
            };

            let activity = Activity {
                session_id: s.session_id.clone(),
                day,
                project: project.clone(),
                git_org: git_info.org,
                git_repo: git_info.repo,
                classification: String::new(), // not needed -- summary speaks for itself
                summary: s.content.clone(),
            };

            db.store_activities(&[activity]);
        }

        db.mark_file_processed(&fi.path, fi.mtime);
    }
}

// ---------------------------------------------------------------------------
// Check command
// ---------------------------------------------------------------------------

/// Check data source health: scan JSONL files for away_summary coverage.
fn handle_check(logs_base: &str, common: &CommonArgs) {
    let (mut date_from, mut date_to) = resolve_date_range(
        "check",
        common.date_from.as_deref(),
        common.date_to.as_deref(),
    );

    // Default to last 7 days for check if --from not specified
    if common.date_from.is_none() {
        let today = Local::now().date_naive();
        date_from = (today - Duration::days(6)).format("%Y-%m-%d").to_string();
        date_to = today.format("%Y-%m-%d").to_string();
    }

    let all_files: Vec<_> = discover_jsonl_files(logs_base)
        .into_iter()
        .filter(|f| !f.path.contains("/subagents/"))
        .collect();

    let mut total_sessions: u64 = 0;
    let mut with_summary: u64 = 0;
    let mut without_summary: u64 = 0;
    let mut summaries_by_day: HashMap<String, u64> = HashMap::new();
    let mut sessions_by_day: HashMap<String, u64> = HashMap::new();
    let mut versions: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();

    for fi in &all_files {
        let file = match fs::File::open(&fi.path) {
            Ok(f) => f,
            Err(_) => continue,
        };
        let reader = BufReader::new(file);

        let mut file_has_summary = false;
        let mut file_has_activity_in_range = false;
        let mut ver: Option<String> = None;

        for line in reader.lines() {
            let line = match line {
                Ok(l) => l,
                Err(_) => continue,
            };
            let trimmed = line.trim().to_string();
            if trimmed.is_empty() {
                continue;
            }

            let obj: serde_json::Value = match serde_json::from_str(&trimmed) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let ts = obj.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
            let day = if ts.len() >= 10 { &ts[..10] } else { "" };

            if ver.is_none() {
                if let Some(v) = obj.get("version").and_then(|v| v.as_str()) {
                    if !v.is_empty() {
                        ver = Some(v.to_string());
                    }
                }
            }

            if day < date_from.as_str() || day > date_to.as_str() {
                continue;
            }

            // Check for user activity
            if obj.get("type").and_then(|v| v.as_str()) == Some("user") {
                if let Some(msg) = obj.get("message") {
                    if msg.get("content").and_then(|v| v.as_str()).is_some() {
                        file_has_activity_in_range = true;
                        *sessions_by_day.entry(day.to_string()).or_insert(0) += 1;
                    }
                }
            }

            // Check for away_summary
            if obj.get("type").and_then(|v| v.as_str()) == Some("system")
                && obj.get("subtype").and_then(|v| v.as_str()) == Some("away_summary")
            {
                file_has_summary = true;
                *summaries_by_day.entry(day.to_string()).or_insert(0) += 1;
            }
        }

        if file_has_activity_in_range {
            total_sessions += 1;
            if file_has_summary {
                with_summary += 1;
            } else {
                without_summary += 1;
            }
        }
        if let Some(v) = ver {
            versions.insert(v);
        }
    }

    let pct = if total_sessions > 0 {
        (with_summary * 100 / total_sessions) as u64
    } else {
        0
    };

    println!("Data source check ({} to {})", date_from, date_to);
    println!();
    println!("  Sessions with activity:  {}", total_sessions);
    println!("  With away_summary:       {} ({}%)", with_summary, pct);
    println!("  Without away_summary:    {}", without_summary);

    let versions_str = if versions.is_empty() {
        "?".to_string()
    } else {
        versions.into_iter().collect::<Vec<_>>().join(", ")
    };
    println!("  Claude Code versions:    {}", versions_str);
    println!();
    println!("  Per day:");

    let mut all_days: Vec<String> = summaries_by_day
        .keys()
        .chain(sessions_by_day.keys())
        .cloned()
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    all_days.sort();

    for day in &all_days {
        let s = summaries_by_day.get(day).copied().unwrap_or(0);
        let t = sessions_by_day.get(day).copied().unwrap_or(0);
        let bar = if t > 0 {
            let filled = "\u{2588}".repeat(s as usize);
            let empty = "\u{2591}".repeat(if t > s { (t - s) as usize } else { 0 });
            format!("{}{}", filled, empty)
        } else {
            String::new()
        };
        println!("    {}  {}/{} summaries  {}", day, s, t, bar);
    }

    println!();
    if pct < 50 {
        println!("  \u{26a0}  Low coverage. away_summary is a recent Claude Code feature.");
        println!("     Sessions without it will have limited detail in reports.");
        println!("     Update Claude Code to the latest version for best results.");
    } else {
        println!("  \u{2713}  Good coverage. Reports should be accurate.");
    }
}

// ---------------------------------------------------------------------------
// Status command
// ---------------------------------------------------------------------------

fn handle_status(db: &CacheDB) {
    let total_files: i64 = db
        .conn
        .query_row("SELECT count(*) FROM files", [], |row| row.get(0))
        .unwrap_or(0);

    let total_activities: i64 = db
        .conn
        .query_row("SELECT count(*) FROM activities", [], |row| row.get(0))
        .unwrap_or(0);

    println!("Processing status:");
    println!("  Files parsed:   {}", total_files);
    println!("  Activities:     {}", total_activities);
}

// ---------------------------------------------------------------------------
// Report pipeline
// ---------------------------------------------------------------------------

/// Full pipeline: process files -> query -> generate report.
fn run_report(db: &CacheDB, logs_base: &str, command: &str, common: &CommonArgs) -> String {
    let (date_from, date_to) = resolve_date_range(
        command,
        common.date_from.as_deref(),
        common.date_to.as_deref(),
    );

    if common.verbose {
        eprintln!("Date range: {} .. {}", date_from, date_to);
    }

    process_files(db, logs_base, common.verbose);

    let orgs: Option<Vec<String>> = common
        .org
        .as_ref()
        .map(|o| o.split(',').map(|s| s.trim().to_string()).collect());

    let repos: Option<Vec<String>> = common
        .repo
        .as_ref()
        .map(|r| r.split(',').map(|s| s.trim().to_string()).collect());

    let activities = db.query_activities(
        &date_from,
        &date_to,
        orgs.as_deref(),
        repos.as_deref(),
    );

    if common.verbose {
        eprintln!("Activities found: {}", activities.len());
    }

    if common.raw {
        generate_template_report(&activities, &common.format, &common.lang)
    } else {
        generate_llm_report(&activities, &common.format, &common.lang)
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/// Parse CLI arguments and run the appropriate command.
pub fn run() {
    let cli = Cli::parse();

    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let logs_base = format!("{home}/.claude/projects");
    let db_path = format!("{home}/.claude-standup/cache.db");

    // Ensure parent directories exist before opening DB
    if let Some(parent) = Path::new(&db_path).parent() {
        fs::create_dir_all(parent).ok();
    }

    // Default to "today" when no subcommand is given
    let command = cli.command.unwrap_or(Commands::Today {
        common: CommonArgs::default(),
    });

    match command {
        Commands::Log {
            message,
            r#type,
            common,
        } => {
            // Validate type
            let typ = r#type.to_uppercase();
            if !VALID_TYPES.contains(&typ.as_str()) {
                eprintln!(
                    "Invalid type '{}'. Valid types: {}",
                    typ,
                    VALID_TYPES.join(", ")
                );
                std::process::exit(1);
            }

            let db = CacheDB::new(&db_path);
            db.store_manual_entry(
                &message,
                &typ,
                common.org.as_deref(),
                common.repo.as_deref(),
            );
            eprintln!("Logged: [{}] {}", typ, message);
        }

        Commands::Check { common } => {
            handle_check(&logs_base, &common);
        }

        Commands::Status { common: _ } => {
            let db = CacheDB::new(&db_path);
            handle_status(&db);
        }

        Commands::Today { common } => {
            let db = CacheDB::new(&db_path);
            let report = run_report(&db, &logs_base, "today", &common);
            println!("{}", report);
            if let Some(ref output_path) = common.output {
                write_output_file(output_path, &report);
            }
        }

        Commands::Yesterday { common } => {
            let db = CacheDB::new(&db_path);
            let report = run_report(&db, &logs_base, "yesterday", &common);
            println!("{}", report);
            if let Some(ref output_path) = common.output {
                write_output_file(output_path, &report);
            }
        }

        Commands::Last7Days { common } => {
            let db = CacheDB::new(&db_path);
            let report = run_report(&db, &logs_base, "last-7-days", &common);
            println!("{}", report);
            if let Some(ref output_path) = common.output {
                write_output_file(output_path, &report);
            }
        }
    }
}

/// Write report content to the specified output file, creating parent dirs if needed.
fn write_output_file(path: &str, content: &str) {
    if let Some(parent) = Path::new(path).parent() {
        fs::create_dir_all(parent).ok();
    }
    if let Err(e) = fs::write(path, content) {
        eprintln!("Failed to write output file '{}': {}", path, e);
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_date_range_today() {
        let today = Local::now().date_naive().format("%Y-%m-%d").to_string();
        let (from, to) = resolve_date_range("today", None, None);
        assert_eq!(from, today);
        assert_eq!(to, today);
    }

    #[test]
    fn test_resolve_date_range_yesterday() {
        let yesterday = (Local::now().date_naive() - Duration::days(1))
            .format("%Y-%m-%d")
            .to_string();
        let (from, to) = resolve_date_range("yesterday", None, None);
        assert_eq!(from, yesterday);
        assert_eq!(to, yesterday);
    }

    #[test]
    fn test_resolve_date_range_last7days() {
        let today = Local::now().date_naive();
        let week_ago = (today - Duration::days(6)).format("%Y-%m-%d").to_string();
        let today_str = today.format("%Y-%m-%d").to_string();
        let (from, to) = resolve_date_range("last-7-days", None, None);
        assert_eq!(from, week_ago);
        assert_eq!(to, today_str);
    }

    #[test]
    fn test_resolve_date_range_override_from() {
        let today = Local::now().date_naive().format("%Y-%m-%d").to_string();
        let (from, to) = resolve_date_range("today", Some("2025-01-01"), None);
        assert_eq!(from, "2025-01-01");
        // When --from is set but --to is not, default to today
        assert_eq!(to, today);
    }

    #[test]
    fn test_resolve_date_range_override_both() {
        let (from, to) = resolve_date_range("today", Some("2025-01-01"), Some("2025-01-15"));
        assert_eq!(from, "2025-01-01");
        assert_eq!(to, "2025-01-15");
    }

    #[test]
    fn test_looks_like_subagent_dir_valid() {
        assert!(_looks_like_subagent_dir(
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        ));
    }

    #[test]
    fn test_looks_like_subagent_dir_invalid() {
        assert!(!_looks_like_subagent_dir("not-a-uuid"));
        assert!(!_looks_like_subagent_dir(""));
        assert!(!_looks_like_subagent_dir("my-project-name"));
    }

    #[test]
    fn test_looks_like_subagent_dir_uppercase_hex() {
        assert!(_looks_like_subagent_dir(
            "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
        ));
    }
}
