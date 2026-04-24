//! Parser module: discovers and reads Claude Code JSONL log files.
//!
//! Primary data source: `away_summary` entries (type=system, subtype=away_summary).
//! These are high-quality session recaps written by Claude Code itself.

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::process::Command;
use std::time::UNIX_EPOCH;

use walkdir::WalkDir;

use crate::models::{FileInfo, GitInfo, SessionSummary};

/// Recursively find `.jsonl` files under `base_path`, returning each with its mtime.
pub fn discover_jsonl_files(base_path: &str) -> Vec<FileInfo> {
    let mut results = Vec::new();

    for entry in WalkDir::new(base_path).into_iter().filter_map(|e| e.ok()) {
        if entry.file_type().is_file() {
            if let Some(ext) = entry.path().extension() {
                if ext == "jsonl" {
                    let path = entry.path().to_string_lossy().to_string();
                    let mtime = entry
                        .metadata()
                        .ok()
                        .and_then(|m| m.modified().ok())
                        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                        .map(|d| d.as_secs_f64())
                        .unwrap_or(0.0);
                    results.push(FileInfo { path, mtime });
                }
            }
        }
    }

    results
}

/// Extract `away_summary` entries from a JSONL file.
///
/// Each summary gets the timestamp of the **preceding user prompt** (not the
/// summary itself), so the `day` reflects when the work actually happened
/// rather than when Claude Code regenerated the recap.
pub fn parse_session_summaries(file_path: &str, project_name: &str) -> Vec<SessionSummary> {
    let mut summaries = Vec::new();

    let file = match File::open(file_path) {
        Ok(f) => f,
        Err(_) => return summaries,
    };

    let reader = BufReader::new(file);

    // We collect ALL away_summaries but only keep the LAST one per session,
    // which is the most complete recap. We use the session's first user
    // prompt timestamp as the activity date (not the summary's timestamp,
    // which gets regenerated each time the session is resumed).
    let mut first_user_ts = String::new();
    let mut session_cwd = String::new();
    let mut session_id = String::new();
    let mut session_git_branch: Option<String> = None;
    let mut last_summary_content = String::new();

    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }

        let obj: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        let entry_type = obj.get("type").and_then(|v| v.as_str()).unwrap_or("");

        // Track the first user prompt for the real activity date
        if entry_type == "user" {
            if let Some(ts) = obj.get("timestamp").and_then(|v| v.as_str()) {
                if first_user_ts.is_empty() {
                    first_user_ts = ts.to_string();
                }
            }
            if session_cwd.is_empty() {
                if let Some(cwd) = obj.get("cwd").and_then(|v| v.as_str()) {
                    session_cwd = cwd.to_string();
                }
            }
            if session_id.is_empty() {
                if let Some(sid) = obj.get("sessionId").and_then(|v| v.as_str()) {
                    session_id = sid.to_string();
                }
            }
            if session_git_branch.is_none() {
                session_git_branch = obj.get("gitBranch").and_then(|v| v.as_str()).map(|s| s.to_string());
            }
        }

        let subtype = obj.get("subtype").and_then(|v| v.as_str()).unwrap_or("");

        if entry_type == "system" && subtype == "away_summary" {
            let content = obj
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim()
                .to_string();

            if !content.is_empty() {
                // Overwrite — we only want the last (most complete) summary
                last_summary_content = content;
            }
        }
    }

    // Emit a single summary for the session (the last away_summary)
    if !last_summary_content.is_empty() {
        let ts = if !first_user_ts.is_empty() {
            first_user_ts
        } else {
            // No user prompts at all — shouldn't happen, but fallback
            String::new()
        };

        if !ts.is_empty() {
            summaries.push(SessionSummary {
                timestamp: ts,
                session_id,
                project: project_name.to_string(),
                content: last_summary_content,
                cwd: session_cwd,
                git_branch: session_git_branch,
            });
        }
    }

    summaries
}

/// Convert a Claude Code project directory name to a readable project name.
///
/// Strips leading `-`, splits by `-`, finds the last `"workspace"` segment,
/// and returns everything after it joined by `-`.
/// Fallback: if no workspace found and len > 2, return segments from index 2 onward.
pub fn derive_project_name(dir_name: &str) -> String {
    let name = dir_name.trim_start_matches('-');
    let parts: Vec<&str> = name.split('-').collect();

    // Search backwards for "workspace"
    for i in (0..parts.len()).rev() {
        if parts[i] == "workspace" {
            let remainder = &parts[i + 1..];
            if !remainder.is_empty() {
                return remainder.join("-");
            }
            break;
        }
    }

    // Fallback: if more than 2 segments, return from index 2 onward
    if parts.len() > 2 {
        return parts[2..].join("-");
    }

    String::new()
}

/// Extract GitHub org and repo from a git remote URL.
///
/// Supports SSH (`git@github.com:org/repo.git`) and HTTPS
/// (`https://github.com/org/repo.git`) formats via manual string parsing.
pub fn parse_remote_url(url: &str) -> GitInfo {
    let url = url.trim();
    if url.is_empty() {
        return GitInfo::default();
    }

    // Try SSH format: git@<host>:<org>/<repo>[.git]
    if let Some(colon_pos) = url.find(':') {
        let before_colon = &url[..colon_pos];
        // SSH URLs start with something like "git@host"
        if before_colon.contains('@') && !before_colon.contains('/') {
            let path = &url[colon_pos + 1..];
            return parse_org_repo_from_path(path);
        }
    }

    // Try HTTPS format: https://<host>/<org>/<repo>[.git]
    if url.starts_with("http://") || url.starts_with("https://") {
        // Strip scheme and host: find the third '/'
        if let Some(scheme_end) = url.find("://") {
            let after_scheme = &url[scheme_end + 3..];
            if let Some(host_end) = after_scheme.find('/') {
                let path = &after_scheme[host_end + 1..];
                return parse_org_repo_from_path(path);
            }
        }
    }

    GitInfo::default()
}

/// Parse `org/repo[.git]` from a path string.
fn parse_org_repo_from_path(path: &str) -> GitInfo {
    let parts: Vec<&str> = path.splitn(3, '/').collect();
    if parts.len() >= 2 {
        let org = parts[0].to_string();
        let repo = parts[1].trim_end_matches(".git").to_string();
        if !org.is_empty() && !repo.is_empty() {
            return GitInfo {
                org: Some(org),
                repo: Some(repo),
            };
        }
    }
    GitInfo::default()
}

/// Get GitHub org/repo by running `git remote get-url origin` on the given cwd.
///
/// Results are cached in the provided `HashMap` to avoid repeated subprocess calls.
pub fn resolve_git_remote(cwd: &str, cache: &mut HashMap<String, GitInfo>) -> GitInfo {
    if let Some(cached) = cache.get(cwd) {
        return cached.clone();
    }

    let info = match Command::new("git")
        .args(["-C", cwd, "remote", "get-url", "origin"])
        .output()
    {
        Ok(output) if output.status.success() => {
            let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
            parse_remote_url(&stdout)
        }
        _ => GitInfo::default(),
    };

    cache.insert(cwd.to_string(), info.clone());
    info
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_derive_project_name_with_workspace() {
        assert_eq!(
            derive_project_name("abc-def-workspace-my-project"),
            "my-project"
        );
    }

    #[test]
    fn test_derive_project_name_fallback() {
        assert_eq!(derive_project_name("aa-bb-cc-dd"), "cc-dd");
    }

    #[test]
    fn test_derive_project_name_short() {
        assert_eq!(derive_project_name("aa-bb"), "");
    }

    #[test]
    fn test_derive_project_name_leading_dashes() {
        assert_eq!(
            derive_project_name("--abc-workspace-cool-tool"),
            "cool-tool"
        );
    }

    #[test]
    fn test_parse_remote_url_ssh() {
        let info = parse_remote_url("git@github.com:myorg/myrepo.git");
        assert_eq!(info.org.as_deref(), Some("myorg"));
        assert_eq!(info.repo.as_deref(), Some("myrepo"));
    }

    #[test]
    fn test_parse_remote_url_https() {
        let info = parse_remote_url("https://github.com/myorg/myrepo.git");
        assert_eq!(info.org.as_deref(), Some("myorg"));
        assert_eq!(info.repo.as_deref(), Some("myrepo"));
    }

    #[test]
    fn test_parse_remote_url_https_no_git_suffix() {
        let info = parse_remote_url("https://github.com/myorg/myrepo");
        assert_eq!(info.org.as_deref(), Some("myorg"));
        assert_eq!(info.repo.as_deref(), Some("myrepo"));
    }

    #[test]
    fn test_parse_remote_url_empty() {
        let info = parse_remote_url("");
        assert!(info.org.is_none());
        assert!(info.repo.is_none());
    }
}
