"""A server's peer identity is declared in the configuration file and held to it (#1941).

ADR-030 §4 and §7. A declaration that cannot be enforced is refused at load,
naming both halves; the key list reaches the HTTP client whatever other blocks
the server has; and the identity digest pins are bound to becomes the key
lineage instead of the address.
"""

from unittest.mock import patch

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects.peer_identity import PeerIdentity
from mcp_hangar.infrastructure.launchers import HttpLauncher
from mcp_hangar.server.config import _peer_identity_tls

PIN = "sha256:" + "d" * 64
NEXT = "sha256:" + "e" * 64
IDENTITY = {"key_ref": "kbs:///default/payments/tls-key", "spki_sha256": [PIN, NEXT]}


def _spec(**overrides) -> dict:
    spec = {"mode": "remote", "endpoint": "https://10.244.3.17:8443", "tls": {"peer_identity": dict(IDENTITY)}}
    spec.update(overrides)
    return spec


class TestADeclarationThatCannotBeEnforcedIsRefused:
    def test_a_plaintext_endpoint_names_both_halves(self) -> None:
        with pytest.raises(ConfigurationError, match=r"peer_identity needs an https:// endpoint.*http://"):
            _peer_identity_tls("payments", _spec(endpoint="http://10.244.3.17:8080"))

    @pytest.mark.parametrize("mode", ["subprocess", "docker", "container"])
    def test_a_mode_without_a_connection_to_bind(self, mode: str) -> None:
        with pytest.raises(ConfigurationError, match="needs mode: remote"):
            _peer_identity_tls("payments", _spec(mode=mode))

    @pytest.mark.parametrize(
        ("identity", "complaint"),
        [
            ({"key_ref": "k", "spki_sha256": []}, "at least one key pin"),
            ({"key_ref": "k"}, "at least one key pin"),
            ({"spki_sha256": [PIN]}, "key_ref must name the key lineage"),
            ({"key_ref": " ", "spki_sha256": [PIN]}, "key_ref must name the key lineage"),
            ({"key_ref": "k", "spki_sha256": ["sha256:" + "D" * 64]}, "is not a key pin"),
            ({"key_ref": "k", "spki_sha256": ["3q2+7w=="]}, "is not a key pin"),
            ({"key_ref": "k", "spki_sha256": ["sha384:" + "d" * 96]}, "is not a key pin"),
            ({"key_ref": "k", "spki_sha256": ["sha256:" + "d" * 63]}, "is not a key pin"),
            ({"key_ref": "k", "spki_sha256": [PIN], "issuance": "kbs-attested"}, "not supported by this release"),
            ({"key_ref": "k", "spki_sha256": [PIN], "san": "x"}, "unknown keys: san"),
            ("kbs:///k", "must be a mapping"),
        ],
    )
    def test_a_key_list_that_would_verify_nothing(self, identity, complaint: str) -> None:
        with pytest.raises(ConfigurationError, match=complaint):
            _peer_identity_tls("payments", _spec(tls={"peer_identity": identity}))

    def test_verify_ssl_false_is_a_misread_and_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="verify_ssl: false"):
            _peer_identity_tls("payments", _spec(tls={"peer_identity": dict(IDENTITY), "verify_ssl": False}))

    def test_max_connection_age_is_refused_until_it_does_something(self) -> None:
        with pytest.raises(ConfigurationError, match="max_connection_age is not supported"):
            _peer_identity_tls("payments", _spec(tls={"peer_identity": dict(IDENTITY), "max_connection_age": "1h"}))

    def test_a_ca_cert_path_is_ignored_with_a_warning(self) -> None:
        from structlog.testing import capture_logs

        with capture_logs() as logs:
            tls = _peer_identity_tls(
                "payments", _spec(tls={"peer_identity": dict(IDENTITY), "ca_cert_path": "/etc/ca.pem"})
            )

        assert tls["peer_identity"] == IDENTITY
        assert [entry["event"] for entry in logs] == ["peer_identity_ignores_ca_cert_path"]


class TestAServerWithoutAPeerIdentityIsUnchanged:
    def test_its_tls_block_passes_through(self) -> None:
        tls = {"verify_ssl": False, "ca_cert_path": "/etc/ca.pem"}

        assert _peer_identity_tls("plain", {"mode": "remote", "endpoint": "http://x:1", "tls": tls}) is tls

    def test_its_identity_is_still_the_address(self) -> None:
        server = McpServer(mcp_server_id="plain", mode="remote", endpoint="https://Upstream:8443/mcp")

        assert server.upstream_identity() == "remote:https://upstream:8443"

    def test_its_client_has_no_key_list(self) -> None:
        with patch("mcp_hangar.http_client.HttpClient") as client:
            HttpLauncher().launch(endpoint="https://upstream:8443", tls_config={"verify_ssl": True})

        assert client.call_args.kwargs["http_config"].peer_key_pins is None


class TestTheIdentityIsTheKeyLineage:
    def test_it_is_the_key_ref_and_not_the_address(self) -> None:
        server = McpServer(
            mcp_server_id="payments",
            mode="remote",
            endpoint="https://10.244.3.17:8443",
            tls={"peer_identity": dict(IDENTITY)},
        )

        assert server.upstream_identity() == "peer-key:kbs:///default/payments/tls-key"

    def test_a_new_address_or_a_rotated_key_keeps_it(self) -> None:
        def identity(endpoint: str, pins: list[str]) -> str:
            tls = {"peer_identity": {"key_ref": IDENTITY["key_ref"], "spki_sha256": pins}}
            return McpServer(mcp_server_id="payments", mode="remote", endpoint=endpoint, tls=tls).upstream_identity()

        assert identity("https://10.244.3.17:8443", [PIN]) == identity("https://10.244.9.2:8443", [PIN, NEXT])
        assert identity("https://10.244.3.17:8443", [PIN]) == identity("https://10.244.3.17:8443", [NEXT])

    def test_a_different_key_ref_behind_the_same_address_is_a_different_upstream(self) -> None:
        def identity(key_ref: str) -> str:
            tls = {"peer_identity": {"key_ref": key_ref, "spki_sha256": [PIN]}}
            return McpServer(
                mcp_server_id="payments", mode="remote", endpoint="https://10.244.3.17:8443", tls=tls
            ).upstream_identity()

        assert identity("lineage-a") != identity("lineage-b")

    def test_giving_an_existing_server_a_peer_identity_changes_its_identity(self) -> None:
        before = McpServer(mcp_server_id="payments", mode="remote", endpoint="https://10.244.3.17:8443")
        after = McpServer(
            mcp_server_id="payments",
            mode="remote",
            endpoint="https://10.244.3.17:8443",
            tls={"peer_identity": dict(IDENTITY)},
        )

        assert before.upstream_identity() != after.upstream_identity()

    def test_a_server_built_another_way_is_held_to_the_same_declaration(self) -> None:
        with pytest.raises(ValueError, match="is not a key pin"):
            McpServer(
                mcp_server_id="payments",
                mode="remote",
                endpoint="https://10.244.3.17:8443",
                tls={"peer_identity": {"key_ref": "k", "spki_sha256": ["nope"]}},
            )


class TestTheKeyListReachesTheClient:
    def test_from_the_tls_block(self) -> None:
        with patch("mcp_hangar.http_client.HttpClient") as client:
            HttpLauncher().launch(endpoint="https://10.244.3.17:8443", tls_config={"peer_identity": dict(IDENTITY)})

        assert client.call_args.kwargs["http_config"].peer_key_pins == frozenset({PIN, NEXT})

    def test_and_survives_an_http_block(self) -> None:
        with patch("mcp_hangar.http_client.HttpClient") as client:
            HttpLauncher().launch(
                endpoint="https://10.244.3.17:8443",
                tls_config={"peer_identity": dict(IDENTITY)},
                http_config={"read_timeout": 12, "headers": {"X-Trace": "1"}},
            )

        config = client.call_args.kwargs["http_config"]
        assert config.peer_key_pins == frozenset({PIN, NEXT})
        assert config.read_timeout == 12

    def test_the_parsed_value_object_is_canonical(self) -> None:
        identity = PeerIdentity.from_mapping(IDENTITY)

        assert identity.spki_sha256 == frozenset({PIN, NEXT})
        assert identity.upstream_identity == "peer-key:kbs:///default/payments/tls-key"


class TestTheDeclarationIsEnforcedOnARealConnection:
    def test_a_launched_client_refuses_an_unlisted_key_before_any_byte(self, tmp_path) -> None:
        from mcp_hangar.domain.exceptions import UpstreamPeerKeyMismatchError
        from tests.unit.test_an_upstream_is_pinned_by_its_key import _leaf, _Upstream

        cert, key, _ = _leaf(tmp_path, "upstream")
        upstream = _Upstream(cert, key)
        client = HttpLauncher().launch(
            endpoint=f"https://127.0.0.1:{upstream.port}", tls_config={"peer_identity": dict(IDENTITY)}
        )
        try:
            with pytest.raises(UpstreamPeerKeyMismatchError):
                client.call("initialize", {})
        finally:
            client.close()
            upstream.stop()

        assert upstream.received == b""
