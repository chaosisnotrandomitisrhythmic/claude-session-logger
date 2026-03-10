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

You produce two distinct outputs wrapped in XML tags:

<summary>
A concise overview of the entire session so far (2-5 sentences). This is regenerated
each time to reflect the full arc of work. It answers: what was this session about,
what was the outcome, what's the current state? Write it as a paragraph, not bullets.
</summary>

<log_entry>
A single timestamped log entry covering ONLY what happened since the last entry.
Focus on decisions, actions, and outcomes. Use this format:

## YYYY-MM-DD HH:MM
- **Plan**: What the user set out to do in this segment
- **Done**: What was accomplished
- **Open**: Unfinished items or next steps (omit if nothing is open)
</log_entry>

You receive three context sections (long context first, instructions last per best practices):
- <full-session>: Complete conversation transcript — use this to write the summary
- <new-content>: Only messages since the last log entry — use this to write the log entry
- <previous-log>: Existing log entries from prior exits — avoid repeating their content

Additional rules:
- Each bullet: 1-2 lines max. Be specific about what changed.
- The summary reflects the whole session. The log entry covers only the new segment.
- On first entry, also include a descriptive title line: # Title
- Output ONLY the two XML-tagged sections, nothing else."""


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
            content = existing_file.read_text()
            # Extract just the log entries (## sections) for context
            import re as _re
            log_entries = _re.findall(r"(## \d{4}-\d{2}-\d{2} \d{2}:\d{2}.*?)(?=\n## |\n---)", content, _re.DOTALL)
            previous_log = "\n\n".join(e.strip() for e in log_entries)
            log.info("Found existing log with %d entries, offset %d", len(log_entries), offset)

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

    # Parse the two XML sections from the response
    import re
    summary_match = re.search(r"<summary>(.*?)</summary>", result, re.DOTALL)
    entry_match = re.search(r"<log_entry>(.*?)</log_entry>", result, re.DOTALL)

    summary = summary_match.group(1).strip() if summary_match else ""
    new_entry = entry_match.group(1).strip() if entry_match else ""

    if not summary and not new_entry:
        # Fallback: treat entire result as a log entry
        log.warning("Could not parse XML sections, using raw result")
        new_entry = result.strip()

    # Assemble the file
    if existing_file and existing_file.exists():
        content = existing_file.read_text()

        # Extract existing log entries (everything between the separator and the footer)
        # File structure: title + summary + --- + log entries + --- + footer
        parts = content.split("\n---\n")
        # parts[0] = title + summary, parts[1:-1] = log section, parts[-1] = footer

        # Find the title line (# ...)
        title_line = ""
        for line in content.split("\n"):
            if line.startswith("# "):
                title_line = line
                break

        # Extract existing log entries: everything after first --- up to the footer
        existing_entries = ""
        if len(parts) >= 3:
            # Middle sections are log entries
            existing_entries = "\n---\n".join(parts[1:-1]).strip()
        elif len(parts) == 2:
            # Could be: [title+summary, footer] with no entries yet, or [title+summary+entries, footer]
            # Check if the second-to-last part has ## entries
            if "## " in parts[0]:
                # Log entries are mixed into the first part — split on first ##
                idx = parts[0].index("## ")
                existing_entries = parts[0][idx:].strip()

        out_path = existing_file
    else:
        # New file — extract title from the log entry
        title_line = ""
        for line in new_entry.split("\n"):
            if line.startswith("# "):
                title_line = line
                new_entry = new_entry.replace(line + "\n", "", 1).strip()
                break

        if not title_line:
            title_line = "# Claude Code Session"

        existing_entries = ""

        # Generate filename from title
        now = datetime.now()
        slug = title_line.lstrip("# ").strip().lower()
        for ch in ":/\\?*\"<>|'(),&.!":
            slug = slug.replace(ch, "")
        slug = "-".join(slug.split())[:60]
        filename = f"{now.strftime('%Y-%m-%d_%H%M')}_{slug}.md"
        out_path = VAULT_DIR / filename

    # Build the file: title → summary → --- → log entries → --- → footer
    sections = [title_line, ""]
    if summary:
        sections.append(summary)
    sections.append("")
    sections.append("---")
    sections.append("")

    # Existing entries + new entry
    if existing_entries:
        sections.append(existing_entries)
        sections.append("")
    if new_entry:
        sections.append(new_entry)

    # Footer
    sections.append("")
    sections.append("---")
    sections.append(f"*Session: `{session_id}` | Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    sections.append("")

    updated = "\n".join(sections)
    out_path.write_text(updated)

    # Update index
    index[session_id] = {"file": out_path.name, "offset": len(entries)}
    write_index(index)

    log.info("Wrote %s (%d bytes, %d transcript entries)", out_path.name, len(updated), len(entries))


if __name__ == "__main__":
    main()
