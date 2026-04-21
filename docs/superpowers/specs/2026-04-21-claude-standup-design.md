# claude-standup — Design Spec

## Overview

CLI tool that parses Claude Code activity logs (`~/.claude/projects/**/*.jsonl`) and generates daily standup reports using the Claude API for classification and report generation.

## Problem

Claude Code generates structured JSONL logs for every session across all projects. These logs contain rich information about development activity but are unreadable in raw form. Developers need a way to extract meaningful daily work summaries from this data.

## Architecture

```
~/.claude/projects/**/*.jsonl
         │
         ▼
   ┌──────────┐     ┌───────────┐     ┌──────────────┐
   │ parser.py │────▶│ cache.py  │────▶│ classifier.py│
   │           │     │ (SQLite)  │     │ (Claude API) │
   └──────────┘     └───────────┘     └──────────────┘
                                             │
                                             ▼
                                      ┌─────────────┐     ┌──────────────┐
                                      │ reporter.py  │────▶│   stdout /   │
                                      │ (Claude API) │     │   file.md /  │
                                      └─────────────┘     │   slack fmt  │
                                             ▲            └──────────────┘
                                             │
                                      ┌─────────────┐
                                      │   cli.py     │
                                      └─────────────┘
```

## Data Source

### JSONL Log Structure

Files located at `~/.claude/projects/<project-name>/<session-uuid>.jsonl`.
Subagent logs at `<session-uuid>/subagents/agent-*.jsonl`.

Each line is a JSON object with a `type` field:

| Type | Relevant | Description |
|------|----------|-------------|
| `user` | Yes | User prompts. `message.content` is string (real prompt) or array (tool_result — skip) |
| `assistant` | Yes | Response. `message.content` is array of blocks: `text` (extract), `thinking` (skip), `tool_use` (extract tool name only) |
| `queue-operation` | No | Internal queue management |
| `attachment` | No | Deferred tools metadata |

### Key fields per entry

- `timestamp` — ISO 8601
- `sessionId` — UUID linking entries to a session
- `cwd` — working directory (reveals project path)
- `gitBranch` — current git branch
- `type` — entry type
- `message.content` — the actual content

### Data volume

As of 2026-04-21: 1,642 files, 707 MB, 49 projects. Growing daily.

## Modules

### `parser.py` — Log Reader

Responsibilities:
- Discover all JSONL files under `~/.claude/projects/`
- Parse each line safely (skip malformed JSON)
- Extract relevant entries only:
  - `type=user` where `message.content` is a string (real user prompts, not tool results)
  - `type=assistant` where `message.content` contains `text` blocks (for context on what was done)
  - `type=assistant` where `message.content` contains `tool_use` blocks (extract tool name for activity signal)
- Skip: `queue-operation`, `attachment`, `thinking` blocks, `tool_result` content
- Return normalized records: `(timestamp, session_id, project_name, entry_type, content, cwd, git_branch)`
- Derive `project_name` from the directory name (strip leading dash, convert dashes to readable form)
- Resolve git org/repo from `cwd` by running `git -C <cwd> remote get-url origin` (cached per unique cwd path, not per session)
- Parse remote URL to extract `org` and `repo` (supports both `git@github.com:org/repo.git` and `https://github.com/org/repo.git`)
- Track file mtime for incremental processing

### `cache.py` — SQLite Cache

Database location: `~/.claude-standup/cache.db`

Schema:

```sql
CREATE TABLE files (
    path TEXT PRIMARY KEY,
    mtime REAL,
    processed_at TEXT
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT,
    git_org TEXT,           -- GitHub organization (e.g. "freepik-company")
    git_repo TEXT,          -- repository name (e.g. "ai-gateway")
    first_ts TEXT,
    last_ts TEXT
);

CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    day TEXT,              -- YYYY-MM-DD
    project TEXT,
    classification TEXT,   -- FEATURE, BUGFIX, REFACTOR, DEBUGGING, EXPLORATION, REVIEW, SUPPORT, MEETING, OTHER
    summary TEXT,          -- one-line description of the activity
    files_mentioned TEXT,  -- JSON array of file paths
    technologies TEXT,     -- JSON array of tech names
    time_spent_minutes INTEGER,  -- estimated from timestamps
    raw_prompts TEXT,      -- JSON array of original user prompts (for report generation)
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
```

Behavior:
- On each run, scan `~/.claude/projects/` for JSONL files
- Compare mtime against `files` table
- Only parse files that are new or modified since last processing
- After classification, mark file as processed

### `classifier.py` — Claude API Classification

Uses the Anthropic Python SDK with the default model.

Input: batch of user prompts + assistant text responses from a single session.

Classification categories:
- `FEATURE` — building new functionality
- `BUGFIX` — fixing broken behavior
- `REFACTOR` — restructuring without changing behavior
- `DEBUGGING` — investigating issues, reading logs, tracing errors
- `EXPLORATION` — reading code, understanding codebase, research
- `REVIEW` — code review, PR review
- `SUPPORT` — helping teammates, answering questions, pair programming
- `MEETING` — meetings, syncs, planning sessions
- `OTHER` — anything that doesn't fit above

The classifier sends a single API call per session with all prompts, asking Claude to:
1. Group related prompts into logical activities (merge sequences about the same task)
2. Classify each activity
3. Extract mentioned files and technologies
4. Generate a one-line summary per activity
5. Estimate time spent per activity (from timestamp gaps)

Noise filtering: the classifier prompt instructs Claude to ignore trivial prompts (single-word confirmations, retries, "yes", "continue", etc.) and merge them into the parent activity.

API call structure:
```python
client.messages.create(
    model="claude-opus-4-6-20250414",
    max_tokens=4096,
    system="You are a development activity classifier...",
    messages=[{"role": "user", "content": session_data_json}]
)
```

Response format: structured JSON with activities array.

Rate limiting: process sessions sequentially with no artificial delay (API handles rate limits). If rate-limited, retry with exponential backoff.

### `reporter.py` — Report Generator

Two-phase process:

**Phase 1: Aggregate** — query cached activities for the requested date range, grouped by day and project.

**Phase 2: Generate** — send aggregated activities to Claude API, asking it to produce the standup report.

The reporter prompt includes:
- All classified activities for the date range
- Instruction to write in the requested language (es/en)
- Instruction to infer "next steps" from incomplete work and recent patterns
- Instruction to identify blockers from repeated failures, confusion, or long debugging sessions

Output formats:

**Markdown** (default):
```markdown
## 2026-04-21

### Yesterday
- Implemented X for project-name
- Debugged Y in project-name

### Today
- Continue X
- Finish Y

### Blockers
- None identified
```

**Slack**:
```
*2026-04-21*

*Yesterday*
• Implemented X for project-name
• Debugged Y in project-name

*Today*
• Continue X
• Finish Y

*Blockers*
• None identified
```

**File output**: write to specified path with `.md` extension.

### `cli.py` — Command Line Interface

Uses `argparse`.

Commands (positional argument):
- `today` — report for today
- `yesterday` — report for yesterday
- `last-7-days` — report for the last 7 days
- `log "<description>"` — register a manual activity (non-Claude work)
- (default if no command) — `today`

Flags (report commands):
- `--from YYYY-MM-DD` — start date (overrides command)
- `--to YYYY-MM-DD` — end date (overrides command)
- `--org <name>[,<name>...]` — filter by GitHub organization(s), comma-separated (e.g. `--org freepik-company,theburrowhub`)
- `--repo <name>[,<name>...]` — filter by repository name(s), comma-separated (e.g. `--repo ai-gateway,bot-data`)
- `--lang es|en` — report language (default: `es`)
- `--format markdown|slack` — output format (default: `markdown`)
- `--output <file>` — write report to file (in addition to stdout)
- `--reprocess` — ignore cache and reprocess all logs
- `--verbose` — show processing progress

Flags (log command):
- `--type FEATURE|BUGFIX|REFACTOR|DEBUGGING|EXPLORATION|REVIEW|SUPPORT|MEETING|OTHER` — activity type (default: `OTHER`)
- `--org <name>` — associate with an organization
- `--repo <name>` — associate with a repository

Entry point: `claude-standup` (via pyproject.toml console_scripts).

CLI flow:
1. Parse arguments, resolve date range
2. Call parser to discover and read new/modified JSONL files
3. Call cache to check what needs processing
4. Call classifier for unprocessed sessions
5. Call reporter to generate the standup
6. Output to stdout and/or file

## Dependencies

Runtime:
- `anthropic` — Anthropic Python SDK (only external runtime dependency)
- `sqlite3` — stdlib
- `argparse` — stdlib
- `json` — stdlib
- `pathlib` — stdlib
- `datetime` — stdlib

Dev/Test:
- `pytest` — test framework
- `pytest-cov` — coverage reporting

## Project Structure

```
claude-standup/
├── claude_standup/
│   ├── __init__.py
│   ├── cli.py
│   ├── parser.py
│   ├── cache.py
│   ├── classifier.py
│   └── reporter.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── valid_session.jsonl
│   │   ├── malformed_lines.jsonl
│   │   ├── empty_session.jsonl
│   │   ├── multi_session.jsonl
│   │   ├── tool_heavy_session.jsonl
│   │   └── manual_entries.jsonl
│   ├── test_parser.py
│   ├── test_cache.py
│   ├── test_classifier.py
│   ├── test_reporter.py
│   ├── test_cli.py
│   └── test_integration.py
├── pyproject.toml
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-21-claude-standup-design.md
```

## Installation

```bash
cd claude-standup
pip install -e .
```

## Manual Activity Logging

The `log` command allows registering activities that happen outside of Claude Code sessions.

```bash
# Log a support activity
claude-standup log "Helped mobile team debug their deploy pipeline" --type SUPPORT --org overmind-swarm --repo bot-data

# Log a meeting
claude-standup log "Sprint planning with backend team" --type MEETING

# Log without type (defaults to OTHER)
claude-standup log "Reviewed architecture docs for new auth system"
```

Manual entries are stored in the `activities` table with `session_id = "manual"` and timestamped at the moment of insertion. They are mixed chronologically with Claude-derived activities in reports — no special treatment.

## Usage Examples

```bash
# Daily standup for today
claude-standup today

# Yesterday's report in English
claude-standup yesterday --lang en

# Last week, Slack format
claude-standup last-7-days --format slack

# Custom range, filter by org, save to file
claude-standup --from 2026-04-14 --to 2026-04-21 --org overmind-swarm --output standup.md

# Multiple orgs
claude-standup last-7-days --org freepik-company,theburrowhub

# Specific repo
claude-standup today --repo ai-gateway,bot-data

# Reprocess all logs (ignore cache)
claude-standup --reprocess
```

## Edge Cases

- **Empty date range**: print "No activity found for this period"
- **API key not set**: clear error message with instructions to set `ANTHROPIC_API_KEY`
- **Malformed JSONL lines**: skip silently, log count of skipped lines in verbose mode
- **Very large sessions**: truncate prompt list to fit API context window (keep first and last prompts, summarize middle)
- **No internet**: fail gracefully with cached data if available, error if no cache exists

## Testing Strategy

Framework: **pytest** with `pytest-cov` for coverage.

### Design for Testability

Every module is designed so its dependencies can be injected or overridden:

- **`parser.py`** — takes a base path as argument (not hardcoded `~/.claude`). All file I/O through a discoverable path, no globals.
- **`cache.py`** — takes a database path as argument. Tests use `:memory:` or `tmp_path` SQLite databases. Exposes a `CacheDB` class, not module-level functions.
- **`classifier.py`** — takes an `anthropic.Anthropic` client as argument. Tests inject a mock client. The classifier is a class or function that receives the client, never instantiates it internally.
- **`reporter.py`** — same pattern as classifier: receives client as argument. Report formatting functions are pure (input data → output string) and tested independently from the API call.
- **`cli.py`** — uses a `main(argv=None)` signature so tests can pass synthetic argument lists. stdout capture via `capsys` or `StringIO`.

### Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── fixtures/                # Sample JSONL data files
│   ├── valid_session.jsonl
│   ├── malformed_lines.jsonl
│   ├── empty_session.jsonl
│   ├── multi_session.jsonl
│   ├── tool_heavy_session.jsonl
│   └── manual_entries.jsonl
├── test_parser.py
├── test_cache.py
├── test_classifier.py
├── test_reporter.py
├── test_cli.py
└── test_integration.py
```

### Fixtures (`conftest.py`)

```python
@pytest.fixture
def sample_jsonl(tmp_path):
    """Creates a temporary directory structure mimicking ~/.claude/projects/ with sample JSONL files."""

@pytest.fixture
def cache_db(tmp_path):
    """Returns a CacheDB instance backed by a temporary SQLite database."""

@pytest.fixture
def mock_anthropic_client():
    """Returns a mock anthropic.Anthropic client with pre-configured responses."""

@pytest.fixture
def classified_activities():
    """Returns a list of pre-classified Activity objects for reporter tests."""
```

### Test Coverage by Module

#### `test_parser.py`

| Test | What it verifies |
|------|-----------------|
| `test_discover_jsonl_files` | Finds all `.jsonl` files recursively, ignores other files |
| `test_parse_valid_session` | Extracts user prompts and assistant text from well-formed JSONL |
| `test_skip_malformed_lines` | Malformed JSON lines are skipped without crashing, count reported |
| `test_skip_queue_operations` | `type=queue-operation` entries are ignored |
| `test_skip_attachments` | `type=attachment` entries are ignored |
| `test_skip_tool_results` | User entries with `toolUseResult` / array content are skipped |
| `test_skip_thinking_blocks` | Assistant `thinking` blocks are excluded from content |
| `test_extract_tool_use_names` | Tool names from `tool_use` blocks are captured |
| `test_extract_metadata` | timestamp, sessionId, cwd, gitBranch correctly extracted |
| `test_derive_project_name` | Directory name converted to readable project name |
| `test_resolve_git_org_repo` | Parses SSH and HTTPS remote URLs into org/repo |
| `test_resolve_git_org_repo_no_remote` | Handles directories without git remote gracefully (org/repo = None) |
| `test_resolve_git_org_repo_cached` | Same cwd path only calls git once |
| `test_empty_file` | Empty JSONL file produces no entries |
| `test_file_mtime_tracking` | Returns mtime alongside file path for cache comparison |

#### `test_cache.py`

| Test | What it verifies |
|------|-----------------|
| `test_create_schema` | Database tables created on first init |
| `test_mark_file_processed` | File path + mtime stored correctly |
| `test_detect_new_files` | Files not in DB are flagged for processing |
| `test_detect_modified_files` | Files with changed mtime are flagged for reprocessing |
| `test_skip_unchanged_files` | Files with same mtime are not reprocessed |
| `test_store_session` | Session metadata (project, org, repo, timestamps) stored |
| `test_store_activities` | Activities with all fields stored and retrievable |
| `test_query_by_date_range` | Activities filtered by day column |
| `test_query_by_org` | Activities filtered by git_org (single and multiple values) |
| `test_query_by_repo` | Activities filtered by git_repo (single and multiple values) |
| `test_query_by_org_and_repo` | Combined org + repo filtering |
| `test_store_manual_entry` | Manual activities stored with session_id="manual" |
| `test_reprocess_clears_file_tracking` | `--reprocess` flag clears files table |
| `test_concurrent_access` | Two CacheDB instances on same file don't corrupt data |

#### `test_classifier.py`

| Test | What it verifies |
|------|-----------------|
| `test_classify_feature_session` | Session about building new functionality classified as FEATURE |
| `test_classify_bugfix_session` | Debugging + fix session classified as BUGFIX |
| `test_classify_review_session` | PR review session classified as REVIEW |
| `test_classify_mixed_session` | Session with multiple activities produces multiple classifications |
| `test_noise_filtering` | Short confirmations ("yes", "continue") are excluded |
| `test_merge_related_prompts` | Sequential prompts about same task merged into one activity |
| `test_extract_files` | File paths mentioned in prompts are captured |
| `test_extract_technologies` | Technology names detected from content |
| `test_time_estimation` | Time spent estimated from timestamp gaps |
| `test_api_error_retry` | Rate limit errors trigger exponential backoff retry |
| `test_api_error_fatal` | Non-retryable errors raise clean exception |
| `test_large_session_truncation` | Sessions exceeding context window are truncated safely |
| `test_structured_response_parsing` | Claude's JSON response parsed into Activity objects |
| `test_malformed_api_response` | Graceful handling if Claude returns unexpected format |

#### `test_reporter.py`

| Test | What it verifies |
|------|-----------------|
| `test_markdown_format` | Output matches expected Markdown structure (headers, bullets) |
| `test_slack_format` | Output uses Slack formatting (*bold*, bullet •) |
| `test_language_es` | Report generated in Spanish |
| `test_language_en` | Report generated in English |
| `test_single_day_report` | Report for one day has correct Yesterday/Today/Blockers |
| `test_multi_day_report` | Multi-day range produces per-day sections |
| `test_includes_manual_entries` | Manual log entries appear in report alongside Claude activities |
| `test_empty_period` | No activities produces "No activity found" message |
| `test_file_output` | Report written to file when --output specified |
| `test_stdout_and_file` | Report goes to both stdout and file simultaneously |
| `test_activities_grouped_by_project` | Activities grouped by org/repo within each day |
| `test_most_active_project_highlighted` | Most active project per day is identified |

#### `test_cli.py`

| Test | What it verifies |
|------|-----------------|
| `test_today_command` | `today` resolves to current date range |
| `test_yesterday_command` | `yesterday` resolves to previous day |
| `test_last_7_days_command` | `last-7-days` resolves to correct 7-day range |
| `test_custom_date_range` | `--from` and `--to` override command dates |
| `test_default_command` | No command defaults to `today` |
| `test_log_command` | `log "message"` stores manual activity |
| `test_log_with_type` | `--type SUPPORT` sets classification |
| `test_log_with_org_repo` | `--org` and `--repo` set metadata |
| `test_org_filter_multiple` | `--org a,b` parsed into list |
| `test_repo_filter_multiple` | `--repo a,b` parsed into list |
| `test_format_flag` | `--format slack` selects Slack output |
| `test_lang_flag` | `--lang en` selects English |
| `test_output_flag` | `--output file.md` writes to file |
| `test_reprocess_flag` | `--reprocess` clears cache before run |
| `test_missing_api_key` | Clear error when ANTHROPIC_API_KEY not set |
| `test_verbose_flag` | `--verbose` enables progress output |

#### `test_integration.py`

End-to-end tests using fixture JSONL files, in-memory SQLite, and mocked Anthropic client:

| Test | What it verifies |
|------|-----------------|
| `test_full_pipeline_today` | parse → cache → classify → report produces valid Markdown |
| `test_full_pipeline_with_manual_entries` | Manual + Claude activities merged in final report |
| `test_full_pipeline_org_filter` | Org filter applied through entire pipeline |
| `test_full_pipeline_reprocess` | Reprocess flag triggers full re-parse and re-classify |
| `test_incremental_processing` | Second run only processes new/modified files |
| `test_empty_logs_directory` | Graceful output when no JSONL files exist |

### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=claude_standup --cov-report=term-missing

# Specific module
pytest tests/test_parser.py -v
```

### Coverage Target

100% line coverage on `parser.py`, `cache.py`, `cli.py` (pure logic, no excuses).
90%+ on `classifier.py` and `reporter.py` (API interaction boundaries mocked).
