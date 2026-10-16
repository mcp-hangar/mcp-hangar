### `/api/config/diff` and `/api/config/backup` refuse `config_path`

Both routes now act on the file the gateway was started with and answer `422` to a request that names `config_path` (the `GET /api/config/diff` query parameter, the `POST /api/config/backup` body field), the way `/api/config/reload` has since 2.x. Drop the parameter from any script that sends it; if it pointed at the gateway's own file, nothing else changes. A gateway started with `--config X` now backs up and diffs `X` instead of `MCP_CONFIG` or `./config.yaml`.
