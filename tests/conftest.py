"""Run the tests on Home Assistant core's test harness."""

import importlib.util
import os
from pathlib import Path
import sys

_CORE = Path(os.environ.get("HA_CORE_DIR", "/workspaces/homeassistant-core"))

if not (_CORE / "tests" / "conftest.py").is_file():
    raise RuntimeError(
        f"Home Assistant core checkout not found at {_CORE}; set HA_CORE_DIR"
    )

# Core's harness imports its own modules relatively, so it loads under another
# name and does not clash with this repository's tests package.
_spec = importlib.util.spec_from_file_location(
    "ha_tests",
    _CORE / "tests" / "__init__.py",
    submodule_search_locations=[str(_CORE / "tests")],
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["ha_tests"] = _module
_spec.loader.exec_module(_module)

pytest_plugins = ["ha_tests.conftest"]
