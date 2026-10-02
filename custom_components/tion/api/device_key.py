"""Tion v4 device key: P-256 key pair that proves this installation."""

import base64
from dataclasses import dataclass
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

APP_ID = "com.tion.magicair4"
RENEW_ACCESS_CONTEXT = "ma4-renew-access-v1"
KEY_FORMAT = "x963"
KEY_ALGORITHM = "ES256"
SIGNATURE_FORMAT = "der"
KEY_PLATFORM = "linux"
KEY_ORIGIN = "software"


def b64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sha256_b64url(text: str) -> str:
    """Return the unpadded base64url SHA-256 digest of a UTF-8 string."""
    return b64url(hashlib.sha256(text.encode()).digest())


@dataclass(frozen=True, slots=True)
class DeviceProofPublicKey:
    """Public key registration sent with GetToken."""

    app_id: str
    key_id: str
    public_key: str
    public_key_format: str
    algorithm: str
    hardware_backed: bool
    platform: str
    key_origin: str


class TionDeviceKey:
    """P-256 device key used to register with and sign for the Tion cloud."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        """Wrap a P-256 private key."""
        if not isinstance(private_key.curve, ec.SECP256R1):
            raise TypeError("device key must be a P-256 key")
        self._private_key = private_key
        self._public_x963 = private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        self._key_id = b64url(hashlib.sha256(self._public_x963).digest())

    @classmethod
    def generate(cls) -> TionDeviceKey:
        """Generate a fresh P-256 key."""
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @classmethod
    def from_pem(cls, pem: str) -> TionDeviceKey:
        """Load a key from an unencrypted PKCS8 PEM string."""
        private_key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise TypeError("device key must be an EC private key")
        return cls(private_key)

    def to_pem(self) -> str:
        """Serialize the private key as unencrypted PKCS8 PEM."""
        return self._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()

    def public_x963(self) -> bytes:
        """Return the uncompressed X9.63 public point (65 bytes, 0x04 prefix)."""
        return self._public_x963

    def key_id(self) -> str:
        """Return the key thumbprint: base64url(sha256(x963 public key))."""
        return self._key_id

    def device_proof_public_key(self) -> DeviceProofPublicKey:
        """Return the GetToken key registration."""
        # The server rejects keys that are not declared hardware-backed and has
        # no attestation, so a software key has to claim it (spec section 8).
        return DeviceProofPublicKey(
            app_id=APP_ID,
            key_id=self._key_id,
            public_key=b64url(self._public_x963),
            public_key_format=KEY_FORMAT,
            algorithm=KEY_ALGORITHM,
            hardware_backed=True,
            platform=KEY_PLATFORM,
            key_origin=KEY_ORIGIN,
        )

    def sign_renew_access(
        self, renew_session_token: str, access_token: str, ts: int, nonce: str
    ) -> str:
        """Sign the RenewAccess proof; return the DER signature as base64url."""
        payload = "\n".join(
            [
                RENEW_ACCESS_CONTEXT,
                f"appId={APP_ID}",
                f"keyId={self._key_id}",
                f"renewSessionTokenHash={sha256_b64url(renew_session_token)}",
                f"accessTokenHash={sha256_b64url(access_token)}",
                f"ts={ts}",
                f"nonce={nonce}",
            ]
        )
        der = self._private_key.sign(payload.encode(), ec.ECDSA(hashes.SHA256()))
        return b64url(der)
