"""Tests for the captcha page that resumes the config flow."""

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestClient
import pytest
import pytest_asyncio

from custom_components.tion.captcha_view import (
    CAPTCHA_SITEKEY,
    TionCaptchaView,
    async_register_captcha_view,
    captcha_page_url,
    captcha_url,
    parse_captcha_base_url,
)
from custom_components.tion.const import CONF_CAPTCHA_TOKEN, DOMAIN
from homeassistant.components.http import KEY_HASS
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.config_entry_oauth2_flow import HEADER_FRONTEND_BASE
from homeassistant.helpers.http import current_request

# The views are served by a local aiohttp test server.
pytestmark = pytest.mark.usefixtures("socket_enabled")

FLOW_ID = "0123456789abcdef0123456789abcdef"


class FakeFlowManager:
    """Flows in progress keyed by id; configure moves the flow to `code`."""

    def __init__(self, flows: dict[str, dict[str, Any]]) -> None:
        """Store the flows in progress."""
        self.flows = flows
        self.configured: list[tuple[str, dict[str, Any]]] = []

    def async_get(self, flow_id: str) -> dict[str, Any]:
        """Return a flow in progress or raise UnknownFlow."""
        if flow_id not in self.flows:
            raise UnknownFlow
        return self.flows[flow_id]

    async def async_configure(
        self, flow_id: str, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Record the input and advance the flow."""
        self.configured.append((flow_id, user_input))
        self.flows[flow_id] = {**self.flows[flow_id], "step_id": "code"}
        return self.flows[flow_id]


class FakeHass:
    """The parts of hass the view and its registration use."""

    def __init__(self, flows: dict[str, dict[str, Any]]) -> None:
        """Initialize with flows in progress."""
        self.data: dict[str, Any] = {}
        self.is_stopping = False
        self.config_entries = SimpleNamespace(flow=FakeFlowManager(flows))
        self.registered: list[TionCaptchaView] = []
        self.http = SimpleNamespace(register_view=self.registered.append)

    async def async_add_executor_job(
        self, target: Callable[..., Any], *args: Any
    ) -> Any:
        """Run the job inline."""
        return target(*args)


def _tion_flow(step_id: str = "captcha") -> dict[str, Any]:
    return {"flow_id": FLOW_ID, "handler": DOMAIN, "step_id": step_id}


@pytest.fixture
def hass() -> FakeHass:
    """Return a fake hass with one Tion flow waiting at the captcha step."""
    return FakeHass({FLOW_ID: _tion_flow()})


@pytest_asyncio.fixture
async def client(hass: FakeHass, aiohttp_client: Callable[..., Any]) -> TestClient:
    """Serve the captcha view on a test server."""
    app = web.Application()
    app[KEY_HASS] = hass
    TionCaptchaView().register(hass, app, app.router)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_get_serves_page_with_sitekey_and_post_url(client: TestClient) -> None:
    """The page embeds the sitekey and posts back to its own flow URL."""
    response = await client.get(captcha_url(FLOW_ID))

    assert response.status == 200
    assert response.content_type == "text/html"
    page = await response.text()
    assert f'const SITEKEY = "{CAPTCHA_SITEKEY}";' in page
    assert f'const POST_URL = "{captcha_url(FLOW_ID)}";' in page
    assert "__SITEKEY__" not in page
    assert "smartcaptcha.cloud.yandex.ru/captcha.js" in page


@pytest.mark.asyncio
async def test_page_served_for_localhost_host_header(client: TestClient) -> None:
    """Through a port forward the page arrives with Host: localhost and is served."""
    response = await client.get(captcha_url(FLOW_ID), headers={"Host": "localhost"})

    assert response.status == 200
    assert f'const SITEKEY = "{CAPTCHA_SITEKEY}";' in await response.text()


@pytest.mark.asyncio
async def test_post_token_resumes_flow(hass: FakeHass, client: TestClient) -> None:
    """POSTing the token configures the flow with it."""
    response = await client.post(captcha_url(FLOW_ID), json={"token": "dD0xtoken"})

    assert response.status == 204
    assert hass.config_entries.flow.configured == [
        (FLOW_ID, {CONF_CAPTCHA_TOKEN: "dD0xtoken"})
    ]


@pytest.mark.asyncio
async def test_second_post_is_rejected(hass: FakeHass, client: TestClient) -> None:
    """Once the flow left the captcha step the URL stops accepting tokens."""
    await client.post(captcha_url(FLOW_ID), json={"token": "first"})

    response = await client.post(captcha_url(FLOW_ID), json={"token": "second"})

    assert response.status == 404
    assert len(hass.config_entries.flow.configured) == 1


@pytest.mark.parametrize(
    "flows",
    [
        pytest.param({}, id="unknown_flow"),
        pytest.param({FLOW_ID: _tion_flow("code")}, id="tion_flow_not_at_captcha"),
        pytest.param(
            {FLOW_ID: {"flow_id": FLOW_ID, "handler": "other", "step_id": "captcha"}},
            id="other_integration_flow",
        ),
    ],
)
@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.asyncio
async def test_only_tion_flows_at_captcha_step_are_served(
    flows: dict[str, dict[str, Any]],
    method: str,
    hass: FakeHass,
    client: TestClient,
) -> None:
    """Any other flow id is a 404 and is never configured."""
    hass.config_entries.flow.flows = flows

    response = await client.request(
        method, captcha_url(FLOW_ID), json={"token": "dD0xtoken"}
    )

    assert response.status == 404
    # A typed body, so a browser shows the error instead of saving an empty file.
    assert response.content_type == "text/plain"
    assert await response.text() == "404: Not Found"
    assert hass.config_entries.flow.configured == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"not json", id="not_json"),
        pytest.param(b'["token"]', id="not_object"),
        pytest.param(b"{}", id="missing_token"),
        pytest.param(b'{"token": ""}', id="empty_token"),
        pytest.param(b'{"token": 5}', id="non_string_token"),
        pytest.param(b'{"token": "' + b"a" * 4097 + b'"}', id="oversized_token"),
    ],
)
@pytest.mark.asyncio
async def test_bad_post_body_is_rejected(
    body: bytes, hass: FakeHass, client: TestClient
) -> None:
    """Malformed bodies get 400 and leave the flow waiting."""
    response = await client.post(
        captcha_url(FLOW_ID),
        data=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status == 400
    assert hass.config_entries.flow.configured == []


@pytest.mark.asyncio
async def test_flow_gone_during_configure_is_404(
    hass: FakeHass, client: TestClient
) -> None:
    """A flow aborted between the check and the configure call is a 404."""

    async def gone(flow_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        raise UnknownFlow

    hass.config_entries.flow.async_configure = gone

    response = await client.post(captcha_url(FLOW_ID), json={"token": "dD0xtoken"})

    assert response.status == 404


@pytest.mark.asyncio
async def test_concurrent_post_is_rejected(hass: FakeHass, client: TestClient) -> None:
    """A second POST arriving while the first is configuring gets 404."""
    configure_event = asyncio.Event()

    async def blocking_configure(
        flow_id: str, user_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Block until released."""
        hass.config_entries.flow.configured.append((flow_id, user_input))
        await configure_event.wait()
        hass.config_entries.flow.flows[flow_id] = {
            **hass.config_entries.flow.flows[flow_id],
            "step_id": "code",
        }
        return hass.config_entries.flow.flows[flow_id]

    hass.config_entries.flow.async_configure = blocking_configure

    # Send first POST and let it start blocking in async_configure
    first_task = asyncio.create_task(
        client.post(captcha_url(FLOW_ID), json={"token": "first"})
    )
    await asyncio.sleep(0.01)

    # Send second POST while first is blocked
    response = await client.post(captcha_url(FLOW_ID), json={"token": "second"})

    assert response.status == 404
    assert len(hass.config_entries.flow.configured) == 1
    assert hass.config_entries.flow.configured[0] == (
        FLOW_ID,
        {CONF_CAPTCHA_TOKEN: "first"},
    )

    # Let first POST complete
    configure_event.set()
    first_response = await first_task
    assert first_response.status == 204


def test_register_view_once(hass: FakeHass) -> None:
    """Repeated flows register the view only once per run."""
    async_register_captcha_view(hass)
    async_register_captcha_view(hass)

    assert len(hass.registered) == 1
    assert isinstance(hass.registered[0], TionCaptchaView)
    assert TionCaptchaView.requires_auth is False


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        pytest.param(
            {HEADER_FRONTEND_BASE: "http://localhost:8123"},
            f"http://localhost:8123/api/tion/captcha/{FLOW_ID}",
            id="frontend_origin",
        ),
        pytest.param(
            {HEADER_FRONTEND_BASE: "https://ha.example.org/"},
            f"https://ha.example.org/api/tion/captcha/{FLOW_ID}",
            id="trailing_slash",
        ),
        pytest.param({}, f"/api/tion/captcha/{FLOW_ID}", id="no_header"),
    ],
)
def test_captcha_page_url_uses_frontend_origin(
    headers: dict[str, str], expected: str
) -> None:
    """The frontend opens only absolute external-step URLs, so use its origin."""
    token = current_request.set(SimpleNamespace(headers=headers))
    try:
        assert captcha_page_url(FLOW_ID) == expected
    finally:
        current_request.reset(token)


def test_captcha_page_url_without_request() -> None:
    """Outside an HTTP request the page path is the best available URL."""
    assert captcha_page_url(FLOW_ID) == f"/api/tion/captcha/{FLOW_ID}"


def test_captcha_page_url_with_base_ignores_request() -> None:
    """An explicit base replaces the frontend origin."""
    token = current_request.set(
        SimpleNamespace(headers={HEADER_FRONTEND_BASE: "http://192.168.1.10:8123"})
    )
    try:
        assert (
            captcha_page_url(FLOW_ID, "http://localhost")
            == f"http://localhost/api/tion/captcha/{FLOW_ID}"
        )
    finally:
        current_request.reset(token)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param("   ", None, id="blank"),
        pytest.param("http://localhost", "http://localhost", id="plain"),
        pytest.param("http://localhost/", "http://localhost", id="trailing_slash"),
        pytest.param(
            " https://HA.example.org:443 ",
            "https://HA.example.org:443",
            id="port_and_spaces",
        ),
        pytest.param("http://[::1]", "http://[::1]", id="ipv6"),
    ],
)
def test_parse_captcha_base_url(value: str | None, expected: str | None) -> None:
    """A bare http(s) origin is normalized; nothing means the frontend origin."""
    assert parse_captcha_base_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("localhost", id="no_scheme"),
        pytest.param("ftp://localhost", id="wrong_scheme"),
        pytest.param("http://", id="no_host"),
        pytest.param("http://localhost/api", id="path"),
        pytest.param("http://localhost/?x=1", id="query"),
        pytest.param("http://localhost#top", id="fragment"),
        pytest.param("http://user:pw@localhost", id="userinfo"),
    ],
)
def test_parse_captcha_base_url_rejects(value: str) -> None:
    """Anything but an http(s) origin is refused."""
    with pytest.raises(ValueError):
        parse_captcha_base_url(value)
