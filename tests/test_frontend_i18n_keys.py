"""Every text key the Vue frontend names literally exists in both language files, and a key it counts with
(tCount) also has its plural: a missing one shows up on the page as the bare key (frontend/src/i18n.ts).

Keys built at run time (a template string, a key from the server) are not seen here.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("de", "en")
CALL = re.compile(r"""\b(t|tCount)\(\s*["'`]([A-Za-z0-9_]+)["'`]""")


def _keys_used() -> dict[str, set[str]]:
    """Each key the frontend needs, with the files that name it: for tCount the key and its plural."""
    needed: dict[str, set[str]] = {}
    for path in sorted((ROOT / "frontend" / "src").rglob("*")):
        if path.suffix not in (".vue", ".ts") or path.name.endswith(".d.ts"):
            continue
        for function, key in CALL.findall(path.read_text(encoding="utf-8")):
            for wanted in (key, f"{key}_plural") if function == "tCount" else (key,):
                needed.setdefault(wanted, set()).add(path.name)
    return needed


def test_the_frontend_finds_each_of_its_texts_in_both_languages() -> None:
    needed = _keys_used()
    assert len(needed) > 100  # the pattern still finds the calls
    for language in LANGUAGES:
        texts = json.loads((ROOT / "aifred" / "lib" / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
        missing = {key: sorted(files) for key, files in needed.items() if key not in texts}
        assert missing == {}, f"{language}.json lacks {missing}"


def test_the_pattern_sees_both_kinds_of_call() -> None:
    sample = 't("doc_index") + tCount("doc_chunk_count", 3) + t(\'jobs_runs\')'
    assert CALL.findall(sample) == [("t", "doc_index"), ("tCount", "doc_chunk_count"), ("t", "jobs_runs")]
