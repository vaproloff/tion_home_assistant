"""Tion v4 cloud objects bound to Home Assistant's shared HTTP client."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.auth import TionAuth, TionTokens
from .api.cloud import TionCloud
from .api.device_key import TionDeviceKey
from .api.nats import TaskFactory
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


async def async_create_cloud(
    hass: HomeAssistant,
    device_key: TionDeviceKey,
    tokens: TionTokens,
    create_task: TaskFactory,
) -> tuple[TionAuth, TionCloud]:
    """Create a logged-in session and its cloud on one transport."""
    transport = await async_create_transport(hass)
    auth = TionAuth(transport, device_key, tokens)
    return auth, TionCloud(transport, auth, create_task=create_task)
