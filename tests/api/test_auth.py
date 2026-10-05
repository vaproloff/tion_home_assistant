"""Tests for the Tion v4 account session."""

import asyncio
import base64
from collections.abc import Callable
import hashlib
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
import pytest

from custom_components.tion.api.auth import (
    LOGIN_ERROR_CODE_EXPIRED,
    LOGIN_ERROR_INVALID_AUTH,
    LOGIN_ERROR_INVALID_CAPTCHA,
    LOGIN_ERROR_INVALID_CODE,
    LOGIN_ERROR_PASSWORD_NOT_SET,
    SVC_ACCOUNT,
    TionAuth,
    TionLoginError,
    TionTokens,
)
from custom_components.tion.api.device_key import TionDeviceKey, sha256_b64url
from custom_components.tion.api.exceptions import (
    TionApiError,
    TionAuthError,
    TionConnectionError,
)
from custom_components.tion.api.protobuf import (
    ProtoMessage,
    encode_bytes,
    encode_string,
    encode_varint,
)

NOW = 1_790_000_000.0
ACCESS_TTL = 900
REFRESH_TTL = 30 * 24 * 3600
EMAIL_TOKEN = b"signed-email-token"
LOGIN_TOKEN = b"signed-login-token"

Response = bytes | Exception | Callable[[], bytes]


class FakeTransport:
    """Scripted stand-in for TionTransport; `now` is the server clock."""

    def __init__(self, *responses: Response) -> None:
        """Queue the responses returned by successive calls."""
        self.responses = list(responses)
        self.calls: list[SimpleNamespace] = []
        self.now = NOW

    def server_time(self) -> float:
        """Return the fake server clock."""
        return self.now

    async def async_call(
        self, service: str, method: str, payload: bytes, token: str | None = None
    ) -> bytes:
        """Record the call and pop the next scripted response."""
        self.calls.append(
            SimpleNamespace(
                service=service,
                method=method,
                request=ProtoMessage.parse(payload),
                token=token,
            )
        )
        await asyncio.sleep(0)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response()
        return response


def _error(field: int, code: int, message: str = "nope") -> bytes:
    return encode_bytes(field, encode_varint(1, code) + encode_string(2, message))


def _timestamp(seconds: float) -> bytes:
    return encode_varint(1, int(seconds))


def _token(
    access: str = "access-2",
    renew: str | None = "renew-1",
    access_expires_at: float = NOW + ACCESS_TTL,
    refresh_expires_at: float | None = NOW + REFRESH_TTL,
) -> bytes:
    """Encode a GetTokenResponse/RenewAccessResponse carrying a Token."""
    token = encode_string(1, access) + encode_bytes(3, _timestamp(access_expires_at))
    if refresh_expires_at is not None:
        token += encode_bytes(4, _timestamp(refresh_expires_at))
    if renew is not None:
        token += encode_string(5, renew)
    return encode_bytes(1, token)


def _tokens(access_expires_in: float = ACCESS_TTL) -> TionTokens:
    return TionTokens(
        access_token="access-1",
        renew_session_token="renew-1",
        access_expires_at=NOW + access_expires_in,
        refresh_expires_at=NOW + REFRESH_TTL,
    )


def _sha256(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()


async def _auth_with_code_sent(transport: FakeTransport) -> TionAuth:
    auth = TionAuth(transport, TionDeviceKey.generate())
    await auth.async_get_confirmation_code("user@example.com", "secret", "captcha")
    return auth


@pytest.mark.asyncio
async def test_login_chain_sends_linked_verifiers_and_registers_key() -> None:
    """The three login calls carry the PKCE-like verifier chain and the key."""
    transport = FakeTransport(
        encode_bytes(1, EMAIL_TOKEN), encode_bytes(2, LOGIN_TOKEN), _token()
    )
    device_key = TionDeviceKey.generate()
    auth = TionAuth(transport, device_key)
    seen: list[TionTokens] = []
    auth.add_update_listener(seen.append)

    await auth.async_get_confirmation_code("user@example.com", "secret", "captcha")
    await auth.async_check_confirmation_code("6483")
    tokens = await auth.async_get_token()

    get_code, check_code, get_token = transport.calls
    assert [call.method for call in transport.calls] == [
        "GetConfirmationCode",
        "CheckConfirmationCode",
        "GetToken",
    ]
    assert {call.service for call in transport.calls} == {SVC_ACCOUNT}
    assert {call.token for call in transport.calls} == {None}

    request = get_code.request
    assert request.get_message(2).get_str(1) == "user@example.com"
    assert request.get_message(3).get_str(1) == "secret"
    assert request.get_str(4) == "captcha"
    assert request.get_int(6) == 5

    request = check_code.request
    email_verifier = request.get_str(3)
    assert _sha256(email_verifier) == get_code.request.get_bytes(5)
    assert request.get_int(1) == 6483
    assert request.get_bytes(2) == EMAIL_TOKEN
    assert request.get_int(5) == 5

    request = get_token.request
    assert _sha256(request.get_str(2)) == check_code.request.get_bytes(4)
    assert request.get_bytes(1) == LOGIN_TOKEN
    proof = request.get_message(3)
    assert proof.get_str(1) == "com.tion.magicair4"
    assert proof.get_str(2) == device_key.key_id()
    assert proof.get_str(3) == device_key.device_proof_public_key().public_key
    assert proof.get_str(4) == "x963"
    assert proof.get_str(5) == "ES256"
    assert proof.get_int(6) == 1
    assert proof.get_str(7) == "linux"
    assert proof.get_str(8) == "software"

    assert tokens == TionTokens(
        access_token="access-2",
        renew_session_token="renew-1",
        access_expires_at=NOW + ACCESS_TTL,
        refresh_expires_at=NOW + REFRESH_TTL,
    )
    assert auth.tokens == tokens
    assert auth.access_token == "access-2"
    assert seen == [tokens]


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        pytest.param(2, LOGIN_ERROR_INVALID_CAPTCHA, id="captcha"),
        pytest.param(3, LOGIN_ERROR_INVALID_AUTH, id="password"),
        pytest.param(4, LOGIN_ERROR_INVALID_AUTH, id="email"),
        pytest.param(5, LOGIN_ERROR_INVALID_AUTH, id="access_denied"),
    ],
)
@pytest.mark.asyncio
async def test_get_confirmation_code_rejections(code: int, reason: str) -> None:
    """Known GetConfirmationCode errors become config flow error keys."""
    auth = TionAuth(FakeTransport(_error(2, code)), TionDeviceKey.generate())

    with pytest.raises(TionLoginError) as exc_info:
        await auth.async_get_confirmation_code("user@example.com", "bad", "captcha")

    assert exc_info.value.reason == reason


@pytest.mark.asyncio
async def test_unmapped_login_error_is_api_error() -> None:
    """An unexpected server error code is an API error, not a login error."""
    auth = TionAuth(FakeTransport(_error(2, 1)), TionDeviceKey.generate())

    with pytest.raises(TionApiError):
        await auth.async_get_confirmation_code("user@example.com", "pw", "captcha")


@pytest.mark.asyncio
async def test_empty_login_response_is_api_error() -> None:
    """A response with neither result nor error is an API error."""
    auth = TionAuth(FakeTransport(b""), TionDeviceKey.generate())

    with pytest.raises(TionApiError):
        await auth.async_get_confirmation_code("user@example.com", "pw", "captcha")


@pytest.mark.parametrize(
    ("code", "sent"),
    [
        pytest.param(" 6483 ", 6483, id="spaces"),
        pytest.param("TI-0483", 483, id="prefix_and_leading_zero"),
    ],
)
@pytest.mark.asyncio
async def test_check_code_accepts_decorated_digits(code: str, sent: int) -> None:
    """Whitespace, a letter prefix or dashes around the digits are tolerated."""
    transport = FakeTransport(
        encode_bytes(1, EMAIL_TOKEN), encode_bytes(2, LOGIN_TOKEN)
    )
    auth = await _auth_with_code_sent(transport)

    await auth.async_check_confirmation_code(code)

    assert transport.calls[1].request.get_int(1) == sent


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("", id="empty"),
        pytest.param("abcd", id="letters"),
        pytest.param("99999999999", id="over_uint32"),
    ],
)
@pytest.mark.asyncio
async def test_check_code_rejects_non_numeric_without_calling(code: str) -> None:
    """A code that cannot be a uint32 fails locally as invalid_code."""
    transport = FakeTransport(encode_bytes(1, EMAIL_TOKEN))
    auth = await _auth_with_code_sent(transport)

    with pytest.raises(TionLoginError) as exc_info:
        await auth.async_check_confirmation_code(code)

    assert exc_info.value.reason == LOGIN_ERROR_INVALID_CODE
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        pytest.param(2, LOGIN_ERROR_INVALID_CODE, id="wrong_code"),
        pytest.param(3, LOGIN_ERROR_CODE_EXPIRED, id="email_token_expired"),
        pytest.param(6, LOGIN_ERROR_INVALID_AUTH, id="account_not_exists"),
    ],
)
@pytest.mark.asyncio
async def test_check_code_rejections(code: int, reason: str) -> None:
    """Known CheckConfirmationCode errors become config flow error keys."""
    transport = FakeTransport(encode_bytes(1, EMAIL_TOKEN), _error(3, code))
    auth = await _auth_with_code_sent(transport)

    with pytest.raises(TionLoginError) as exc_info:
        await auth.async_check_confirmation_code("1111")

    assert exc_info.value.reason == reason


@pytest.mark.asyncio
async def test_password_token_means_password_not_set() -> None:
    """A PasswordToken instead of a LoginToken means no permanent password."""
    transport = FakeTransport(encode_bytes(1, EMAIL_TOKEN), encode_bytes(1, b"pwd"))
    auth = await _auth_with_code_sent(transport)

    with pytest.raises(TionLoginError) as exc_info:
        await auth.async_check_confirmation_code("1111")

    assert exc_info.value.reason == LOGIN_ERROR_PASSWORD_NOT_SET


@pytest.mark.asyncio
async def test_code_retry_reuses_email_verifier() -> None:
    """After a wrong code the same EmailToken and verifier are sent again."""
    transport = FakeTransport(
        encode_bytes(1, EMAIL_TOKEN), _error(3, 2), encode_bytes(2, LOGIN_TOKEN)
    )
    auth = await _auth_with_code_sent(transport)
    with pytest.raises(TionLoginError):
        await auth.async_check_confirmation_code("1111")

    await auth.async_check_confirmation_code("2222")

    first, second = transport.calls[1].request, transport.calls[2].request
    assert first.get_str(3) == second.get_str(3)
    assert second.get_bytes(2) == EMAIL_TOKEN


@pytest.mark.asyncio
async def test_get_token_login_token_expired_needs_new_login() -> None:
    """An expired LoginToken is reported so the flow can restart the login."""
    transport = FakeTransport(
        encode_bytes(1, EMAIL_TOKEN), encode_bytes(2, LOGIN_TOKEN), _error(2, 2)
    )
    auth = await _auth_with_code_sent(transport)
    await auth.async_check_confirmation_code("1111")

    with pytest.raises(TionLoginError) as exc_info:
        await auth.async_get_token()

    assert exc_info.value.reason == LOGIN_ERROR_CODE_EXPIRED
    assert auth.tokens is None


@pytest.mark.parametrize(
    ("response", "error"),
    [
        pytest.param(_error(2, 5), TionApiError, id="get_token_error"),
        pytest.param(_token(renew=None), TionApiError, id="incomplete_token"),
    ],
)
@pytest.mark.asyncio
async def test_get_token_failures(response: bytes, error: type[Exception]) -> None:
    """GetToken rejections and incomplete tokens never yield a session."""
    transport = FakeTransport(
        encode_bytes(1, EMAIL_TOKEN), encode_bytes(2, LOGIN_TOKEN), response
    )
    auth = await _auth_with_code_sent(transport)
    await auth.async_check_confirmation_code("1111")

    with pytest.raises(error):
        await auth.async_get_token()

    assert auth.tokens is None


@pytest.mark.asyncio
async def test_steps_out_of_order_raise() -> None:
    """Checking a code before requesting one is a programming error."""
    auth = TionAuth(FakeTransport(), TionDeviceKey.generate())

    with pytest.raises(RuntimeError):
        await auth.async_check_confirmation_code("1111")
    with pytest.raises(RuntimeError):
        await auth.async_get_token()


@pytest.mark.asyncio
async def test_ensure_valid_keeps_fresh_token() -> None:
    """A token far from expiry is returned without a call."""
    transport = FakeTransport()
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    assert await auth.async_ensure_valid() == "access-1"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_ensure_valid_renews_near_expiry_with_signed_proof() -> None:
    """Under the threshold RenewAccess is sent with Bearer and an ES256 proof."""
    transport = FakeTransport(_token(renew=None, refresh_expires_at=None))
    device_key = TionDeviceKey.generate()
    auth = TionAuth(transport, device_key, _tokens(access_expires_in=30))
    seen: list[TionTokens] = []
    auth.add_update_listener(seen.append)

    assert await auth.async_ensure_valid() == "access-2"

    (call,) = transport.calls
    assert call.method == "RenewAccess"
    assert call.token == "access-1"
    request = call.request
    assert request.get_str(1) == "renew-1"
    assert request.get_str(2) == "com.tion.magicair4"
    assert request.get_str(3) == device_key.key_id()
    assert request.get_int(4) == int(NOW)
    nonce = request.get_str(5)
    assert len(nonce) == 32
    assert request.get_str(7) == sha256_b64url("access-1")
    assert request.get_str(9) == "der"
    assert request.get_str(10) == "ES256"
    payload = (
        "ma4-renew-access-v1\nappId=com.tion.magicair4\n"
        f"keyId={device_key.key_id()}\n"
        f"renewSessionTokenHash={sha256_b64url('renew-1')}\n"
        f"accessTokenHash={sha256_b64url('access-1')}\n"
        f"ts={int(NOW)}\nnonce={nonce}"
    ).encode()
    signature = request.get_str(8)
    der = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), device_key.public_x963()
    )
    public_key.verify(der, payload, ec.ECDSA(hashes.SHA256()))
    # The renewal omitted the renew token and window: the old ones are kept.
    assert auth.tokens == TionTokens(
        access_token="access-2",
        renew_session_token="renew-1",
        access_expires_at=NOW + ACCESS_TTL,
        refresh_expires_at=NOW + REFRESH_TTL,
    )
    assert seen == [auth.tokens]


@pytest.mark.asyncio
async def test_ensure_valid_uses_server_clock() -> None:
    """Expiry and the proof timestamp follow the server clock, not the local one."""
    transport = FakeTransport(_token())
    transport.now = NOW + ACCESS_TTL - 10
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    await auth.async_ensure_valid()

    assert transport.calls[0].request.get_int(4) == int(NOW + ACCESS_TTL - 10)


@pytest.mark.asyncio
async def test_concurrent_ensure_valid_renews_once() -> None:
    """Parallel callers near expiry share a single RenewAccess."""
    transport = FakeTransport(_token())
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens(access_expires_in=5))

    results = await asyncio.gather(auth.async_ensure_valid(), auth.async_ensure_valid())

    assert results == ["access-2", "access-2"]
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_expired_refresh_window_requires_login() -> None:
    """After the 30-day window no renewal is attempted."""
    transport = FakeTransport()
    transport.now = NOW + REFRESH_TTL
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    with pytest.raises(TionAuthError):
        await auth.async_ensure_valid()

    assert transport.calls == []


@pytest.mark.asyncio
async def test_not_logged_in_is_auth_error() -> None:
    """A session without tokens cannot produce an access token."""
    auth = TionAuth(FakeTransport(), TionDeviceKey.generate())

    with pytest.raises(TionAuthError):
        await auth.async_ensure_valid()


@pytest.mark.parametrize(
    ("code", "error"),
    [
        pytest.param(1, TionAuthError, id="invalid_session"),
        pytest.param(3, TionAuthError, id="replay_detected"),
        pytest.param(4, TionAuthError, id="renew_token_error"),
        pytest.param(0, TionApiError, id="unknown"),
    ],
)
@pytest.mark.asyncio
async def test_renew_rejections(code: int, error: type[Exception]) -> None:
    """Terminal renew errors demand a new login; others are API errors."""
    transport = FakeTransport(_error(2, code))
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    with pytest.raises(error):
        await auth.async_renew_access()

    assert auth.tokens == _tokens()
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_invalid_device_proof_retried_once_with_resynced_clock() -> None:
    """InvalidDeviceProof is retried once with a fresh ts and nonce."""
    transport = FakeTransport()

    def resync_then_reject() -> bytes:
        transport.now = NOW + 120
        return _error(2, 2)

    transport.responses = [resync_then_reject, _token()]
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    tokens = await auth.async_renew_access()

    assert tokens.access_token == "access-2"
    first, second = (call.request for call in transport.calls)
    assert first.get_int(4) == int(NOW)
    assert second.get_int(4) == int(NOW + 120)
    assert first.get_str(5) != second.get_str(5)


@pytest.mark.asyncio
async def test_repeated_invalid_device_proof_is_terminal() -> None:
    """A second InvalidDeviceProof gives up with an auth error."""
    transport = FakeTransport(_error(2, 2), _error(2, 2))
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    with pytest.raises(TionAuthError):
        await auth.async_renew_access()

    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_renew_connection_error_keeps_tokens() -> None:
    """A network failure propagates and leaves the session untouched."""
    transport = FakeTransport(TionConnectionError("down"))
    auth = TionAuth(transport, TionDeviceKey.generate(), _tokens())

    with pytest.raises(TionConnectionError):
        await auth.async_renew_access()

    assert auth.tokens == _tokens()


@pytest.mark.asyncio
async def test_listener_unsubscribe() -> None:
    """A removed listener is no longer called."""
    auth = TionAuth(FakeTransport(_token()), TionDeviceKey.generate(), _tokens())
    seen: list[TionTokens] = []
    unsubscribe = auth.add_update_listener(seen.append)

    unsubscribe()
    await auth.async_renew_access()

    assert seen == []


def test_tokens_dict_roundtrip() -> None:
    """Tokens survive a trip through their config entry dict."""
    tokens = _tokens()

    assert tokens.as_dict() == {
        "access_token": "access-1",
        "renew_session_token": "renew-1",
        "access_expires_at": NOW + ACCESS_TTL,
        "refresh_expires_at": NOW + REFRESH_TTL,
    }
    assert TionTokens.from_dict(tokens.as_dict()) == tokens


def test_tokens_repr_hides_secrets() -> None:
    """The repr() of tokens does not expose the secret strings."""
    tokens = TionTokens(
        access_token="secret-access",
        renew_session_token="secret-renew",
        access_expires_at=1.0,
        refresh_expires_at=2.0,
    )

    token_repr = repr(tokens)
    assert "secret-access" not in token_repr
    assert "secret-renew" not in token_repr
    assert "access_expires_at" in token_repr
