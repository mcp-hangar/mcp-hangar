**core:** the live tier now carries the three claims 2.18.0 rests on, driven the way an
operator drives them rather than through internal APIs: `mcp-hangar pin` (a digest per
tool, `--write`, `--check` exiting 1 on a changed *description* and 2 on a question it
cannot answer), what `init` writes (a config `config check` accepts, with `front_door`, a
stdio principal, and no pins when the smoke test was skipped), and the quickstart's own
sequence over stdio with the SDK client, ending in `Tool 'echo' schema does not match its
pinned digest`. The last one also pins the half that must not move: without the principal
block, the same configuration still serves an empty list.
