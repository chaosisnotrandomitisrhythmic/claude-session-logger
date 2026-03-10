#!/usr/bin/env bash
set -euo pipefail

# Install claude-session-logger
# Sets up the SessionEnd hook and creates the Obsidian output directory.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SETTINGS_FILE="$HOME/.claude/settings.json"
VAULT_DIR="${OBSIDIAN_VAULT_DIR:-$HOME/Documents/refuse_to_choose}/Claude Sessions"

echo "Installing claude-session-logger..."
echo "  Scripts: $SCRIPT_DIR"
echo "  Output:  $VAULT_DIR"

# Create output directory
mkdir -p "$VAULT_DIR"

# Check for API key
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    if ! grep -q 'ANTHROPIC_API_KEY' "$HOME/.bashrc" 2>/dev/null; then
        echo ""
        echo "WARNING: No ANTHROPIC_API_KEY found in env or ~/.bashrc"
        echo "The logger needs this to call the Claude API."
        echo "Add to ~/.bashrc:  export ANTHROPIC_API_KEY=sk-ant-..."
    fi
fi

# Add hook to settings.json
if [[ ! -f "$SETTINGS_FILE" ]]; then
    echo '{}' > "$SETTINGS_FILE"
fi

# Check if hook already exists
if python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    s = json.load(f)
hooks = s.get('hooks', {}).get('SessionEnd', [])
for h in hooks:
    for sub in h.get('hooks', []):
        if 'session-summary.sh' in sub.get('command', ''):
            sys.exit(0)
sys.exit(1)
" "$SETTINGS_FILE" 2>/dev/null; then
    echo "Hook already registered in settings.json"
else
    python3 -c "
import json, sys
path = sys.argv[1]
cmd = sys.argv[2]
with open(path) as f:
    s = json.load(f)
s.setdefault('hooks', {}).setdefault('SessionEnd', []).append({
    'matcher': '',
    'hooks': [{'type': 'command', 'command': f'bash {cmd}', 'timeout': 10000}]
})
with open(path, 'w') as f:
    json.dump(s, f, indent=2)
    f.write('\n')
" "$SETTINGS_FILE" "$SCRIPT_DIR/session-summary.sh"
    echo "Hook registered in $SETTINGS_FILE"
fi

echo ""
echo "Done. Start a new Claude Code session — summaries will appear in:"
echo "  $VAULT_DIR"
