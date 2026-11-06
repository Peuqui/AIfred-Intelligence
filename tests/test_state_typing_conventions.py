"""The two declarations mypy needs to check the UI against the state.

- A public state field is an ``rx.Field[...]``: only then is ``AIState.field`` a typed Var for mypy
  (``.length()``, ``.contains()``, ``["key"]``); a plain annotation is the field's value type.
- A public state method carries ``@rx.event`` (or is a computed ``@rx.var``): only then are its
  calls from the UI typed. Every public method is an event handler at runtime anyway; one that
  only state code calls is private (``_name``).

Without these the UI check loses its ground silently: mypy stays green and checks less.
"""

import ast
from pathlib import Path

import pytest

STATE_DIR = Path(__file__).resolve().parent.parent / "aifred" / "state"


def _state_classes(tree: ast.Module) -> list[ast.ClassDef]:
    def is_state(cls: ast.ClassDef) -> bool:
        bases = {ast.unparse(base) for base in cls.bases}
        return bool(bases & {"rx.State", "AIState"}) or any(kw.arg == "mixin" for kw in cls.keywords)

    return [node for node in tree.body if isinstance(node, ast.ClassDef) and is_state(node)]


def _members(cls: ast.ClassDef) -> list[ast.stmt]:
    """Class body plus the declarations under ``if TYPE_CHECKING:``."""
    members: list[ast.stmt] = []
    for node in cls.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            members.extend(node.body)
        else:
            members.append(node)
    return members


def _findings(check) -> list[str]:
    found: list[str] = []
    for path in sorted(STATE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in _state_classes(tree):
            for node in _members(cls):
                problem = check(node)
                if problem:
                    found.append(f"{path.name}:{node.lineno} {cls.name}.{problem}")
    return found


def _plain_field(node: ast.stmt) -> str | None:
    if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
        return None
    name, annotation = node.target.id, ast.unparse(node.annotation)
    if name.startswith("_") or "ClassVar" in annotation or annotation.startswith("rx.Field["):
        return None
    return f"{name}: {annotation} is not an rx.Field"


def _undecorated_method(node: ast.stmt) -> str | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
        return None
    decorators = [ast.unparse(d) for d in node.decorator_list]
    if any(d == "rx.event" or d.startswith(("rx.event(", "rx.var")) for d in decorators):
        return None
    return f"{node.name}() has neither @rx.event nor @rx.var"


def test_the_state_classes_are_found() -> None:
    names = {cls.name for path in STATE_DIR.glob("*.py") for cls in _state_classes(ast.parse(path.read_text()))}
    assert {"AIState", "ChatMixin", "TTSStreamingMixin"} <= names


@pytest.mark.parametrize("check", [_plain_field, _undecorated_method], ids=["fields", "methods"])
def test_every_public_member_is_typed_for_the_ui(check) -> None:
    assert _findings(check) == []
