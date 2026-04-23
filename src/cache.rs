// STUB — to be implemented by subagent
use crate::models::{Activity, FileInfo};

pub struct CacheDB {
    pub conn: rusqlite::Connection,
}

impl CacheDB {
    pub fn new(_db_path: &str) -> Self { todo!() }
    pub fn mark_file_processed(&self, _path: &str, _mtime: f64) { todo!() }
    pub fn get_unprocessed_files(&self, _files: &[FileInfo]) -> Vec<FileInfo> { todo!() }
    pub fn store_activities(&self, _activities: &[Activity]) { todo!() }
    pub fn store_manual_entry(&self, _summary: &str, _classification: &str, _git_org: Option<&str>, _git_repo: Option<&str>) { todo!() }
    pub fn query_activities(&self, _date_from: &str, _date_to: &str, _orgs: Option<&[String]>, _repos: Option<&[String]>) -> Vec<Activity> { todo!() }
}
