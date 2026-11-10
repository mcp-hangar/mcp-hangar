**release:** 2.36.2 was tagged but never published: a test of the
`tools/list_changed` coalescing window asserted before the callbacks it had
handed to the event loop had run, and failed in the release job under a newer
`pytest-asyncio`. The test now waits for them. 2.36.3 publishes everything
2.36.2 carried. No runtime change.
