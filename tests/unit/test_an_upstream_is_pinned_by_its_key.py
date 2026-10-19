"""An upstream with a key list is reached only through a connection that ends at a listed key (#1938).

ADR-030 §5. The check runs inside the TLS handshake, so a peer whose key is not
listed receives no request byte. Every case here talks to a real TLS server in
a thread that records what it received, so "zero bytes to a mismatched peer" is
measured rather than assumed.
"""

import datetime
import socket
import ssl
import threading
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from mcp_hangar.domain.exceptions import ClientError, UpstreamPeerKeyMismatchError
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.errors import ExpectedRefusal
from mcp_hangar.http_client import (
    HttpClient,
    HttpClientConfig,
    _peer_key_context,
    _PeerKeyCheckedSocket,
    peer_key_pin,
)

_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}"


def _leaf(directory: Path, name: str) -> tuple[str, str, str]:
    """A self-signed leaf for `localhost`: (cert path, key path, its key pin)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{name}.pem", directory / f"{name}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return str(cert_path), str(key_path), peer_key_pin(certificate.public_bytes(serialization.Encoding.DER))


class _Upstream:
    """One-connection TLS server that records the SNI it saw and the bytes it was sent."""

    def __init__(self, cert: str | None, key: str | None, *, tls_max: ssl.TLSVersion | None = None) -> None:
        self.sni: str | None = None
        self.received = b""
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._context: ssl.SSLContext | None = None
        if cert is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            if tls_max is not None:
                context.maximum_version = tls_max
            context.sni_callback = self._remember_sni
            self._context = context
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _remember_sni(self, _socket, name, _context) -> None:
        self.sni = name

    def _serve(self) -> None:
        self._listener.settimeout(5)
        try:
            raw, _ = self._listener.accept()
        except OSError:
            return
        raw.settimeout(2)
        try:
            conn = self._context.wrap_socket(raw, server_side=True) if self._context else raw
            while not self._whole_request():
                chunk = conn.recv(4096)
                if not chunk:
                    break
                self.received += chunk
            if self.received:
                conn.sendall(_RESPONSE)
            # Every byte was read first: closing with unread data sends a reset,
            # which can arrive before the response and drop it.
            conn.close()
        except (OSError, ssl.SSLError):
            raw.close()

    def _whole_request(self) -> bool:
        head, separator, body = self.received.partition(b"\r\n\r\n")
        if not separator:
            return False
        for line in head.split(b"\r\n"):
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                return len(body) >= int(value.strip())
        return True

    def stop(self) -> None:
        self._listener.close()
        self._thread.join(timeout=5)


def _client(port: int, pins: frozenset[str] | None, *, host: str = "127.0.0.1", **config) -> HttpClient:
    return HttpClient(
        endpoint=f"https://{host}:{port}", http_config=HttpClientConfig(peer_key_pins=pins, max_retries=0, **config)
    )


class TestAListedKeyIsServed:
    def test_the_call_goes_through_and_the_socket_keeps_the_observed_key(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = _client(upstream.port, frozenset({pin}))
        try:
            response = client._client.post(f"https://127.0.0.1:{upstream.port}/mcp", content=b"{}")
            stream = response.extensions["network_stream"]
            observed = stream.get_extra_info("socket").observed_peer_spki
        finally:
            client.close()
            upstream.stop()

        assert response.status_code == 200
        assert observed == pin
        assert upstream.received.startswith(b"POST /mcp")

    def test_sni_is_still_sent_with_the_name_check_off(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = _client(upstream.port, frozenset({pin}), host="localhost")
        try:
            client._client.post(f"https://localhost:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()

        assert upstream.sni == "localhost"


class TestAKeyNotInTheListIsRefusedBeforeAnyByte:
    def test_an_otherwise_valid_chain_is_refused_and_the_upstream_receives_nothing(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        _, _, other = _leaf(tmp_path, "other")
        upstream = _Upstream(cert, key)
        # The CA that would make the chain valid is configured, and ignored.
        client = _client(upstream.port, frozenset({other}), ca_cert_path=cert)
        try:
            with pytest.raises(UpstreamPeerKeyMismatchError) as refused:
                client._client.post(f"https://127.0.0.1:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()

        assert refused.value.observed_peer_spki == pin
        assert refused.value.failure_code == "upstream_peer_key_mismatch"
        assert upstream.received == b""

    def test_a_peer_that_presents_no_certificate_is_refused(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = _client(upstream.port, frozenset({pin}))
        try:
            with (
                patch.object(_PeerKeyCheckedSocket, "getpeercert", return_value=None),
                pytest.raises(UpstreamPeerKeyMismatchError) as refused,
            ):
                client._client.post(f"https://127.0.0.1:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()

        assert refused.value.observed_peer_spki is None
        assert upstream.received == b""


class TestAFailureBeforeTheKeyIsSeenIsATransportError:
    def test_a_tls_1_2_only_peer_fails_the_handshake(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key, tls_max=ssl.TLSVersion.TLSv1_2)
        client = _client(upstream.port, frozenset({pin}))
        try:
            with pytest.raises(httpx.ConnectError):
                client._client.post(f"https://127.0.0.1:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()

        assert upstream.received == b""

    def test_a_plaintext_peer_fails_the_handshake(self, tmp_path) -> None:
        _, _, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(None, None)
        client = _client(upstream.port, frozenset({pin}))
        try:
            with pytest.raises(httpx.ConnectError):
                client._client.post(f"https://127.0.0.1:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()


class TestTheRefusalAndTheContextAreWhatTheRecordSays:
    def test_the_refusal_is_an_expected_client_error_and_not_an_os_error(self) -> None:
        assert issubclass(UpstreamPeerKeyMismatchError, ClientError)
        assert issubclass(UpstreamPeerKeyMismatchError, ExpectedRefusal)
        assert not issubclass(UpstreamPeerKeyMismatchError, OSError)

    def test_the_context_requires_tls_1_3_and_checks_no_chain_or_name(self) -> None:
        context = _peer_key_context(frozenset({"sha256:" + "0" * 64}))

        assert context.minimum_version == ssl.TLSVersion.TLSv1_3
        assert context.check_hostname is False
        assert context.verify_mode == ssl.CERT_NONE
        assert issubclass(context.sslsocket_class, _PeerKeyCheckedSocket)


class TestAKeyListThatVerifiesNothingIsRefused:
    def test_an_empty_key_list_does_not_build_a_client(self) -> None:
        with pytest.raises(ClientError, match="non-empty key list"):
            HttpClient(endpoint="https://upstream:8443", http_config=HttpClientConfig(peer_key_pins=frozenset()))

    def test_a_plaintext_endpoint_does_not_build_a_client(self) -> None:
        with pytest.raises(ClientError, match="over https"):
            HttpClient(
                endpoint="http://upstream:8080",
                http_config=HttpClientConfig(peer_key_pins=frozenset({"sha256:" + "0" * 64})),
            )

    def test_a_server_without_a_key_list_is_unchanged(self) -> None:
        client = HttpClient(endpoint="http://upstream:8080", http_config=HttpClientConfig())
        client.close()


class TestTheKeyCheckComposesWithTheSsrfGuard:
    """ADR-030 open question Q5: one transport, the IP pinned, SNI still the name."""

    @staticmethod
    def _guarded(port: int, pins: frozenset[str]) -> HttpClient:
        return _client(
            port,
            pins,
            host="localhost",
            enforce_ssrf=True,
            provenance=Provenance.DISCOVERY,
            runtime_addresses=frozenset({"127.0.0.1"}),
        )

    @staticmethod
    def _resolves_to_loopback():
        # `ssrf.socket` is the process's socket module, so httpcore's connect
        # resolves through this too: keep the port it asked for.
        def loopback(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0))]

        return patch("mcp_hangar.domain.security.ssrf.socket.getaddrinfo", side_effect=loopback)

    def test_a_listed_key_is_served_through_the_pinned_ip_with_the_name_as_sni(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = self._guarded(upstream.port, frozenset({pin}))
        try:
            with self._resolves_to_loopback():
                response = client._client.post(f"https://localhost:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()

        assert response.status_code == 200
        assert upstream.sni == "localhost"

    def test_an_unlisted_key_is_refused_through_the_guard_too(self, tmp_path) -> None:
        cert, key, _ = _leaf(tmp_path, "upstream")
        _, _, other = _leaf(tmp_path, "other")
        upstream = _Upstream(cert, key)
        client = self._guarded(upstream.port, frozenset({other}))
        try:
            with self._resolves_to_loopback(), pytest.raises(UpstreamPeerKeyMismatchError):
                client._client.post(f"https://localhost:{upstream.port}/mcp", content=b"{}")
        finally:
            client.close()
            upstream.stop()

        assert upstream.received == b""
