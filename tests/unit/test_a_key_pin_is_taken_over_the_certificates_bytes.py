"""A key pin is the SHA-256 of the SubjectPublicKeyInfo the certificate carries (ADR-030 §4, #1982).

Core used to re-encode the certificate's public key before hashing it. For RSA
and named-curve EC keys the bytes are the same, but an EC key with explicit
curve parameters comes back on its named curve: 91 bytes instead of the
certificate's 335, a different digest, and a listed key refused. The `openssl`
on macOS (LibreSSL) writes such keys unless told `ec_param_enc:named_curve`.
"""

from __future__ import annotations

import datetime
import hashlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from mcp_hangar.http_client import peer_key_pin

# `openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256
#  -pkeyopt ec_param_enc:explicit -subj /CN=explicit` (LibreSSL 3.3.6), a v1
# certificate: its TBSCertificate has no version field.
EXPLICIT_PARAMETERS = b"""-----BEGIN CERTIFICATE-----
MIICCjCCAbACCQCVhvJjxY8qyzAKBggqhkjOPQQDAjATMREwDwYDVQQDDAhleHBs
aWNpdDAeFw0yNjEwMjEwNzU5MDlaFw0zNjEwMTgwNzU5MDlaMBMxETAPBgNVBAMM
CGV4cGxpY2l0MIIBSzCCAQMGByqGSM49AgEwgfcCAQEwLAYHKoZIzj0BAQIhAP//
//8AAAABAAAAAAAAAAAAAAAA////////////////MFsEIP////8AAAABAAAAAAAA
AAAAAAAA///////////////8BCBaxjXYqjqT57PrvVV2mIa8ZR0GsMxTsPY7zjw+
J9JgSwMVAMSdNgiG5wSTamZ44ROdJreBn36QBEEEaxfR8uEsQkf4vOblY6RA8ncD
fYEt6zOg9KE5RdiYwpZP40Li/hp/m47n60p8D54WK84zV2sxXs7LtkBoN79R9QIh
AP////8AAAAA//////////+85vqtpxeehPO5ysL8YyVRAgEBA0IABF321we54ek5
er90HtGwR+Dq2ZaR2ydLOYpW72OKezwQdEihvZ445/vmhkbd2Qz+ZY30lxbfpN10
hUf6vdpQUpQwCgYIKoZIzj0EAwIDSAAwRQIhAKeu1TFFn1nuHcPB0d6VrlUI+LdA
tr6IIxtReb8j1DuVAiBJdFhrNzGWhjkSUQBGJ0IHUGHZKVJ1i/d3G16YUFCGIA==
-----END CERTIFICATE-----
"""

# `openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256`
# over the certificate above: the pipeline the tutorial and the issue give.
OPENSSL_PIN = "sha256:4120c34c5d6046351a71168fd02adb6dcd18abe88fed725e887887e8d26a3041"


def _der(pem: bytes) -> bytes:
    return x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)


def _reencoded_pin(der: bytes) -> str:
    key = x509.load_der_x509_certificate(der).public_key()
    spki = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "sha256:" + hashlib.sha256(spki).hexdigest()


def _self_signed(key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> bytes:
    """A v3 certificate, with extensions, so its TBSCertificate opens with the version field."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "payments")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.DER)


class TestExplicitCurveParameters:
    def test_the_pin_matches_the_openssl_pipeline(self) -> None:
        assert peer_key_pin(_der(EXPLICIT_PARAMETERS)) == OPENSSL_PIN

    def test_a_re_encoded_key_would_not(self) -> None:
        # What core computed before: the named-curve encoding of the same key.
        assert _reencoded_pin(_der(EXPLICIT_PARAMETERS)) != OPENSSL_PIN


class TestKeysThatEncodeOneWay:
    @pytest.mark.parametrize(
        "key",
        [ec.generate_private_key(ec.SECP256R1()), rsa.generate_private_key(public_exponent=65537, key_size=2048)],
        ids=["ec-named-curve", "rsa"],
    )
    def test_the_pin_is_unchanged(self, key) -> None:
        der = _self_signed(key)

        assert peer_key_pin(der) == _reencoded_pin(der)
