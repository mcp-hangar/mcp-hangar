### SIGHUP reloads in the background, and reloads queue

`kill -HUP` used to run the reload on the thread that serves requests, so a
request sent right after it waited until the reload had applied (and so did
every other request, `/health/live` included). The reload now runs on a thread
of its own: the gateway keeps answering, and a request sent right after the
signal can still see the old configuration. A script that sends SIGHUP and
then checks the result should wait for the `config_reload_completed_via_signal`
(or `config_reload_failed_via_signal`) log line, or use
`POST /api/config/reload`, which answers once the reload has applied.

Reloads also run one at a time now. A `POST /api/config/reload` or
`hangar_reload_config` call made while another reload runs waits for it, which
for a graceful reload can be up to its 30 s drain, then applies and returns its
own result. The log says `config_reload_waiting` when that happens.
