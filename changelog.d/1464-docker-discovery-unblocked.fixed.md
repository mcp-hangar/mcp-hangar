**core:** docker discovery no longer holds up `serve`, or a `Hangar` that runs
discovery, while Docker is unreachable. Starting waited for the docker source's
whole connection retry schedule, about 15 s by default. And a single connection
attempt blocked discovery's event loop for as long as the Docker client took to
give up: 60 s by default, for a TCP `DOCKER_HOST` that does not answer. A stop
could hang that long. The source now connects on a thread of its own. Starting
returns at once, and a stop no longer waits for an attempt in flight. Each
request of an attempt times out after the new `connect_timeout_s`, 5 s by
default. A reachable Docker is still scanned in the first discovery cycle.
The docker source's `is_healthy`, in `GET /discovery/sources` and
`hangar_sources`, now reports whether discovery's last connection or scan
reached Docker, instead of connecting from the request.
