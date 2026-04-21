# claude-standup v2 — Design Spec

## Overview

Rewrite of claude-standup with a background daemon architecture. The daemon continuously pre-processes and classifies Claude Code activity logs, making report generation instantaneous. Distributed as native OS packages built by GitHub Actions CI/CD.

## Problem (v1 Lessons)

v1 classified sessions synchronously at report time using `claude -p`. This was unacceptably slow (~2 min per session, 8+ min for a typical day with 58 sessions). The core issue: LLM classification is inherently slow and must happen asynchronously in the background, not blocking the user.

## Architecture

```
┌──────────────────────────────────┐
│      claude-standup daemon       │  Background service (launchd/systemd)
│                                  │
│  Every 60s:                      │
│  1. Discover new/modified JSONL  │  ← local I/O, ~4s for 1700 files
│  2. Parse → store sessions       │  ← local, instant
│  3. Classify pending sessions    │  ← claude -p, 1 at a time, ~2min each
│     (sequential, no rush)        │     runs forever until all done
│  4. Store activities in SQLite   │
└──────────────┬───────────────────┘
               │ SQLite (read-only)
┌──────────────▼───────────────────┐
│      claude-standup CLI          │  User-facing, instantaneous
│                                  │
│  • today / yesterday / last-7    │  ← reads pre-classified data
│  • log "manual entry"            │  ← direct write to SQLite
│  • daemon status                 │  ← check daemon health
│  • --template (no LLM)           │  ← instant local report
│  • default (1 LLM call)          │  ← ~15s for report generation
└──────────────────────────────────┘
```

## Components

### 1. Daemon (`claude_standup/daemon.py`)

A long-running background process that watches for new Claude Code logs and classifies them.

**Loop:**
```
while True:
    1. discover_jsonl_files(~/.claude/projects/)
    2. filter to unprocessed (compare mtime in cache)
    3. parse each new file → store sessions + raw_prompts in SQLite
    4. get_unclassified_sessions() → classify one at a time with claude -p
    5. sleep(60)
```

**Behavior:**
- Runs as current user (not root)
- PID file: `~/.claude-standup/daemon.pid`
- Log file: `~/.claude-standup/daemon.log` (rotated, max 10MB)
- Graceful shutdown on SIGTERM/SIGINT
- On startup: immediately runs one full cycle, then enters the 60s loop
- Classification: sequential (1 session at a time) — no rush, it's background
- If `claude` CLI is not available: log warning, skip classification, retry next cycle
- If a classification fails: log error, skip session, mark as failed, retry after 3 cycles

**macOS service:** `~/Library/LaunchAgents/com.claude-standup.daemon.plist`
- `RunAtLoad: true` (starts on login)
- `KeepAlive: true` (restarts if crashes)
- `StandardOutPath` / `StandardErrorPath` → daemon.log

**Linux service:** `~/.config/systemd/user/claude-standup.service`
- `WantedBy=default.target` (starts on login)
- `Restart=always`
- `RestartSec=10`

### 2. CLI (`claude_standup/cli.py`)

Refactored from v1. Report commands **never classify** — they only read from the cache.

**Commands:**

| Command | What it does | LLM calls |
|---------|-------------|-----------|
| `claude-standup today` | Report for today from cache | 1 (report generation) |
| `claude-standup yesterday` | Report for yesterday | 1 |
| `claude-standup last-7-days` | Last 7 days | 1 |
| `claude-standup today --template` | Report from template, no LLM | 0 |
| `claude-standup log "message"` | Manual activity entry | 0 |
| `claude-standup daemon status` | Show daemon status + stats | 0 |
| `claude-standup warmup` | Force immediate parse cycle | 0 |

**Flags (same as v1):**
- `--from YYYY-MM-DD` / `--to YYYY-MM-DD`
- `--org <name>[,<name>...]` / `--repo <name>[,<name>...]`
- `--lang es|en`
- `--format markdown|slack`
- `--output <file>`
- `--template` — use local template instead of LLM for report generation
- `--verbose`

**Report flow:**
1. Parse any new files since last daemon cycle (instant, local)
2. Read classified activities from SQLite for date range
3. If unclassified sessions exist for the range: show warning banner
4. Generate report: `--template` → local template, default → 1 `claude -p` call
5. Output to stdout + optional file

**Pending sessions warning:**
```
⚠ 3 sessions pending classification (daemon: running)

## 2026-04-21
### Done
- [FEATURE] Implemented login with OAuth2 (acme/my-app) ~45min
...
```

**`daemon status` output:**
```
Daemon: running (PID 12345)
Uptime: 2h 34m
Sessions: 245 classified, 3 pending
Last cycle: 30s ago
Next cycle: in 30s
Log: ~/.claude-standup/daemon.log
```

### 3. Template Reporter

Local report generation without LLM. Instant, deterministic.

**Markdown output:**
```markdown
## 2026-04-21

### Done
- [FEATURE](acme/my-app) Implemented login with OAuth2 ~45min
- [BUGFIX](acme/my-app) Fixed session expiration ~20min
- [MEETING] Sprint planning with backend team
- [SUPPORT](acme/infra) Helped team debug deploy pipeline

### Pending classification
- 3 sessions (daemon running, ETA ~6min)
```

**Slack output:**
```
*2026-04-21*

*Done*
• [FEATURE](acme/my-app) Implemented login with OAuth2 ~45min
• [BUGFIX](acme/my-app) Fixed session expiration ~20min
• [MEETING] Sprint planning with backend team

*Pending*
• 3 sessions being classified
```

No "Today" / "Blockers" inference — that requires LLM. The template mode gives you a clean activity list instantly.

### 4. Installer (`installer/`)

**Build artifacts (GitHub Actions):**

| Platform | Format | Contents |
|----------|--------|----------|
| macOS (arm64) | `.pkg` | CLI binary + launchd plist |
| macOS (x86_64) | `.pkg` | CLI binary + launchd plist |
| Linux (amd64) | `.deb` | CLI binary + systemd service |
| Linux (amd64) | `.rpm` | CLI binary + systemd service |
| Homebrew | Formula | Points to GitHub Release tarball |

**Standalone binary:** PyInstaller or Nuitka to compile Python to a single native binary. No Python dependency for the end user.

**macOS .pkg (`installer/macos/`):**
- Preinstall script: stop existing daemon if running
- Install: copy `claude-standup` to `/usr/local/bin/`
- Postinstall script: install launchd plist → `launchctl load` → daemon starts
- Uninstall: `claude-standup daemon uninstall` removes plist + stops daemon

**Linux .deb (`installer/linux/`):**
- `debian/control`: metadata, depends on nothing (standalone binary)
- `debian/postinst`: enable + start systemd user service
- `debian/prerm`: stop + disable systemd user service
- Binary at `/usr/local/bin/claude-standup`
- Service at `/usr/lib/systemd/user/claude-standup.service`

**Homebrew formula (`installer/homebrew/`):**
```ruby
class ClaudeStandup < Formula
  desc "Daily standup reports from Claude Code activity logs"
  homepage "https://github.com/theburrowhub/claude-standup"
  url "https://github.com/theburrowhub/claude-standup/releases/download/v2.0.0/claude-standup-2.0.0.tar.gz"
  sha256 "..."

  def install
    bin.install "claude-standup"
  end

  service do
    run [opt_bin/"claude-standup", "daemon", "run"]
    keep_alive true
    log_path var/"log/claude-standup.log"
  end
end
```

### 5. GitHub Actions CI/CD (`.github/workflows/`)

**Trigger:** push to `main` with tag `v*`

**Jobs:**

1. **test** — run pytest on Python source
2. **build-macos** — PyInstaller on macOS runner → `.pkg` (arm64 + x86_64)
3. **build-linux** — PyInstaller on Ubuntu runner → `.deb` + `.rpm`
4. **release** — create GitHub Release, upload all artifacts
5. **homebrew** — update Homebrew formula with new SHA + URL

**Matrix:**
```yaml
strategy:
  matrix:
    include:
      - os: macos-latest
        arch: arm64
      - os: macos-13
        arch: x86_64
      - os: ubuntu-latest
        arch: amd64
```

## Reused from v1

All these modules are kept as-is (with minor interface adjustments):

| Module | Status |
|--------|--------|
| `parser.py` | As-is. 1700 files in 4s. |
| `cache.py` | As-is. SQLite with raw_prompts, sessions, activities, file tracking. |
| `models.py` | As-is. FileInfo, GitInfo, LogEntry, Activity dataclasses. |
| `classifier.py` | As-is. classify_session with LLMBackend. |
| `llm.py` | As-is. ClaudeCLIBackend + AnthropicSDKBackend. |
| `tests/fixtures/` | As-is. All JSONL test files. |
| All existing tests | As-is + new tests for daemon, template, installer. |

## New/Modified Files

| File | What |
|------|------|
| `claude_standup/daemon.py` | Background daemon: loop, PID management, signal handling, logging |
| `claude_standup/reporter.py` | Add `generate_template_report()` for instant local reports |
| `claude_standup/cli.py` | Add `daemon` subcommand, `--template` flag, remove sync classification |
| `installer/macos/build.sh` | PyInstaller + pkgbuild for macOS |
| `installer/macos/com.claude-standup.daemon.plist` | launchd service definition |
| `installer/linux/build.sh` | PyInstaller + dpkg-deb for Linux |
| `installer/linux/claude-standup.service` | systemd user service definition |
| `installer/homebrew/claude-standup.rb` | Homebrew formula |
| `.github/workflows/release.yml` | CI/CD pipeline: test → build → release |
| `tests/test_daemon.py` | Daemon unit tests |
| `tests/test_template_reporter.py` | Template reporter tests |

## SQLite Schema (unchanged from v1)

```sql
CREATE TABLE files (path TEXT PRIMARY KEY, mtime REAL, processed_at TEXT);
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project TEXT, git_org TEXT, git_repo TEXT, first_ts TEXT, last_ts TEXT, classified INTEGER DEFAULT 0);
CREATE TABLE raw_prompts (id INTEGER PRIMARY KEY, session_id TEXT, timestamp TEXT, content TEXT);
CREATE TABLE activities (id INTEGER PRIMARY KEY, session_id TEXT, day TEXT, project TEXT, git_org TEXT, git_repo TEXT, classification TEXT, summary TEXT, files_mentioned TEXT, technologies TEXT, time_spent_minutes INTEGER, raw_prompts TEXT);
```

## Dependencies

**Runtime:** None beyond Python stdlib + `anthropic` SDK (bundled in standalone binary)

**Build:** PyInstaller, pkgbuild (macOS), dpkg-deb (Linux)

**CI:** GitHub Actions runners (macos-latest, macos-13, ubuntu-latest)

## User Experience

```bash
# Install (macOS)
brew tap theburrowhub/homebrew-tools
brew install claude-standup
# Daemon starts automatically on install

# Or download .pkg from GitHub Releases and double-click

# Daily use
claude-standup today                    # Instant report (1 LLM call for text)
claude-standup today --template         # Instant report (no LLM, pure template)
claude-standup yesterday --format slack # Slack format
claude-standup log "Sprint planning" --type MEETING
claude-standup daemon status            # Check daemon health

# Install (Linux)
sudo dpkg -i claude-standup_2.0.0_amd64.deb
# Daemon starts automatically

claude-standup today
```

## Testing Strategy

Same as v1 (pytest + pytest-cov) plus:

**test_daemon.py:**
- test_daemon_loop_discovers_files
- test_daemon_loop_classifies_pending
- test_daemon_graceful_shutdown
- test_daemon_skips_when_no_claude
- test_daemon_retries_failed_classification
- test_pid_file_management

**test_template_reporter.py:**
- test_markdown_template_format
- test_slack_template_format
- test_pending_sessions_warning
- test_empty_activities
- test_mixed_classified_and_pending

Coverage target: 100% on daemon.py, reporter.py, cli.py (all pure logic).
