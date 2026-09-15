### docker discovery no longer waits for Docker

With docker discovery enabled and Docker unreachable, `serve` and
`Hangar.start()` no longer wait out the docker source's connection retries, and
a stop no longer waits for a connection attempt in flight. **Nothing needs
changing.** Two things read differently:

- **The docker source's `is_healthy`** in `GET /discovery/sources` and
  `hangar_sources` reports whether discovery's last connection or scan reached
  Docker. It no longer connects and pings from the request. It is `false` until
  the first connection completes, and it follows a Docker that goes away or
  comes back at the next discovery cycle, not at the next listing.
- **Each connection attempt times out sooner.** Its requests, the API version
  check and the ping, time out after 5 s instead of the Docker client's 60 s.
  A Docker that takes longer than that to answer is treated as unreachable.
  Calls after the connection keep the client's default timeout.
