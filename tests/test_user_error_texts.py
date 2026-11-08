"""Every refusal the code raises has its text in German and English, with the values the text names.

A ``UserError`` (``aifred/lib/user_error.py``) carries an i18n key; the API words it as ``error_<key>`` in the UI
language. A key without a text would reach the user as the bare key (``t()`` answers with the key), a placeholder the
raise does not fill breaks the wording with a ``KeyError`` inside the error handler, a value no text names is lost. The
check reads the source: every call of a ``UserError`` kind, including the kinds defined elsewhere (``NameTaken``,
``PTZError`` …), with a key written out as a string.
"""

import ast
import json
import string
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "aifred" / "lib" / "i18n"
BASE = "UserError"
# Names of t()'s own: a value called so would choose the language or the plural form instead of filling the text.
RESERVED = {"lang", "count"}


def _trees() -> dict[Path, ast.Module]:
    return {path: ast.parse(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "aifred").rglob("*.py"))}


def _error_kinds(trees: dict[Path, ast.Module]) -> set[str]:
    """``UserError`` and every class that derives from it, by name across the package."""
    bases: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases[node.name] = {base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                                    for base in node.bases}
    kinds = {BASE}
    grew = True
    while grew:
        grew = False
        for name, parents in bases.items():
            if name not in kinds and parents & kinds:
                kinds.add(name)
                grew = True
    return kinds


def _raises(trees: dict[Path, ast.Module], kinds: set[str]) -> list[tuple[str, ast.Call]]:
    found: list[tuple[str, ast.Call]] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in kinds and name != BASE:
                found.append((f"{path.relative_to(ROOT)}:{node.lineno}", node))
    return found


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
    trees = _trees()
    raises = _raises(trees, _error_kinds(trees))
    assert raises, "no UserError raised anywhere: the check reads the wrong place"
    problems = _problems(raises, _texts())
    assert not problems, "\n".join(problems)


def test_the_check_sees_a_missing_text_and_a_wrong_value() -> None:
    source = (
        "class Invalid(UserError): pass\n"
        "class Conflict(UserError): pass\n"
        "class Own(Conflict): pass\n"
        "raise Invalid('known', value=1)\n"
        "raise Own('unknown')\n"
        "raise Own('known', other=2)\n"
        "raise Invalid(name)\n"
        "raise ValueError('not a refusal')\n"
        "raise Invalid('known', lang='de')\n"
    )
    trees = {ROOT / "sample.py": ast.parse(source)}
    texts = {"de": {"error_known": "{value}"}, "en": {"error_known": "{value}"}}
    problems = _problems(_raises(trees, _error_kinds(trees)), texts)
    assert [problem.split(":")[1] for problem in problems] == ["5", "5", "6", "6", "7", "9"]
