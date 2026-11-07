"""No code uses ``rx.icon`` or ``rx.moment``: they go through their classes (``Icon.create``,
``Moment.create``, the same functions at runtime).

07.11.2026: reflex_components_lucide and reflex_components_moment have a submodule and a function of
the same name (``icon = Icon.create`` after ``from .icon import Icon``). After any change of the code,
mypy's incremental run took the submodule for ``rx.icon`` and reported "Module not callable" at every
call (242 times); only a run without the cache was right (40 instead of 9 seconds)."""

from __future__ import annotations

import ast
from pathlib import Path

AMBIGUOUS = {"icon", "moment"}
ROOT = Path(__file__).resolve().parents[1]


def test_no_code_names_rx_icon_or_rx_moment() -> None:
    found = []
    for path in sorted((ROOT / "aifred").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in AMBIGUOUS and isinstance(node.value, ast.Name) and node.value.id == "rx":
                found.append(f"{path.relative_to(ROOT)}:{node.lineno} rx.{node.attr}")
    assert found == [], "use Icon.create / Moment.create:\n" + "\n".join(found)
