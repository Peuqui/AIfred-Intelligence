#!/usr/bin/env python3
"""Patch a Reflex problem AIfred depends on being fixed.

``reflex/utils/exec.py`` — a crashed backend worker is never respawned.
``reflex run`` starts granian with backend hot-reload; in that mode
granian (2.6) ignores ``respawn_failed_workers``, so a worker killed by a
C-level crash (e.g. a libc segfault in opencv/ffmpeg) stays dead while
the master lives on and systemd's ``Restart=always`` never fires. Fix:
no hot-reload (code changes need a service restart anyway) and respawn
failed workers; a crash loop ends the master, then systemd restarts the
service.

Idempotent — running it twice is a no-op. A patch is only applied when its
original text is found verbatim, so a changed upstream file is reported,
never overwritten blindly.

Usage:
    venv/bin/python scripts/patch-reflex.py
    venv/bin/python scripts/patch-reflex.py --check    # exit 0 if patched/not-needed, 1 if missing
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# (file relative to the reflex package, original text, replacement)
PATCHES: list[tuple[str, str, str]] = [
    (
        "utils/exec.py",
        """        reload=True,
        reload_paths=get_reload_paths(),
        reload_ignore_worker_failure=True,
        reload_ignore_patterns=HOTRELOAD_IGNORE_PATTERNS,
        reload_tick=100,
""",
        """        # AIfred patch (scripts/patch-reflex.py): no backend hot-reload, so
        # granian respawns a crashed worker instead of leaving it dead.
        reload=False,
        respawn_failed_workers=True,
        respawn_interval=3.5,
""",
    ),
]


def find_reflex_dir() -> Path | None:
    """Locate the reflex package inside the current Python environment."""
    spec = importlib.util.find_spec("reflex")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't modify, just report status (exit 0=ok, 1=needs patch, 2=reflex not found)",
    )
    args = parser.parse_args()

    reflex_dir = find_reflex_dir()
    if reflex_dir is None:
        print("❌ reflex not found — is it installed in this Python env?", file=sys.stderr)
        return 2

    status = 0
    for rel_path, original, replacement in PATCHES:
        target = reflex_dir / rel_path
        text = target.read_text(encoding="utf-8")
        if replacement in text:
            print(f"✅ Already patched: {target}")
            continue
        if original not in text:
            # Fixed upstream with a different shape, or this patcher is stale —
            # don't guess, let the user investigate (CLAUDE.md, Reflex patches).
            print(f"ℹ️  Original text not found in {target} — patch not applied.")
            status = 1
            continue
        if args.check:
            print(f"⚠️  Needs patching: {target}")
            status = 1
            continue
        target.write_text(text.replace(original, replacement, 1), encoding="utf-8")
        print(f"✅ Patched {target}")
    return status if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
