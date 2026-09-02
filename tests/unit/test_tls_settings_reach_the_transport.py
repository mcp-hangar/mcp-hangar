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

    def test_the_transport_no_longer_retries_on_its_own(self) -> None:
        """Retrying moved up to `_post_with_retry`, and must not happen twice (#1163).

        httpcore's loop retries connect failures only, on a backoff the operator
        cannot configure, in a place the retry metric cannot be emitted from.
        Leaving it at `max_retries` as well would multiply the two loops.
        """
        client = HttpClient(endpoint=ENDPOINT, http_config=HttpClientConfig(max_retries=7))

        assert _transport(client)._pool._retries == 0
        assert client._http_config.max_retries == 7


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


class TestTurningItOffIsLoud:
    """The setting changed meaning, so the deployments it changes have to hear it.

    Until 2.5.0 `verify_ssl: false` was accepted and discarded. A configuration
    carrying it from that era did nothing and now does exactly what it says --
    and whoever wrote it may no longer be reading. One warning per upstream
    beats a field on an info line among thirty others.
    """

    def _launch(self, tls_config):
        from structlog.testing import capture_logs

        from mcp_hangar.infrastructure.launchers.http import HttpLauncher

        with capture_logs() as logs:
            HttpLauncher().launch(endpoint="https://upstream.internal:8443/mcp", tls_config=tls_config)
        return logs

    def test_disabling_verification_warns(self) -> None:
        logs = self._launch({"verify_ssl": False})

        warnings = [entry for entry in logs if entry["event"] == "tls_verification_disabled"]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert "upstream.internal" in warnings[0]["endpoint"]

    def test_the_warning_says_what_to_do(self) -> None:
        detail = next(e for e in self._launch({"verify_ssl": False}) if e["event"] == "tls_verification_disabled")

        assert "verify_ssl: true" in detail["detail"]
        assert "ca_cert_path" in detail["detail"]

    def test_the_default_is_quiet(self) -> None:
        assert not [e for e in self._launch(None) if e["event"] == "tls_verification_disabled"]

    def test_a_custom_ca_is_not_a_warning(self, tmp_path) -> None:
        # Trusting your own CA is verification, not the absence of it. The
        # bundle has to exist: httpx loads it when the transport is built, so a
        # missing path fails the launch for an unrelated reason.
        bundle = tmp_path / "ca.pem"
        bundle.write_text(_a_real_certificate())

        logs = self._launch({"verify_ssl": True, "ca_cert_path": str(bundle)})

        assert not [e for e in logs if e["event"] == "tls_verification_disabled"]

    def test_ca_cert_path_overrides_a_false_boolean_without_the_off_warning(self, tmp_path) -> None:
        # `ca_cert_path` wins in the client (`verify=` gets the path), so
        # verification is ENFORCED here even though `verify_ssl: false`. The
        # "verification is off" warning would send the operator to disable the
        # one setting keeping the handshake honest.
        bundle = tmp_path / "ca.pem"
        bundle.write_text(_a_real_certificate())

        logs = self._launch({"verify_ssl": False, "ca_cert_path": str(bundle)})

        assert not [e for e in logs if e["event"] == "tls_verification_disabled"]

    def test_ca_cert_path_overriding_a_false_boolean_says_verification_is_enforced(self, tmp_path) -> None:
        bundle = tmp_path / "ca.pem"
        bundle.write_text(_a_real_certificate())

        logs = self._launch({"verify_ssl": False, "ca_cert_path": str(bundle)})

        overrides = [e for e in logs if e["event"] == "tls_verify_ssl_overridden_by_ca_cert"]
        assert len(overrides) == 1
        assert overrides[0]["log_level"] == "warning"
        assert "upstream.internal" in overrides[0]["endpoint"]
        assert "enforced" in overrides[0]["detail"]
        assert overrides[0]["ca_cert_path"] == str(bundle)
