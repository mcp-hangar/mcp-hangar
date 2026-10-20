**core:** the audit trail of a key-pinned server (ADR-030) records which TLS key answered. The key is read from the socket of the connection that served the attempt, never from configuration.
- `ToolInvocationCompleted` and `ToolInvocationFailed` gain `upstream_peer_spki`, the verified key of the serving connection.
- `ToolInvocationFailed` gains `failure_code` (`upstream_peer_key_mismatch`) and `observed_peer_spki`, the key a refused peer presented.
- `ToolCallRefused` gains `observed_peer_spki` for a refused cold start.

How each export format carries them:
- JSON lines: under the event field names.
- LEEF and syslog: as `upstreamPeerSpki`, `failureCode` and `observedPeerSpki`.
- CEF: the one key as `fileHash`, with `fileType` saying whether it was verified (`UpstreamTlsKeyVerified`) or refused (`UpstreamTlsKeyRefused`).
- OTLP audit records: as `hangar.upstream.peer_spki`, `hangar.upstream.observed_peer_spki` and `hangar.upstream.failure_code`.

A server without a peer identity records none of these fields.
