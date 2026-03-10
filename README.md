# claude-session-logger

Auto-logs Claude Code sessions to Obsidian as structured markdown files.

Uses the `SessionEnd` hook to trigger after each session exit. Calls the Anthropic API (Opus 4.6 with extended thinking) to generate concise Plan/Done/Open log entries. Resumed sessions append new entries to the same file rather than creating duplicates.

## How it works

1. Claude Code fires the `SessionEnd` hook on exit
2. `session-summary.sh` reads the hook input and backgrounds the Python script
3. `session_summary.py` parses the JSONL transcript and builds a three-context prompt:
   - `<full-session>` — entire transcript for context
   - `<new-content>` — only messages since the last log entry
   - `<previous-log>` — existing log entries from the Obsidian file
4. Opus generates a timestamped log entry (not a full re-summary)
5. The entry is appended to a date-named file in Obsidian: `YYYY-MM-DD_HHMM_title.md`
6. A `.session-index` file tracks session_id → filename + transcript offset

## Output format

```markdown
# Descriptive Session Title

Rolling summary paragraph updated each session exit...

---

## 2026-03-10 14:30
- **Plan**: What the user intended to do
- **Done**: What was accomplished
- **Open**: Unfinished items or next steps

## 2026-03-10 16:45
- **Plan**: What was tackled after resuming
- **Done**: Additional progress

---
*Session: `abc123` | Updated: 2026-03-10 16:45 | Host: xenolaptop (Linux x86_64)*
```

The footer includes the **hostname and OS/architecture** so you can tell which machine a session was run on (e.g. `xenolaptop (Linux x86_64)` vs `macbook (Darwin arm64)`).

## Requirements

- Python 3.8+
- `ANTHROPIC_API_KEY` in environment or `~/.bashrc`
- Claude Code 2.1.0+ (SessionEnd hooks)

## Install

```bash
git clone https://github.com/<you>/claude-session-logger.git ~/.claude/scripts/claude-session-logger
cd ~/.claude/scripts/claude-session-logger
./install.sh
```

The installer:
- Creates the output directory (`~/Documents/refuse_to_choose/Claude Sessions/` by default)
- Registers the `SessionEnd` hook in `~/.claude/settings.json`

Start a new Claude Code session for the hook to load. Every session exit after that will generate a summary.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required. Your Anthropic API key |
| `OBSIDIAN_VAULT_DIR` | `~/Documents/refuse_to_choose` | Override the Obsidian vault root (install.sh only) |

To change the output directory after install, edit `VAULT_DIR` in `session_summary.py`.

## Debugging

Logs are written to `~/.claude/scripts/session-summary.log`. Check there if summaries aren't appearing.

Manual test:
```bash
echo '{"transcript_path":"<path-to-transcript.jsonl>","session_id":"test","cwd":"/home/user"}' | bash ~/.claude/scripts/claude-session-logger/session-summary.sh
sleep 30  # Opus needs ~15-20s
cat ~/Documents/refuse_to_choose/Claude\ Sessions/*.md
```
