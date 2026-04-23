use std::collections::BTreeMap;
use std::process::Command;

use crate::models::Activity;

/// Format a single activity line.
///
/// Pattern: `[classification](org/repo) summary`
/// - Skip classification prefix if empty.
/// - Skip org/repo if both are None.
fn format_activity_line(activity: &Activity, bullet: &str) -> String {
    let mut parts = Vec::new();

    if !activity.classification.is_empty() {
        parts.push(format!("[{}]", activity.classification));
    }

    let has_org = activity.git_org.as_ref().is_some_and(|s| !s.is_empty());
    let has_repo = activity.git_repo.as_ref().is_some_and(|s| !s.is_empty());

    if has_org || has_repo {
        let org = activity.git_org.as_deref().unwrap_or("");
        let repo = activity.git_repo.as_deref().unwrap_or("");
        parts.push(format!("({}/{})", org, repo));
    }

    parts.push(activity.summary.clone());

    format!("{} {}", bullet, parts.join(" "))
}

/// Generate a template-based standup report (no LLM).
///
/// Groups activities by day (sorted ascending). Renders in Markdown or Slack format.
pub fn generate_template_report(activities: &[Activity], format: &str, lang: &str) -> String {
    if activities.is_empty() {
        return match lang {
            "es" => "No se encontró actividad para el período solicitado.".to_string(),
            _ => "No activity found for the requested period.".to_string(),
        };
    }

    // Group activities by day using a BTreeMap for sorted keys.
    let mut by_day: BTreeMap<&str, Vec<&Activity>> = BTreeMap::new();
    for activity in activities {
        by_day.entry(activity.day.as_str()).or_default().push(activity);
    }

    let is_slack = format == "slack";
    let mut output = String::new();

    for (day, day_activities) in &by_day {
        // Day header
        if is_slack {
            output.push_str(&format!("*{}*\n\n", day));
        } else {
            output.push_str(&format!("## {}\n\n", day));
        }

        // "Done" section header
        if is_slack {
            output.push_str("*Done*\n");
        } else {
            output.push_str("### Done\n");
        }

        // Activity lines
        let bullet = if is_slack { "•" } else { "-" };
        for activity in day_activities {
            output.push_str(&format_activity_line(activity, bullet));
            output.push('\n');
        }

        output.push('\n');
    }

    output.trim_end().to_string()
}

/// Generate an LLM-powered standup report by calling `claude -p`.
///
/// Falls back to `generate_template_report` if `claude` is not available or fails.
pub fn generate_llm_report(activities: &[Activity], format: &str, lang: &str) -> String {
    if activities.is_empty() {
        return generate_template_report(activities, format, lang);
    }

    // Build raw activity text lines.
    let activities_text: String = activities
        .iter()
        .map(|a| {
            let has_org = a.git_org.as_ref().is_some_and(|s| !s.is_empty());
            let has_repo = a.git_repo.as_ref().is_some_and(|s| !s.is_empty());

            let repo_part = if has_org || has_repo {
                let org = a.git_org.as_deref().unwrap_or("");
                let repo = a.git_repo.as_deref().unwrap_or("");
                format!("({}/{})", org, repo)
            } else {
                String::new()
            };

            let class_part = if a.classification.is_empty() {
                String::new()
            } else {
                format!("[{}]", a.classification)
            };

            format!(
                "- [{}] {}{} {}",
                a.day,
                class_part,
                repo_part,
                a.summary
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    let lang_name = match lang {
        "es" => "Spanish",
        _ => "English",
    };

    let format_desc = if format == "slack" {
        "Slack (*bold*, • bullets)"
    } else {
        "Markdown (## headers, - bullets)"
    };

    let prompt = format!(
        r#"You are generating a daily standup report for a software developer.

Below is raw classified activity data from their Claude Code sessions. Your job:

1. Write a clean, concise activity summary. Group by project. Only what was done.
2. IGNORE noise and mechanical/routine tasks that add no value to a standup: subagent instructions, internal tool output, git sync/rebase/pull, branch cleanup, submodule updates, pushing pointers, adding reviewers, linting, formatting.
3. When there are multiple distinct deliverables (PRs, services, bugs), LIST EACH by name.
4. Keep bullets concise. One line per deliverable. No implementation details.
5. Include org/repo when available. No time estimates.
6. Multiple related fixes to the same component = one bullet summarizing the outcome.
7. Write in {lang}. Use {format} formatting.
8. Do NOT include "Next steps", "Blockers", or "TODO" sections.

Raw activity data:
{activities}"#,
        lang = lang_name,
        format = format_desc,
        activities = activities_text,
    );

    let result = Command::new("claude")
        .args(["-p", &prompt, "--output-format", "text", "--no-session-persistence"])
        .output();

    match result {
        Ok(output) if output.status.success() => {
            let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if text.is_empty() {
                generate_template_report(activities, format, lang)
            } else {
                text
            }
        }
        _ => generate_template_report(activities, format, lang),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::Activity;

    fn sample_activities() -> Vec<Activity> {
        vec![
            Activity {
                session_id: "s1".to_string(),
                day: "2026-04-20".to_string(),
                project: "api".to_string(),
                git_org: Some("acme".to_string()),
                git_repo: Some("api".to_string()),
                classification: "feature".to_string(),
                summary: "Add user endpoint".to_string(),
            },
            Activity {
                session_id: "s2".to_string(),
                day: "2026-04-20".to_string(),
                project: "api".to_string(),
                git_org: Some("acme".to_string()),
                git_repo: Some("api".to_string()),
                classification: "bugfix".to_string(),
                summary: "Fix auth middleware".to_string(),
            },
            Activity {
                session_id: "s3".to_string(),
                day: "2026-04-21".to_string(),
                project: "web".to_string(),
                git_org: None,
                git_repo: None,
                classification: "".to_string(),
                summary: "Update landing page".to_string(),
            },
        ]
    }

    #[test]
    fn test_empty_activities_en() {
        let result = generate_template_report(&[], "markdown", "en");
        assert_eq!(result, "No activity found for the requested period.");
    }

    #[test]
    fn test_empty_activities_es() {
        let result = generate_template_report(&[], "markdown", "es");
        assert_eq!(
            result,
            "No se encontró actividad para el período solicitado."
        );
    }

    #[test]
    fn test_markdown_format() {
        let activities = sample_activities();
        let result = generate_template_report(&activities, "markdown", "en");

        assert!(result.contains("## 2026-04-20"));
        assert!(result.contains("## 2026-04-21"));
        assert!(result.contains("### Done"));
        assert!(result.contains("- [feature] (acme/api) Add user endpoint"));
        assert!(result.contains("- [bugfix] (acme/api) Fix auth middleware"));
        assert!(result.contains("- Update landing page"));
    }

    #[test]
    fn test_slack_format() {
        let activities = sample_activities();
        let result = generate_template_report(&activities, "slack", "en");

        assert!(result.contains("*2026-04-20*"));
        assert!(result.contains("*2026-04-21*"));
        assert!(result.contains("*Done*"));
        assert!(result.contains("• [feature] (acme/api) Add user endpoint"));
        assert!(result.contains("• Update landing page"));
    }

    #[test]
    fn test_days_sorted() {
        let activities = vec![
            Activity {
                session_id: "s1".to_string(),
                day: "2026-04-21".to_string(),
                project: "b".to_string(),
                git_org: None,
                git_repo: None,
                classification: "".to_string(),
                summary: "Second day".to_string(),
            },
            Activity {
                session_id: "s2".to_string(),
                day: "2026-04-19".to_string(),
                project: "a".to_string(),
                git_org: None,
                git_repo: None,
                classification: "".to_string(),
                summary: "First day".to_string(),
            },
        ];
        let result = generate_template_report(&activities, "markdown", "en");
        let pos_19 = result.find("2026-04-19").unwrap();
        let pos_21 = result.find("2026-04-21").unwrap();
        assert!(pos_19 < pos_21, "Days should be sorted ascending");
    }

    #[test]
    fn test_llm_report_empty_fallback() {
        let result = generate_llm_report(&[], "markdown", "en");
        assert_eq!(result, "No activity found for the requested period.");
    }
}
