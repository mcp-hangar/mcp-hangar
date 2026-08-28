#!/bin/bash
# MCP Hangar - container-discovery pool
# Usage: ./scripts/containers.sh [start|stop|status|scale N]
#
# Stands up labelled containers so the container discovery source has something
# to find. The images are the official ones from modelcontextprotocol/servers,
# published by Docker on Docker Hub -- nothing here is built locally.
#
# It used to run `localhost/mcp-{memory,math,fetch,filesystem}:latest`, images
# that no command in this repository, the Makefile, CI or the docs produced.
# Every `podman run` redirected stderr to /dev/null, so a missing image failed
# silently and the script printed nothing for that provider: it had never
# worked on a fresh checkout and said so to nobody.

set -u

DATA_DIR="${MCP_DATA_DIR:-./data}"
PODMAN="${PODMAN_PATH:-podman}"

# name:image:group. `mcp/fetch` is current; `mcp/filesystem` and `mcp/memory`
# were last pushed in 2025 -- old, but official and pullable, which is the
# trade for container mode. Anything driven as a subprocess should use
# `npx -y @modelcontextprotocol/server-*` or `uvx mcp-server-*` instead, where
# the packages are current.
PROVIDERS=(
  "filesystem:docker.io/mcp/filesystem:latest:filesystem-cluster"
  "memory:docker.io/mcp/memory:latest:memory-cluster"
  "fetch:docker.io/mcp/fetch:latest:fetch-cluster"
)

start_containers() {
    local count=${1:-5}
    echo "Starting MCP containers..."
    mkdir -p "$DATA_DIR/memory" "$DATA_DIR/filesystem"
    chmod 777 "$DATA_DIR/memory" "$DATA_DIR/filesystem"

    local started=0
    for entry in "${PROVIDERS[@]}"; do
        local name="${entry%%:*}"
        local rest="${entry#*:}"
        local image="${rest%:*}"
        local group="${rest##*:}"

        # Pulled once, up front: a failure here is the operator's answer, not
        # something to swallow per-replica.
        if ! $PODMAN pull "$image" >/dev/null; then
            echo "SKIP $name -- could not pull $image"
            continue
        fi

        for i in $(seq 1 "$count"); do
            if $PODMAN run -d --name "mcp-$name-$i" \
                --label mcp.hangar.enabled=true \
                --label "mcp.hangar.name=$name-$i" \
                --label mcp.hangar.mode=container \
                --label mcp.hangar.read-only=false \
                --label "mcp.hangar.group=$group" \
                --label "mcp.hangar.volumes=$DATA_DIR/$name:/data:rw" \
                "$image" >/dev/null; then
                started=$((started + 1))
            else
                echo "FAILED mcp-$name-$i"
            fi
        done
        echo "OK $name x$count"
    done

    echo ""
    echo "Total: $started containers started"
}

stop_containers() {
    echo "Stopping MCP containers..."
    local ids
    ids=$($PODMAN ps -aq --filter label=mcp.hangar.enabled=true)
    if [ -n "$ids" ]; then
        # One id per argument is the point, so the split is deliberate.
        # shellcheck disable=SC2086
        $PODMAN rm -f $ids >/dev/null
    fi
    echo "All MCP containers stopped"
}

status() {
    echo "MCP containers:"
    $PODMAN ps -a --filter label=mcp.hangar.enabled=true --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
    echo ""
    echo "Total: $($PODMAN ps -a --filter label=mcp.hangar.enabled=true -q | wc -l | tr -d ' ') containers"
}

case "${1:-}" in
    start)  start_containers "${2:-5}" ;;
    stop)   stop_containers ;;
    status) status ;;
    scale)  stop_containers; start_containers "${2:-5}" ;;
    *)
        echo "Usage: $0 [start|stop|status|scale] [count]"
        exit 1
        ;;
esac
