#!/bin/bash
# Opens iTerm with two vertical split panes running opencode
# in packages/core and packages/ui.
#
# Usage: ./scripts/opencode-split.sh
# Requires: iTerm, opencode

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CORE_DIR="$REPO_ROOT/packages/core"
UI_DIR="$REPO_ROOT/packages/ui"

if ! command -v opencode &>/dev/null; then
  echo "Error: opencode not found in PATH" >&2
  exit 1
fi

osascript - "$CORE_DIR" "$UI_DIR" <<'APPLESCRIPT'
on run argv
    set coreDir to item 1 of argv
    set uiDir to item 2 of argv

    tell application "iTerm"
        activate
        delay 0.5
        set newWindow to (create window with default profile)

        tell newWindow
            tell current session
                write text "cd " & quoted form of coreDir & " && opencode"
                split vertically with default profile
            end tell
            tell last session of current tab
                write text "cd " & quoted form of uiDir & " && opencode"
            end tell
        end tell
    end tell
end run
APPLESCRIPT
