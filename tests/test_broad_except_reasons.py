"""Every broad ``except Exception`` that ruff lets through by ``# noqa: BLE001`` names its reason.

A broad catch is right only at a real boundary: a loop that must go on, a callback, one rule or
camera among others, an action whose failure the user sees. Everywhere else the handler catches
what can occur there (``sqlite3.Error``, ``OSError``, ``cv2.error`` …), or it goes and the caller's
handler takes over. The reason after the code (``# noqa: BLE001 — <reason>``) says which boundary
it is; a bare ``# noqa: BLE001`` hid 198 catches until 06.11.2026, among them a ``TypeError`` from a
test double that nobody saw. A broad catch that logs with its traceback (``exc_info=True`` or
``logger.exception``) needs no noqa; its reason stands as a comment after the colon.
"""

import io
import re
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKED_DIRS = ("aifred", "scripts", "tests")
NOQA_CODES = re.compile(r"#\s*noqa:\s*(?P<codes>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)(?P<rest>.*)")
REASON = re.compile(r"^\s+—\s+\S")


def _unreasoned(path: Path, root: Path) -> list[str]:
    found: list[str] = []
    source = path.read_text(encoding="utf-8")
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        match = NOQA_CODES.search(token.string)
        if match is None:
            continue
        codes = {code.strip() for code in match.group("codes").split(",")}
        if "BLE001" in codes and not REASON.match(match.group("rest")):
            found.append(f"{path.relative_to(root)}:{token.start[0]}: {token.string.strip()}")
    return found


def test_every_broad_except_names_its_reason() -> None:
    found: list[str] = []
    for directory in CHECKED_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            found.extend(_unreasoned(path, ROOT))
    assert not found, "noqa: BLE001 without '— <reason>':\n" + "\n".join(found)


def test_the_check_sees_a_bare_noqa(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "try:\n    pass\nexcept Exception:  # noqa: BLE001\n    pass\n"
        "try:\n    pass\nexcept Exception:  # noqa: S110, BLE001\n    pass\n"
        "try:\n    pass\nexcept Exception:  # noqa: BLE001 — a loop that must go on\n    pass\n",
        encoding="utf-8",
    )
    found = _unreasoned(sample, tmp_path)
    assert [entry.split(":")[1] for entry in found] == ["3", "7"]
