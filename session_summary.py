#!/usr/bin/env python3
"""Session summary logger for Obsidian.

Called by the SessionEnd hook wrapper. Reads a Claude Code transcript,
calls the Anthropic API (Opus 4.6 with adaptive thinking) to generate
a log entry, and writes/updates a dated markdown file in Obsidian.

Three-context prompt design:
  <full-session>    — entire transcript for full context
  <new-content>     — only the portion since the last summary
  <previous-log>    — existing log entries from the Obsidian file
"""

import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VAULT_DIR = Path.home() / "Documents" / "refuse_to_choose" / "Claude Sessions"
INDEX_FILE = VAULT_DIR / ".session-index"
LOG_FILE = Path.home() / ".claude" / "scripts" / "session-summary.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    format="[%(asctime)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def parse_transcript(path: str) -> list[dict]:
    """Parse JSONL transcript into a list of {role, text, timestamp} dicts."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") not in ("user", "assistant"):
                continue

            message = entry.get("message", {})
            role = message.get("role", entry["type"])
            content = message.get("content", "")
            timestamp = entry.get("timestamp", "")

            # String content
            if isinstance(content, str) and content.strip():
                entries.append({"role": role, "text": content.strip(), "ts": timestamp})
                continue

            # Array content — text blocks only
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text", "").strip()
                        if t:
                            texts.append(t)
                if texts:
                    entries.append({"role": role, "text": "\n".join(texts), "ts": timestamp})

    return entries


def format_conversation(entries: list[dict]) -> str:
    """Format transcript entries into readable text."""
    lines = []
    for e in entries:
        ts = ""
        if e["ts"]:
            try:
                dt = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                ts = f" ({dt.strftime('%H:%M')})"
            except (ValueError, TypeError):
                pass
        lines.append(f"[{e['role']}{ts}]: {e['text']}")
    return "\n\n".join(lines)


def read_index() -> dict:
    """Read session index: {session_id: {file: str, offset: int}}."""
    index = {}
    if not INDEX_FILE.exists():
        return index
    for line in INDEX_FILE.read_text().splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3:
            index[parts[0]] = {"file": parts[1], "offset": int(parts[2])}
    return index


def write_index(index: dict):
    """Write session index back to disk."""
    lines = []
    for sid, info in index.items():
        lines.append(f"{sid}|{info['file']}|{info['offset']}")
    INDEX_FILE.write_text("\n".join(lines) + "\n")


def get_api_key() -> str:
    """Get Anthropic API key from env or bashrc."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    bashrc = Path.home() / ".bashrc"
    if bashrc.exists():
        import re
        for line in bashrc.read_text().splitlines():
            m = re.search(r'ANTHROPIC_API_KEY=([^\s"\']+)', line)
            if m:
                key = m.group(1)
    return key


def call_api(system: str, user_content: str) -> str:
    """Call Anthropic Messages API with Opus 4.6 + adaptive thinking."""
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY found")

    payload = json.dumps({
        "model": "claude-opus-4-6",
        "max_tokens": 16000,
        "thinking": {"type": "adaptive"},
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())

    # Extract text blocks (skip thinking blocks)
    texts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            texts.append(block["text"])

    usage = data.get("usage", {})
    log.info(
        "API call: input=%s output=%s",
        usage.get("input_tokens", "?"),
        usage.get("output_tokens", "?"),
    )
    return "\n".join(texts)


SYSTEM_PROMPT = """\
You are a session logger for a developer's Obsidian knowledge base.

You receive three sections of context:
- <full-session>: The complete conversation transcript for background understanding
- <new-content>: Only the messages since the last log entry (or all, if first entry)
- <previous-log>: Existing log entries from prior exits of this same session

Your task: generate ONLY the new log entry for the current exit.

Output format for a NEW session (no previous log):
```
# <Descriptive Title>

*Session: `<session_id>` | Directory: `<cwd>`*

## <YYYY-MM-DD HH:MM>
- **Plan**: What the user set out to do
- **Done**: What was accomplished
- **Open**: Unfinished items, next steps (omit if nothing is open)
```

Output format for an UPDATE (previous log exists):
```
## <YYYY-MM-DD HH:MM>
- **Plan**: What the user set out to do in this segment
- **Done**: What was accomplished in this segment
- **Open**: Unfinished items, next steps (omit if nothing is open)
```

Rules:
- Be concise. Each bullet should be 1-2 lines max.
- Never repeat content from previous log entries.
- Focus on what changed: decisions, actions, outcomes.
- The title (first entry only) should describe the session's purpose, not be generic.
- Use the timestamps in the transcript to determine the log entry time.
- Output ONLY the markdown — no preamble, no explanation."""


def main():
    hook_input = json.loads(sys.stdin.read())
    transcript_path = hook_input.get("transcript_path", "")
    session_id = hook_input.get("session_id", "")
    cwd = hook_input.get("cwd", "")

    if not transcript_path or not session_id or not os.path.isfile(transcript_path):
        log.error("Missing transcript_path (%s) or session_id (%s)", transcript_path, session_id)
        return

    log.info("Processing session %s", session_id)

    # Parse transcript
    entries = parse_transcript(transcript_path)
    if len(entries) < 2:
        log.info("Session %s too short (%d entries)", session_id, len(entries))
        return

    # Check index for previous state
    index = read_index()
    prev = index.get(session_id)
    previous_log = ""
    existing_file = None
    offset = 0

    if prev:
        existing_file = VAULT_DIR / prev["file"]
        offset = prev["offset"]
        if existing_file.exists():
            # Read everything up to the metadata footer
            content = existing_file.read_text()
            # Strip the trailing metadata line
            lines = content.split("\n")
            while lines and (lines[-1].startswith("*Session:") or lines[-1] == "---" or lines[-1].strip() == ""):
                lines.pop()
            previous_log = "\n".join(lines)
            log.info("Found existing log with %d entries, offset %d", previous_log.count("## "), offset)

    # Build the three contexts
    full_text = format_conversation(entries)
    new_entries = entries[offset:] if offset > 0 else entries
    new_text = format_conversation(new_entries)

    # Truncate if needed (keep under ~150K chars for Opus context)
    if len(full_text) > 150000:
        full_text = full_text[:150000] + "\n\n[...truncated...]"
    if len(new_text) > 80000:
        new_text = new_text[:80000] + "\n\n[...truncated...]"

    # Build user prompt
    user_content = f"""<full-session>
{full_text}
</full-session>

<new-content>
{new_text}
</new-content>

<previous-log>
{previous_log if previous_log else "(First entry — no previous log)"}
</previous-log>

<metadata>
session_id: {session_id}
cwd: {cwd}
current_time: {datetime.now().strftime("%Y-%m-%d %H:%M")}
</metadata>

Generate the log entry."""

    # Call API
    result = call_api(SYSTEM_PROMPT, user_content)
    if not result.strip():
        log.error("Empty result from API")
        return

    # Determine output file
    if existing_file and existing_file.exists():
        # Append new entry to existing file
        content = existing_file.read_text()
        # Strip old metadata footer
        lines = content.rstrip().split("\n")
        while lines and (lines[-1].startswith("*Session:") or lines[-1] == "---" or lines[-1].strip() == ""):
            lines.pop()

        updated = "\n".join(lines) + "\n\n" + result.strip() + "\n"
        out_path = existing_file
    else:
        # New file with date-based name
        now = datetime.now()
        # Extract a slug from the title line
        first_line = result.strip().split("\n")[0]
        slug = first_line.lstrip("# ").strip()
        # Convert to kebab-case filename
        slug = slug.lower()
        for ch in ":/\\?*\"<>|'(),&.!":
            slug = slug.replace(ch, "")
        slug = "-".join(slug.split())[:60]

        filename = f"{now.strftime('%Y-%m-%d_%H%M')}_{slug}.md"
        out_path = VAULT_DIR / filename
        updated = result.strip() + "\n"

    # Add metadata footer
    footer = f"\n---\n*Session: `{session_id}` | Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"
    updated += footer

    out_path.write_text(updated)

    # Update index
    index[session_id] = {"file": out_path.name, "offset": len(entries)}
    write_index(index)

    log.info("Wrote %s (%d bytes, %d transcript entries)", out_path.name, len(updated), len(entries))


if __name__ == "__main__":
    main()
