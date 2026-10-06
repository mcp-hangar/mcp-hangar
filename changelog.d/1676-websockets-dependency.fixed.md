**deps:** `/api/ws/events` works on a pip or uv install. uvicorn upgrades a
WebSocket connection only through a library it can import, and none was
declared -- only the container image installed `websockets` -- so outside the
image uvicorn logged "No supported WebSocket library detected" and the event
stream answered 404. `websockets>=16.0` is now a base dependency, and the
Dockerfile no longer installs it separately.
