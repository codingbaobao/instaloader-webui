import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "instaloader_webui"
GATEWAY_PATH = PACKAGE_ROOT / "instagram" / "profile_lookup.py"


def _from_username_references(source_path: Path) -> tuple[int, ...]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
    return tuple(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "from_username"
    )


@pytest.mark.parametrize(
    "source",
    [
        (
            "from instaloader import Profile as InstaloaderProfile\n"
            "InstaloaderProfile.from_username(context, username)\n"
        ),
        (
            "import instaloader as upstream\n"
            "upstream.Profile.from_username(context, username)\n"
        ),
        (
            "from instaloader import Profile\n"
            "lookup = Profile.from_username\n"
            "lookup(context, username)\n"
        ),
    ],
)
def test_boundary_guard_detects_aliased_upstream_lookup(
    tmp_path: Path,
    source: str,
) -> None:
    source_path = tmp_path / "bypass.py"
    source_path.write_text(source, encoding="utf-8")

    assert _from_username_references(source_path) == (2,)


def test_production_profile_lookups_cannot_bypass_gateway() -> None:
    # Break caught: a new direct Profile.from_username() call silently omits
    # the application's guarded fallback behavior.
    violations: list[str] = []
    for source_path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if source_path == GATEWAY_PATH:
            continue
        violations.extend(
            f"{source_path.relative_to(PACKAGE_ROOT)}:{line_number}"
            for line_number in _from_username_references(source_path)
        )

    assert violations == []
