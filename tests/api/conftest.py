"""Shared fixtures for the api tests."""

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest

from custom_components.tion.api.nats import TaskFactory


def _eager_task(coro: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
    """Start a task eagerly, as Home Assistant's background tasks do."""
    loop = asyncio.get_running_loop()
    return asyncio.Task(coro, loop=loop, name=name, eager_start=True)


_TASK_FACTORIES: dict[str, TaskFactory | None] = {"lazy": None, "eager": _eager_task}


@pytest.fixture(params=list(_TASK_FACTORIES))
def create_task(request: pytest.FixtureRequest) -> TaskFactory | None:
    """Run with the default task factory and with an eager one."""
    return _TASK_FACTORIES[request.param]
