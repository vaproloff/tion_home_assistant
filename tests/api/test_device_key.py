"""Tests for the Tion v4 device key."""

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from custom_components.tion.api.device_key import (
    APP_ID,
    TionDeviceKey,
    b64url,
    sha256_b64url,
)

# Private scalar 1: the public key is the curve generator, so the X9.63 point
# and its thumbprint are fixed, known values.
KNOWN_KEY_ID = "aYvqY9xEo0RmP_FCmuoQhC3ye2uZHvJYZrLGwCzcxb4"
KNOWN_X963_HEX = (
    "046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
    "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
)
KNOWN_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgAAAAAAAAAAAAAAAA\n"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAGhRANCAARrF9Hy4SxCR/i85uVjpEDydwN9gS3r\n"
    "M6D0oTlF2JjClk/jQuL+Gn+bjufrSnwPnhYrzjNXazFezsu2QGg3v1H1\n"
    "-----END PRIVATE KEY-----\n"
)


def _known_key() -> TionDeviceKey:
    return TionDeviceKey(ec.derive_private_key(1, ec.SECP256R1()))


def test_b64url_is_unpadded_urlsafe() -> None:
    """base64url drops padding and uses the URL-safe alphabet."""
    assert b64url(b"\xfb\xff") == "-_8"
    assert sha256_b64url("access") == "oFYf1knNtrqnhAVfBRuteW6gr-8X_KOCGVSd7rpOjBo"


def test_known_key_public_point_and_key_id() -> None:
    """The keyId is base64url(sha256(x963 public point))."""
    key = _known_key()

    assert key.public_x963().hex() == KNOWN_X963_HEX
    assert key.key_id() == KNOWN_KEY_ID


def test_pem_roundtrip_keeps_identity() -> None:
    """A key restored from its PEM has the same keyId."""
    key = _known_key()

    assert key.to_pem() == KNOWN_PEM
    assert TionDeviceKey.from_pem(KNOWN_PEM).key_id() == KNOWN_KEY_ID


def test_generate_makes_distinct_p256_keys() -> None:
    """Every generated key is a fresh 65-byte uncompressed P-256 point."""
    first, second = TionDeviceKey.generate(), TionDeviceKey.generate()

    assert first.key_id() != second.key_id()
    assert len(first.public_x963()) == 65
    assert first.public_x963()[0] == 0x04


def test_from_pem_rejects_garbage() -> None:
    """A corrupt PEM raises instead of yielding a broken key."""
    with pytest.raises(ValueError):
        TionDeviceKey.from_pem("not a pem")


def test_rejects_non_p256_key() -> None:
    """Only P-256 keys can be registered as ES256."""
    with pytest.raises(TypeError):
        TionDeviceKey(ec.generate_private_key(ec.SECP384R1()))


def test_device_proof_public_key_fields() -> None:
    """GetToken registration declares the key as a hardware-backed ES256 x963."""
    proof = _known_key().device_proof_public_key()

    assert proof.app_id == APP_ID == "com.tion.magicair4"
    assert proof.key_id == KNOWN_KEY_ID
    assert proof.public_key == b64url(bytes.fromhex(KNOWN_X963_HEX))
    assert proof.public_key_format == "x963"
    assert proof.algorithm == "ES256"
    assert proof.hardware_backed is True
    assert proof.platform == "linux"
    assert proof.key_origin == "software"


def test_sign_renew_access_signs_the_canonical_seven_lines() -> None:
    """The DER signature verifies against the exact canonical string."""
    key = _known_key()
    expected_payload = (
        "ma4-renew-access-v1\n"
        "appId=com.tion.magicair4\n"
        f"keyId={KNOWN_KEY_ID}\n"
        f"renewSessionTokenHash={sha256_b64url('renew')}\n"
        f"accessTokenHash={sha256_b64url('access')}\n"
        "ts=1790244324\n"
        "nonce=nonce-value"
    ).encode()

    signature = key.sign_renew_access("renew", "access", 1790244324, "nonce-value")

    der = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    assert der[0] == 0x30  # DER SEQUENCE, not raw r||s
    public_key = ec.derive_private_key(1, ec.SECP256R1()).public_key()
    public_key.verify(der, expected_payload, ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        public_key.verify(der, expected_payload + b"x", ec.ECDSA(hashes.SHA256()))
