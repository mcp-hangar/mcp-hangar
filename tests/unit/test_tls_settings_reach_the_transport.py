"""The TLS settings have to be on the transport, or they are not settings.

`httpx.Client(verify=...)` configures the transport httpx would have built for
itself. This client passes `transport=` explicitly, for retries -- which
replaces that transport with one built here, and a transport constructed
without `verify` verifies against the system trust store. So every TLS setting
an operator could write was accepted, logged, and discarded.

Measured against a self-signed upstream on 2.5.0-rc.3, all three through the
same httpx:

    verify=False, no explicit transport        -> 200
    verify=False + transport without verify    -> ConnectError
    transport built with verify=False          -> 200

It failed **closed**, which is why nobody noticed: `verify_ssl: false` simply
did not work, and the failure looked like a stubborn certificate. `ca_cert_path`
rides the same argument and was discarded the same way -- and that one has no
safe reading, because it is how a deployment trusts its own internal CA.

These tests read the transport rather than opening a socket: what matters is
that the setting arrives, and a live TLS handshake in a unit test would be a
slower way of asking a worse question. The live proof is in the commit message.
"""

from __future__ import annotations

import ssl

import httpx
import pytest

from mcp_hangar.http_client import HttpClient, HttpClientConfig

ENDPOINT = "https://upstream.internal:8443/mcp"


def _transport(client: HttpClient) -> httpx.HTTPTransport:
    transport = client._client._transport
    assert isinstance(transport, httpx.HTTPTransport)
    return transport


def _verifies(transport: httpx.HTTPTransport) -> bool:
    """Whether this transport would check the peer's certificate."""
    context = transport._pool._ssl_context
    assert isinstance(context, ssl.SSLContext)
    return context.verify_mode is not ssl.CERT_NONE


class TestTheSettingReachesTheTransport:
    def test_verification_is_on_by_default(self) -> None:
        client = HttpClient(endpoint=ENDPOINT)

        assert _verifies(_transport(client))

    def test_verify_ssl_false_actually_turns_it_off(self) -> None:
        # The bug: this was accepted, logged as `verify_ssl=False`, and ignored.
        client = HttpClient(endpoint=ENDPOINT, http_config=HttpClientConfig(verify_ssl=False))

        assert not _verifies(_transport(client))

    def test_a_ca_bundle_is_loaded_rather_than_dropped(self, tmp_path) -> None:
        # No safe reading of losing this one: it is how a deployment trusts its
        # own internal CA, and discarding it means the upstream is unreachable.
        bundle = tmp_path / "ca.pem"
        bundle.write_text(_a_real_certificate())

        client = HttpClient(endpoint=ENDPOINT, http_config=HttpClientConfig(ca_cert_path=str(bundle)))
        context = _transport(client)._pool._ssl_context

        assert context.verify_mode is not ssl.CERT_NONE
        assert any(cert["subject"] for cert in context.get_ca_certs()), "the bundle was not loaded"

    def test_the_ca_bundle_wins_over_the_boolean(self, tmp_path) -> None:
        bundle = tmp_path / "ca.pem"
        bundle.write_text(_a_real_certificate())

        client = HttpClient(
            endpoint=ENDPOINT,
            http_config=HttpClientConfig(verify_ssl=True, ca_cert_path=str(bundle)),
        )

        assert _verifies(_transport(client))


class TestTheClientDoesNotAskTwice:
    def test_verify_is_not_also_passed_to_the_client(self) -> None:
        """A `verify=` beside an explicit `transport=` reads as configuration and is not.

        Keeping it would leave the next reader with two places to change and one
        of them inert -- which is exactly how this survived.
        """
        import inspect

        source = inspect.getsource(HttpClient._create_client)
        client_call = source[source.index("return httpx.Client(") :]

        assert "verify=" not in client_call
        assert "verify=verify" in source, "the transport must still receive it"

    def test_the_retry_setting_is_still_there(self) -> None:
        # The transport exists for retries; moving TLS onto it must not drop them.
        client = HttpClient(endpoint=ENDPOINT, http_config=HttpClientConfig(max_retries=7))

        assert _transport(client)._pool._retries == 7


@pytest.mark.parametrize("verify_ssl", [True, False])
def test_every_client_gets_one_transport(verify_ssl: bool) -> None:
    client = HttpClient(endpoint=ENDPOINT, http_config=HttpClientConfig(verify_ssl=verify_ssl))

    assert isinstance(client._client._transport, httpx.HTTPTransport)


def _a_real_certificate() -> str:
    """A throwaway self-signed certificate, generated rather than pasted.

    The bundle test has to load something OpenSSL accepts; a hand-written PEM
    is a fixture that fails for reasons unrelated to the thing under test.
    """
    from datetime import datetime, timedelta, UTC

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hangar-test-ca")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()
