import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "instaloader_webui"
GATEWAY_PATH = PACKAGE_ROOT / "instagram" / "profile_lookup.py"


def test_production_profile_lookups_cannot_bypass_gateway() -> None:
    # Break caught: a new direct Profile.from_username() call silently omits
    # the application's guarded fallback behavior.
    violations: list[str] = []
    for source_path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if source_path == GATEWAY_PATH:
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"), source_path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "from_username"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "Profile"
            ):
                violations.append(
                    f"{source_path.relative_to(PACKAGE_ROOT)}:{node.lineno}"
                )

    assert violations == []
