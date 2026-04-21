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
    first_ts TEXT,
    last_ts TEXT
);

CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    day TEXT,              -- YYYY-MM-DD
    project TEXT,
    classification TEXT,   -- FEATURE, BUGFIX, REFACTOR, DEBUGGING, EXPLORATION, REVIEW, OTHER
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
- (default if no command) — `today`

Flags:
- `--from YYYY-MM-DD` — start date (overrides command)
- `--to YYYY-MM-DD` — end date (overrides command)
- `--project <name>` — filter by project (substring match against project directory name)
- `--lang es|en` — report language (default: `es`)
- `--format markdown|slack` — output format (default: `markdown`)
- `--output <file>` — write report to file (in addition to stdout)
- `--reprocess` — ignore cache and reprocess all logs
- `--verbose` — show processing progress

Entry point: `claude-standup` (via pyproject.toml console_scripts).

CLI flow:
1. Parse arguments, resolve date range
2. Call parser to discover and read new/modified JSONL files
3. Call cache to check what needs processing
4. Call classifier for unprocessed sessions
5. Call reporter to generate the standup
6. Output to stdout and/or file

## Dependencies

- `anthropic` — Anthropic Python SDK (only external dependency)
- `sqlite3` — stdlib
- `argparse` — stdlib
- `json` — stdlib
- `pathlib` — stdlib
- `datetime` — stdlib

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

## Usage Examples

```bash
# Daily standup for today
claude-standup today

# Yesterday's report in English
claude-standup yesterday --lang en

# Last week, Slack format
claude-standup last-7-days --format slack

# Custom range, specific project, save to file
claude-standup --from 2026-04-14 --to 2026-04-21 --project overmind --output standup.md

# Reprocess all logs (ignore cache)
claude-standup --reprocess
```

## Edge Cases

- **Empty date range**: print "No activity found for this period"
- **API key not set**: clear error message with instructions to set `ANTHROPIC_API_KEY`
- **Malformed JSONL lines**: skip silently, log count of skipped lines in verbose mode
- **Very large sessions**: truncate prompt list to fit API context window (keep first and last prompts, summarize middle)
- **No internet**: fail gracefully with cached data if available, error if no cache exists
