"""The v4 cloud client must not depend on Home Assistant."""

import ast
from pathlib import Path

import pytest

API_DIR = Path(__file__).parents[2] / "custom_components" / "tion" / "api"
_FORBIDDEN_ROOTS = {"homeassistant", "custom_components"}


def _forbidden_imports(path: Path) -> list[str]:
    """Return imports that reach Home Assistant or the integration outside api/."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level > 1:
            found.append("." * node.level + (node.module or ""))
            continue
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        else:
            continue
        found.extend(name for name in names if name.split(".")[0] in _FORBIDDEN_ROOTS)
    return found


def test_api_package_is_scanned() -> None:
    """Guard against the scan silently matching no files."""
    assert (API_DIR / "auth.py").is_file()


@pytest.mark.parametrize(
    "path", sorted(API_DIR.glob("*.py")), ids=lambda path: path.name
)
def test_api_module_is_home_assistant_free(path: Path) -> None:
    """No module in api/ imports Home Assistant or the integration root."""
    assert _forbidden_imports(path) == []
