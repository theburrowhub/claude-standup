// STUB — to be implemented by subagent
use crate::models::{FileInfo, GitInfo, SessionSummary};

pub fn discover_jsonl_files(_base_path: &str) -> Vec<FileInfo> {
    todo!()
}

pub fn parse_session_summaries(_file_path: &str, _project_name: &str) -> Vec<SessionSummary> {
    todo!()
}

pub fn derive_project_name(_dir_name: &str) -> String {
    todo!()
}

pub fn parse_remote_url(_url: &str) -> GitInfo {
    todo!()
}

pub fn resolve_git_remote(_cwd: &str, _cache: &mut std::collections::HashMap<String, GitInfo>) -> GitInfo {
    todo!()
}
