### The example compose files publish the gateway on `127.0.0.1` only

`docker-compose.yml` and the `quickstart`, `otel-collector` and `task_upstream`
examples now publish port 8080 as `127.0.0.1:8080:8080`, and the quickstart's
Prometheus and Grafana likewise. From the same host, `http://localhost:8080`
works as before. From another machine it no longer answers.

Those files run the gateway with `--unsafe-no-auth`, so reaching it from
elsewhere meant reaching an unauthenticated management API. To serve other hosts,
configure authentication (see `examples/auth-keycloak/`), drop `--unsafe-no-auth`,
and only then publish the port beyond loopback.
