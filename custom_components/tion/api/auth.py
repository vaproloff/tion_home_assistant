"""Tion v4 account session: login chain, token renewal and persistence."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import logging
import re
import secrets
import string
from typing import Any

from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .device_key import (
    APP_ID,
    KEY_ALGORITHM,
    SIGNATURE_FORMAT,
    DeviceProofPublicKey,
    TionDeviceKey,
    b64url,
    sha256_b64url,
)
from .exceptions import TionApiError, TionAuthError
from .protobuf import ProtoMessage, encode_bytes, encode_string, encode_varint
from .transport import TionTransport, create_ssl_context

_LOGGER = logging.getLogger(__name__)

CONF_ACCESS_EXPIRES_AT = "access_expires_at"
CONF_REFRESH_EXPIRES_AT = "refresh_expires_at"
CONF_RENEW_SESSION_TOKEN = "renew_session_token"

SVC_ACCOUNT = "api.v1.user.account.AccountService"
CONFIRMATION_CODE_TYPE_LOGIN = 5
RENEW_THRESHOLD = 60
VERIFIER_LENGTH = 20
_VERIFIER_ALPHABET = string.ascii_letters + string.digits
_UINT32_MAX = 0xFFFFFFFF

LOGIN_ERROR_INVALID_AUTH = "invalid_auth"
LOGIN_ERROR_INVALID_CAPTCHA = "invalid_captcha"
LOGIN_ERROR_INVALID_CODE = "invalid_code"
LOGIN_ERROR_CODE_EXPIRED = "code_expired"
LOGIN_ERROR_PASSWORD_NOT_SET = "password_not_set"

# GetConfirmationCodeResponse.Error.ErrorCode
_GET_CODE_ERRORS = {
    2: LOGIN_ERROR_INVALID_CAPTCHA,  # CaptchaIsInvalid
    3: LOGIN_ERROR_INVALID_AUTH,  # PasswordIsInvalid
    4: LOGIN_ERROR_INVALID_AUTH,  # EmailIsInvalid
    5: LOGIN_ERROR_INVALID_AUTH,  # AccessDenied
}
# CheckConfirmationCodeResponse.Error.ErrorCode
_CHECK_CODE_ERRORS = {
    2: LOGIN_ERROR_INVALID_CODE,  # ConfirmationCodeIsInvalid
    3: LOGIN_ERROR_CODE_EXPIRED,  # EmailTokenIsExpired
    6: LOGIN_ERROR_INVALID_AUTH,  # AccountNotExists
}
# GetTokenResponse.Error.ErrorCode
_GET_TOKEN_ERRORS = {
    2: LOGIN_ERROR_CODE_EXPIRED,  # LoginTokenIsExpired
}
# RenewAccessResponse.Error.ErrorCode
_RENEW_INVALID_DEVICE_PROOF = 2
_RENEW_TERMINAL_ERRORS = frozenset(
    {
        1,  # InvalidSession
        _RENEW_INVALID_DEVICE_PROOF,
        3,  # ReplayDetected
        4,  # RenewTokenError
    }
)


class TionLoginError(TionAuthError):
    """A login step was rejected; reason is a config flow error key."""

    def __init__(self, reason: str, message: str = "") -> None:
        """Initialize with a config flow error key."""
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class TionTokens:
    """Session tokens; expiry times are Unix seconds on the server clock."""

    access_token: str
    renew_session_token: str
    access_expires_at: float
    refresh_expires_at: float

    def as_entry_data(self) -> dict[str, Any]:
        """Return the tokens as config entry data."""
        return {
            CONF_ACCESS_TOKEN: self.access_token,
            CONF_RENEW_SESSION_TOKEN: self.renew_session_token,
            CONF_ACCESS_EXPIRES_AT: self.access_expires_at,
            CONF_REFRESH_EXPIRES_AT: self.refresh_expires_at,
        }

    @classmethod
    def from_entry_data(cls, data: Mapping[str, Any]) -> TionTokens:
        """Build tokens from config entry data."""
        return cls(
            access_token=data[CONF_ACCESS_TOKEN],
            renew_session_token=data[CONF_RENEW_SESSION_TOKEN],
            access_expires_at=data[CONF_ACCESS_EXPIRES_AT],
            refresh_expires_at=data[CONF_REFRESH_EXPIRES_AT],
        )


def _verifier() -> str:
    return "".join(secrets.choice(_VERIFIER_ALPHABET) for _ in range(VERIFIER_LENGTH))


def _challenge(verifier: str) -> bytes:
    return hashlib.sha256(verifier.encode()).digest()


def _encode_device_proof(proof: DeviceProofPublicKey) -> bytes:
    return b"".join(
        [
            encode_string(1, proof.app_id),
            encode_string(2, proof.key_id),
            encode_string(3, proof.public_key),
            encode_string(4, proof.public_key_format),
            encode_string(5, proof.algorithm),
            encode_varint(6, int(proof.hardware_backed)),
            encode_string(7, proof.platform),
            encode_string(8, proof.key_origin),
        ]
    )


def _timestamp(message: ProtoMessage | None) -> float:
    """Decode a google.protobuf.Timestamp; 0 when absent."""
    if message is None:
        return 0.0
    return message.get_int(1) + message.get_int(2) / 1e9


def _parse_tokens(raw: ProtoMessage, previous: TionTokens | None) -> TionTokens:
    """Decode domain.entities.Token; a renewal may omit unchanged fields."""
    renew_session_token = raw.get_str(5)
    refresh_expires_at = _timestamp(raw.get_message(4))
    if previous is not None:
        renew_session_token = renew_session_token or previous.renew_session_token
        refresh_expires_at = refresh_expires_at or previous.refresh_expires_at
    tokens = TionTokens(
        access_token=raw.get_str(1),
        renew_session_token=renew_session_token,
        access_expires_at=_timestamp(raw.get_message(3)),
        refresh_expires_at=refresh_expires_at,
    )
    if not (
        tokens.access_token
        and tokens.renew_session_token
        and tokens.access_expires_at
        and tokens.refresh_expires_at
    ):
        raise TionApiError("Token response is missing fields")
    return tokens


def _error(raw: ProtoMessage) -> tuple[int, str]:
    """Decode a {Code = 1; Message = 2} error message."""
    return raw.get_int(1), raw.get_str(2)


class TionAuth:
    """Owns the Tion v4 account session and every AccountService call."""

    def __init__(
        self,
        transport: TionTransport,
        device_key: TionDeviceKey,
        tokens: TionTokens | None = None,
    ) -> None:
        """Initialize the session."""
        self._transport = transport
        self._device_key = device_key
        self._tokens = tokens
        self._listeners: list[Callable[[TionTokens], None]] = []
        self._renew_lock = asyncio.Lock()
        self._email_token: bytes | None = None
        self._email_verifier: str | None = None
        self._login_token: bytes | None = None
        self._login_verifier: str | None = None

    @property
    def device_key(self) -> TionDeviceKey:
        """Return the device key this session is bound to."""
        return self._device_key

    @property
    def tokens(self) -> TionTokens | None:
        """Return the current tokens, if logged in."""
        return self._tokens

    @property
    def access_token(self) -> str | None:
        """Return the current access token, if logged in."""
        return self._tokens.access_token if self._tokens else None

    def add_update_listener(
        self, listener: Callable[[TionTokens], None]
    ) -> Callable[[], None]:
        """Call listener with new tokens after login or renewal."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_get_confirmation_code(
        self, email: str, password: str, captcha_token: str
    ) -> None:
        """Step 1: send the e-mail code; keep the signed EmailToken."""
        self._email_token = self._login_token = self._login_verifier = None
        self._email_verifier = _verifier()
        response = await self._async_call(
            "GetConfirmationCode",
            b"".join(
                [
                    encode_bytes(2, encode_string(1, email)),
                    encode_bytes(3, encode_string(1, password)),
                    encode_string(4, captcha_token),
                    encode_bytes(5, _challenge(self._email_verifier)),
                    encode_varint(6, CONFIRMATION_CODE_TYPE_LOGIN),
                ]
            ),
        )
        if err := response.get_message(2):
            raise self._login_error(_GET_CODE_ERRORS, *_error(err))
        if not response.has(1):
            raise TionApiError("GetConfirmationCode: empty response")
        self._email_token = response.get_bytes(1)

    async def async_check_confirmation_code(self, code: str) -> None:
        """Step 2: exchange the e-mail code for a LoginToken."""
        if self._email_token is None or self._email_verifier is None:
            raise RuntimeError("async_get_confirmation_code must succeed first")
        digits = re.sub(r"\D", "", code)
        if not digits or int(digits) > _UINT32_MAX:
            raise TionLoginError(LOGIN_ERROR_INVALID_CODE, "code is not a number")
        login_verifier = _verifier()
        response = await self._async_call(
            "CheckConfirmationCode",
            b"".join(
                [
                    encode_varint(1, int(digits)),
                    encode_bytes(2, self._email_token),
                    encode_string(3, self._email_verifier),
                    encode_bytes(4, _challenge(login_verifier)),
                    encode_varint(5, CONFIRMATION_CODE_TYPE_LOGIN),
                ]
            ),
        )
        if err := response.get_message(3):
            raise self._login_error(_CHECK_CODE_ERRORS, *_error(err))
        if response.has(1):
            raise TionLoginError(LOGIN_ERROR_PASSWORD_NOT_SET)
        if not response.has(2):
            raise TionApiError("CheckConfirmationCode: empty response")
        self._login_token = response.get_bytes(2)
        self._login_verifier = login_verifier

    async def async_get_token(self) -> TionTokens:
        """Step 3: register the device key and get the session tokens."""
        if self._login_token is None or self._login_verifier is None:
            raise RuntimeError("async_check_confirmation_code must succeed first")
        response = await self._async_call(
            "GetToken",
            b"".join(
                [
                    encode_bytes(1, self._login_token),
                    encode_string(2, self._login_verifier),
                    encode_bytes(
                        3,
                        _encode_device_proof(
                            self._device_key.device_proof_public_key()
                        ),
                    ),
                ]
            ),
        )
        if err := response.get_message(2):
            raise self._login_error(_GET_TOKEN_ERRORS, *_error(err))
        if (raw := response.get_message(1)) is None:
            raise TionApiError("GetToken: empty response")
        tokens = _parse_tokens(raw, None)
        self._email_token = self._email_verifier = None
        self._login_token = self._login_verifier = None
        self._set_tokens(tokens)
        return tokens

    async def async_ensure_valid(self) -> str:
        """Return a valid access token, renewing it when close to expiry."""
        if self._needs_renewal():
            async with self._renew_lock:
                if self._needs_renewal():
                    await self._async_renew()
        return self._require_tokens().access_token

    async def async_renew_access(self) -> TionTokens:
        """Renew the access token now."""
        async with self._renew_lock:
            return await self._async_renew()

    def _require_tokens(self) -> TionTokens:
        if self._tokens is None:
            raise TionAuthError("Not logged in")
        return self._tokens

    def _needs_renewal(self) -> bool:
        tokens = self._require_tokens()
        return (
            tokens.access_expires_at - self._transport.server_time() < RENEW_THRESHOLD
        )

    async def _async_renew(self) -> TionTokens:
        tokens = self._require_tokens()
        if self._transport.server_time() >= tokens.refresh_expires_at:
            raise TionAuthError("Renew session expired, a new login is required")
        response = await self._async_call_renew(tokens)
        err = response.get_message(2)
        if err is not None and err.get_int(1) == _RENEW_INVALID_DEVICE_PROOF:
            # The rejected response already re-synced the server clock that
            # the proof timestamp is taken from, so one retry is worth it.
            _LOGGER.debug("RenewAccess: invalid device proof, retrying once")
            response = await self._async_call_renew(tokens)
        if (raw := response.get_message(1)) is not None:
            renewed = _parse_tokens(raw, tokens)
            self._set_tokens(renewed)
            return renewed
        if (err := response.get_message(2)) is None:
            raise TionApiError("RenewAccess: empty response")
        code, message = _error(err)
        if code in _RENEW_TERMINAL_ERRORS:
            raise TionAuthError(f"RenewAccess rejected ({code}): {message}")
        raise TionApiError(f"RenewAccess failed ({code}): {message}")

    async def _async_call_renew(self, tokens: TionTokens) -> ProtoMessage:
        """Send RenewAccess with a fresh timestamp, nonce and device proof."""
        ts = int(self._transport.server_time())
        nonce = b64url(secrets.token_bytes(24))
        signature = self._device_key.sign_renew_access(
            tokens.renew_session_token, tokens.access_token, ts, nonce
        )
        return await self._async_call(
            "RenewAccess",
            b"".join(
                [
                    encode_string(1, tokens.renew_session_token),
                    encode_string(2, APP_ID),
                    encode_string(3, self._device_key.key_id()),
                    encode_varint(4, ts),
                    encode_string(5, nonce),
                    encode_string(7, sha256_b64url(tokens.access_token)),
                    encode_string(8, signature),
                    encode_string(9, SIGNATURE_FORMAT),
                    encode_string(10, KEY_ALGORITHM),
                ]
            ),
            token=tokens.access_token,
        )

    def _set_tokens(self, tokens: TionTokens) -> None:
        self._tokens = tokens
        for listener in list(self._listeners):
            listener(tokens)

    async def _async_call(
        self, method: str, payload: bytes, token: str | None = None
    ) -> ProtoMessage:
        return ProtoMessage.parse(
            await self._transport.async_call(SVC_ACCOUNT, method, payload, token)
        )

    @staticmethod
    def _login_error(
        reasons: Mapping[int, str], code: int, message: str
    ) -> TionLoginError | TionApiError:
        if (reason := reasons.get(code)) is not None:
            return TionLoginError(reason, message)
        return TionApiError(f"Login rejected ({code}): {message}")


async def async_create_auth(
    hass: HomeAssistant,
    device_key: TionDeviceKey,
    tokens: TionTokens | None = None,
) -> TionAuth:
    """Create a session on Home Assistant's shared HTTP client."""
    ssl_context = await hass.async_add_executor_job(create_ssl_context)
    transport = TionTransport(async_get_clientsession(hass), ssl_context)
    return TionAuth(transport, device_key, tokens)
