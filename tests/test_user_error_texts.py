"""Every refusal the code raises has its text in German and English, with the values the text names.

A ``UserError`` (``aifred/lib/user_error.py``) carries an i18n key; the API words it as ``error_<key>`` in the UI
language. A key without a text would reach the user as the bare key (``t()`` answers with the key), a placeholder the
raise does not fill breaks the wording with a ``KeyError`` inside the error handler, a value no text names is lost. The
check reads the source: every call of a ``UserError`` kind, including the kinds defined elsewhere (``NameTaken``,
``PTZError`` …), with a key written out as a string. A name stands for what its module imports or defines under it:
the TTS client's own ``Unauthorized`` and ``BadRequest`` are errors of the TTS service, no refusals.
"""

import ast
import json
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "aifred" / "lib" / "i18n"
BASE = ("aifred.lib.user_error", "UserError")
# Names of t()'s own: a value called so would choose the language or the plural form instead of filling the text.
RESERVED = {"lang", "count"}

Ref = tuple[str, str]   # (module, name in it)


def _trees() -> dict[str, tuple[ast.Module, bool]]:
    """Every module of the package by its dotted name, with whether it is a package (``__init__.py``)."""
    trees: dict[str, tuple[ast.Module, bool]] = {}
    for path in sorted((ROOT / "aifred").rglob("*.py")):
        parts = path.relative_to(ROOT).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        trees[".".join(parts[:-1] if is_package else parts)] = (ast.parse(path.read_text(encoding="utf-8")), is_package)
    return trees


def _source_of(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, *([node.module] if node.module else [])])


def _namespaces(trees: dict[str, tuple[ast.Module, bool]]) -> dict[str, dict[str, Ref]]:
    """Per module, what a name stands for: a name it imports (anywhere in the file: the code imports inside functions
    too) or a class of its own (which wins over an import of the same name)."""
    spaces: dict[str, dict[str, Ref]] = {}
    for module, (tree, is_package) in trees.items():
        names: dict[str, Ref] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                source = _source_of(module, is_package, node)
                names.update({alias.asname or alias.name: (source, alias.name) for alias in node.names})
        names.update({node.name: (module, node.name) for node in ast.walk(tree) if isinstance(node, ast.ClassDef)})
        spaces[module] = names
    return spaces


def _resolve(expression: ast.expr, names: dict[str, Ref]) -> Ref | None:
    """``Kind`` through the module's names, ``module.Kind`` through the module imported under that name."""
    if isinstance(expression, ast.Name):
        return names.get(expression.id)
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        imported = names.get(expression.value.id)
        if imported is not None:
            return (f"{imported[0]}.{imported[1]}", expression.attr)
    return None


def _error_kinds(trees: dict[str, tuple[ast.Module, bool]], spaces: dict[str, dict[str, Ref]]) -> set[Ref]:
    """``UserError`` and every class that derives from it, wherever it is defined."""
    classes = [(module, node) for module, (tree, _) in trees.items() for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef)]
    kinds = {BASE}
    grew = True
    while grew:
        grew = False
        for module, node in classes:
            if (module, node.name) not in kinds and any(_resolve(base, spaces[module]) in kinds for base in node.bases):
                kinds.add((module, node.name))
                grew = True
    return kinds


def _raises(trees: dict[str, tuple[ast.Module, bool]]) -> list[tuple[str, ast.Call]]:
    spaces = _namespaces(trees)
    kinds = _error_kinds(trees, spaces) - {BASE}
    return [(f"{module}:{node.lineno}", node) for module, (tree, _) in trees.items() for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _resolve(node.func, spaces[module]) in kinds]


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field}


def _problems(raises: list[tuple[str, ast.Call]], texts: dict[str, dict[str, str]]) -> list[str]:
    problems: list[str] = []
    for where, call in raises:
        if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            problems.append(f"{where}: key not written out as a string")
            continue
        key = f"error_{call.args[0].value}"
        given = {keyword.arg for keyword in call.keywords if keyword.arg}
        if given & RESERVED:
            problems.append(f"{where}: values named {sorted(given & RESERVED)} belong to t() itself")
            continue
        for lang, table in texts.items():
            if key not in table:
                problems.append(f"{where}: {key} missing in {lang}.json")
            elif _placeholders(table[key]) != given:
                problems.append(f"{where}: {key} ({lang}) names {sorted(_placeholders(table[key]))}, "
                                f"the raise gives {sorted(given)}")
    return problems


def _texts() -> dict[str, dict[str, str]]:
    return {lang: json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("de", "en")}


def test_every_refusal_has_its_text_in_both_languages() -> None:
    raises = _raises(_trees())
    assert len(raises) > 100, f"only {len(raises)} refusals found: the check reads the wrong place"
    problems = _problems(raises, _texts())
    assert not problems, "\n".join(problems)


def _http_exceptions(trees: dict[str, tuple[ast.Module, bool]]) -> list[str]:
    """Where the code imports or raises FastAPI's ``HTTPException``: its ``detail`` reads in no language of the UI."""
    return [f"{module}:{node.lineno}" for module, (tree, _) in trees.items() for node in ast.walk(tree)
            if (isinstance(node, ast.ImportFrom) and any(alias.name == "HTTPException" for alias in node.names))
            or (isinstance(node, ast.Name) and node.id == "HTTPException")
            or (isinstance(node, ast.Attribute) and node.attr == "HTTPException")]


def test_every_refusal_of_the_api_is_a_user_error() -> None:
    found = _http_exceptions(_trees())
    assert not found, "HTTPException instead of a UserError (aifred/lib/user_error.py):\n" + "\n".join(found)


def test_the_api_check_sees_an_http_exception() -> None:
    sample = "from fastapi import HTTPException\nraise HTTPException(status_code=404, detail='gone')\nimport fastapi\nfastapi.HTTPException(400)\n"
    assert _http_exceptions({"aifred.lib.api.sample": (ast.parse(sample), False)}) == [
        "aifred.lib.api.sample:1", "aifred.lib.api.sample:2", "aifred.lib.api.sample:4"]


def test_the_check_sees_a_missing_text_and_a_wrong_value() -> None:
    sample = (
        "from aifred.lib.user_error import Conflict, Invalid\n"
        "from aifred.lib import user_error\n"
        "class Own(Conflict): pass\n"
        "class Refused(Exception): pass\n"            # a class of its own under a kind's name: no refusal
        "raise Invalid('known', value=1)\n"
        "raise Own('unknown')\n"
        "raise Own('known', other=2)\n"
        "raise Invalid(name)\n"
        "raise ValueError('not a refusal')\n"
        "raise Invalid('known', lang='de')\n"
        "raise Refused('anything')\n"
        "raise user_error.Missing('unknown')\n"
    )
    library = (ROOT / "aifred" / "lib" / "user_error.py").read_text(encoding="utf-8")
    trees = {"aifred.lib.user_error": (ast.parse(library), False), "aifred.lib.sample": (ast.parse(sample), False)}
    texts = {"de": {"error_known": "{value}"}, "en": {"error_known": "{value}"}}
    problems = _problems([entry for entry in _raises(trees) if entry[0].startswith("aifred.lib.sample:")], texts)
    assert [problem.split(":")[1] for problem in problems] == ["6", "6", "7", "7", "8", "10", "12", "12"]
