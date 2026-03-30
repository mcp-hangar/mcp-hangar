#!/bin/bash
# Opens iTerm running opencode in packages/core.
#
# Usage: ./scripts/opencode-split.sh
# Requires: iTerm, opencode

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORE_DIR="$REPO_ROOT/packages/core"

if ! command -v opencode &>/dev/null; then
  echo "Error: opencode not found in PATH" >&2
  exit 1
fi

osascript - "$CORE_DIR" <<'APPLESCRIPT'
on run argv
    set coreDir to item 1 of argv

    tell application "iTerm"
        activate
        delay 0.5
        set newWindow to (create window with default profile)

        tell newWindow
            tell current session
                write text "cd " & quoted form of coreDir & " && opencode"
            end tell
        end tell
    end tell
end run
APPLESCRIPT
