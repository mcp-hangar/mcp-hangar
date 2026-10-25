**infra:** the compose files that run the gateway with `--unsafe-no-auth` now
publish it on loopback only. `docker-compose.yml`, `examples/quickstart`,
`examples/otel-collector` and `examples/task_upstream` published `8080:8080`,
which Docker binds on every host interface, so anyone who could reach the host
had the unauthenticated management API. The quickstart's Prometheus and Grafana
(`admin/admin`) are on loopback too. The root file's healthcheck, which could
never fail, now probes `/health/ready`, and its obsolete `version:` key is gone
