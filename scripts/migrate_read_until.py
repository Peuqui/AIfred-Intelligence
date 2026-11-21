#!/usr/bin/env python3
"""Once, at the switch to the read mark per session (``read_until``, lib/session_storage.py ``mark_read_until``): every
session file without the field gets the timestamp of its newest bubble (an empty history: ""), so that the replies
that exist today do not all count as unread. A file that has the field is left alone. Run it on the sessions
directory with the service stopped, after a backup.

    venv/bin/python scripts/migrate_read_until.py <sessions directory>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Before any aifred import: a helper script is not the service and must not reset its debug log.
os.environ.setdefault("AIFRED_CLI_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aifred.lib.atomic_write import write_text_atomic


def migrate_directory(directory: Path) -> int:
    """Set ``read_until`` in every session file that lacks it; prints one line per file, returns how many changed."""
    changed = 0
    for path in sorted(directory.glob("*.json")):
        session = json.loads(path.read_text(encoding="utf-8"))
        if "read_until" in session:
            print(f"{path.name}: unchanged (has read_until)")
            continue
        history = session.get("data", {}).get("chat_history", [])
        session["read_until"] = max((entry["timestamp"] for entry in history), default="")
        write_text_atomic(path, json.dumps(session, ensure_ascii=False, indent=2))
        print(f"{path.name}: read_until = {session['read_until']!r}")
        changed += 1
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path, help="the sessions directory (data/sessions)")
    print(f"{migrate_directory(parser.parse_args().directory)} file(s) changed")
