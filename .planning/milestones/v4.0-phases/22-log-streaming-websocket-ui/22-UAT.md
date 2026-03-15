---
status: testing
phase: 22-log-streaming-websocket-ui
source: [22-01-SUMMARY.md, 22-02-SUMMARY.md, 22-03-SUMMARY.md]
started: 2026-03-15T12:00:00Z
updated: 2026-03-15T12:00:00Z
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

number: 1
name: Process Logs section visible on Provider Detail
expected: |
  Open the Provider Detail page for any provider (running or stopped).
  A "Process Logs" section is visible at the bottom of the page below
  the existing sections (health, tools, circuit breaker, etc.).
awaiting: user response

## Tests

### 1. Process Logs section visible on Provider Detail

expected: Open the Provider Detail page for any provider (running or stopped). A "Process Logs" section is visible at the bottom of the page below the existing sections (health, tools, circuit breaker, etc.).
result: [pending]

### 2. Log viewer uses monospace font

expected: The log viewer area renders text in a monospace/terminal-style font (e.g., Courier, Consolas, monospace). Log lines look like terminal output, not regular body text.
result: [pending]

### 3. stderr lines appear in amber

expected: Log lines captured from stderr are rendered in an amber/orange color. If you have a running provider that writes to stderr, those lines are visually distinct in warm amber/orange.
result: [pending]

### 4. stdout lines appear in gray

expected: Log lines captured from stdout are rendered in gray. They are visually distinct from stderr lines and from regular UI text.
result: [pending]

### 5. Buffered history loads on connect

expected: When you open a Provider Detail page for a provider that has already been running (and has produced log output), existing buffered log lines appear immediately in the log viewer — no page reload or manual action needed.
result: [pending]

### 6. Live log streaming without refresh

expected: While a provider is running and producing output, new log lines appear in the log viewer in real time without any page reload or polling. The viewer updates as the provider writes to stderr/stdout.
result: [pending]

### 7. WebSocket auto-reconnects after disconnect

expected: If the WebSocket connection drops (e.g., you briefly lose network or the backend restarts), the log viewer automatically reconnects and resumes streaming — no manual action required.
result: [pending]

## Summary

total: 7
passed: 0
issues: 0
pending: 7
skipped: 0

## Gaps

[none yet]
