"""Which files in the agents' documents a tool call changed.

The tools that write there name their file in the call (write_file, patch_file), but
execute_code_write runs code that may write any file. So a writing call is bracketed by a
look at the folder before and after it (path and modification time, fractions of a millisecond
for a few hundred files): what differs is what the call changed, whoever wrote it. The ToolKit
of an agent keeps the list for its turn (function_calling.ToolKit.changed_documents), the code
review hands it to the reviewer (turn_debate._recensio).
"""

from __future__ import annotations

from .config import DOCUMENTS_DIR

NEW = "new"
CHANGED = "changed"
DELETED = "deleted"


def snapshot() -> dict[str, int]:
    """Every file in the documents: its path (relative, POSIX) → its modification time (ns)."""
    if not DOCUMENTS_DIR.is_dir():
        return {}
    return {
        path.relative_to(DOCUMENTS_DIR).as_posix(): path.stat().st_mtime_ns
        for path in DOCUMENTS_DIR.rglob("*") if path.is_file()
    }


def changes(before: dict[str, int], after: dict[str, int]) -> dict[str, str]:
    """What differs between two snapshots: path → NEW, CHANGED or DELETED."""
    found = {path: NEW if path not in before else CHANGED for path, mtime in after.items() if before.get(path) != mtime}
    found.update({path: DELETED for path in before if path not in after})
    return found


def merge(known: dict[str, str], found: dict[str, str]) -> None:
    """Add a call's changes to the turn's: a file new in this turn stays new when changed again and
    leaves the list when deleted again (it was never there before the turn)."""
    for path, kind in found.items():
        if known.get(path) == NEW:
            if kind == DELETED:
                del known[path]
            continue
        known[path] = kind
