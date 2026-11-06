"""Every ``self.<name>`` in a state class names something the composed state has.

mypy cannot check this: the mixins only meet in AIState, so mypy.ini switches
attr-defined off for aifred.state.*. A call to a method no mixin defines then
fails only at run time — on 06.11.2026 on_load stopped the page at
"wird initialisiert …" because _discover_vision_models had been deleted.
"""

import ast
import importlib
from pathlib import Path

import pytest
import reflex as rx

from aifred.state import AIState

_STATE_DIR = Path(__file__).resolve().parent.parent / "aifred" / "state"


def _state_classes() -> list[tuple[type, ast.ClassDef, Path]]:
    """Every rx.State class defined at module level in aifred/state/, with its syntax tree."""
    found = []
    for path in sorted(_STATE_DIR.glob("*.py")):
        module = importlib.import_module(f"aifred.state.{path.stem}")
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not isinstance(node, ast.ClassDef):
                continue
            cls = getattr(module, node.name)
            if isinstance(cls, type) and issubclass(cls, rx.State):
                found.append((cls, node, path))
    return found


_CLASSES = _state_classes()


@pytest.mark.parametrize(
    ("cls", "node", "path"), _CLASSES, ids=[f"{path.stem}.{cls.__name__}" for cls, _, path in _CLASSES]
)
def test_self_attributes_exist_on_composed_state(cls: type, node: ast.ClassDef, path: Path) -> None:
    # A mixin is only whole inside AIState; a standalone state is its own whole.
    composed = AIState if cls in AIState.__mro__ else cls
    missing = sorted({
        f"{path.name}:{sub.lineno} self.{sub.attr}"
        for sub in ast.walk(node)
        if isinstance(sub, ast.Attribute)
        and isinstance(sub.value, ast.Name)
        and sub.value.id == "self"
        and not hasattr(composed, sub.attr)
    })
    assert not missing, f"{composed.__name__} has no attribute for: " + ", ".join(missing)


def test_every_state_class_of_aistate_is_checked() -> None:
    """A mixin defined outside aifred/state/ would slip past the check above."""
    checked = {cls for cls, _, _ in _CLASSES}
    composed_from = {c for c in AIState.__mro__ if isinstance(c, type) and issubclass(c, rx.State)
                     and c.__module__.startswith("aifred.")}
    assert composed_from <= checked, sorted(c.__name__ for c in composed_from - checked)
