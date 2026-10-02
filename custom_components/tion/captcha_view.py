"""Captcha page that resumes the Tion config flow's external step."""

from http import HTTPStatus
import json
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import UnknownFlow

from .const import CONF_CAPTCHA_TOKEN, DOMAIN

CAPTCHA_PATH = "/api/tion/captcha"
CAPTCHA_SITEKEY = "ysc1_1957yv82UnvMkTSRqfnjpTYhNHo8TUSsbhbHdZCrb4b4a76f"
CAPTCHA_STEP_ID = "captcha"
MAX_TOKEN_LENGTH = 4096
_TEMPLATE_PATH = Path(__file__).parent / "captcha.html"
_DATA_VIEW_REGISTERED = f"{DOMAIN}_captcha_view_registered"


def captcha_url(flow_id: str) -> str:
    """Return the captcha page path for a flow."""
    return f"{CAPTCHA_PATH}/{flow_id}"


@callback
def async_register_captcha_view(hass: HomeAssistant) -> None:
    """Register the captcha view once per Home Assistant run."""
    # Config flows start without the integration's async_setup having run,
    # so the flow registers the view itself on first use.
    if hass.data.get(_DATA_VIEW_REGISTERED):
        return
    hass.http.register_view(TionCaptchaView())
    hass.data[_DATA_VIEW_REGISTERED] = True


def _captcha_flow_open(hass: HomeAssistant, flow_id: str) -> bool:
    """Return True if flow_id is a Tion flow waiting at the captcha step."""
    try:
        flow = hass.config_entries.flow.async_get(flow_id)
    except UnknownFlow:
        return False
    return flow["handler"] == DOMAIN and flow.get("step_id") == CAPTCHA_STEP_ID


class TionCaptchaView(HomeAssistantView):
    """Serve the SmartCaptcha page and accept its token."""

    url = f"{CAPTCHA_PATH}/{{flow_id}}"
    name = "api:tion:captcha"
    # The browser opens this page from the external step without an HA token;
    # the random flow_id of an open Tion flow at the captcha step authorizes it.
    requires_auth = False

    def __init__(self) -> None:
        """Initialize the view."""
        self._template: str | None = None
        self._in_flight: set[str] = set()

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        """Return the captcha page."""
        hass = request.app[KEY_HASS]
        if not _captcha_flow_open(hass, flow_id):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        if self._template is None:
            self._template = await hass.async_add_executor_job(
                _TEMPLATE_PATH.read_text, "utf-8"
            )
        page = self._template.replace(
            "__SITEKEY__", json.dumps(CAPTCHA_SITEKEY)
        ).replace("__POST_URL__", json.dumps(captcha_url(flow_id)))
        return web.Response(text=page, content_type="text/html")

    async def post(self, request: web.Request, flow_id: str) -> web.Response:
        """Accept the captcha token and resume the flow."""
        hass = request.app[KEY_HASS]
        if not _captcha_flow_open(hass, flow_id):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            body = await request.json()
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_LENGTH:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        if flow_id in self._in_flight or not _captcha_flow_open(hass, flow_id):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        self._in_flight.add(flow_id)
        try:
            await hass.config_entries.flow.async_configure(
                flow_id=flow_id, user_input={CONF_CAPTCHA_TOKEN: token}
            )
        except UnknownFlow:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        finally:
            self._in_flight.discard(flow_id)
        return web.Response(status=HTTPStatus.NO_CONTENT)
