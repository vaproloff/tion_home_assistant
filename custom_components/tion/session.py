"""Tion v4 cloud objects bound to Home Assistant's shared HTTP client."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.auth import TionAuth, TionTokens
from .api.device_key import TionDeviceKey
from .api.transport import TionTransport, create_ssl_context


async def async_create_transport(hass: HomeAssistant) -> TionTransport:
    """Create a transport on Home Assistant's shared HTTP client."""
    ssl_context = await hass.async_add_executor_job(create_ssl_context)
    return TionTransport(async_get_clientsession(hass), ssl_context)


async def async_create_auth(
    hass: HomeAssistant,
    device_key: TionDeviceKey,
    tokens: TionTokens | None = None,
) -> TionAuth:
    """Create a session on Home Assistant's shared HTTP client."""
    return TionAuth(await async_create_transport(hass), device_key, tokens)
