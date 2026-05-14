#!/usr/bin/env python3
"""Patch a Reflex bug that breaks routing when ``frontend_path`` is set.

Reflex (≥ 0.8.24) calls ``path.removeprefix(config.frontend_path)`` in
``route.get_route()``. The browser-side path always starts with ``/``
(e.g. ``/aifred/``), while ``frontend_path`` does not — so the prefix
never matches and ``on_load`` never fires. AIfred hangs on
"wird initialisiert...".

The fix prepends a slash:

    path = path.removeprefix("/" + config.frontend_path)

This script is idempotent — running it twice is a no-op. It only patches
when the buggy line is found verbatim, so a future upstream fix won't be
overwritten.

Usage:
    venv/bin/python scripts/patch-reflex.py
    venv/bin/python scripts/patch-reflex.py --check    # exit 0 if patched/not-needed, 1 if missing
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

BUGGY = 'path = path.removeprefix(config.frontend_path)'
FIXED = 'path = path.removeprefix("/" + config.frontend_path)'


def find_route_py() -> Path | None:
    """Locate reflex/route.py inside the current Python environment."""
    spec = importlib.util.find_spec("reflex")
    if spec is None or spec.origin is None:
        return None
    reflex_dir = Path(spec.origin).parent
    candidate = reflex_dir / "route.py"
    return candidate if candidate.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't modify, just report status (exit 0=ok, 1=needs patch, 2=reflex not found)",
    )
    args = parser.parse_args()

    route_py = find_route_py()
    if route_py is None:
        print("❌ reflex/route.py not found — is reflex installed in this Python env?", file=sys.stderr)
        return 2

    text = route_py.read_text(encoding="utf-8")

    if FIXED in text:
        print(f"✅ Already patched: {route_py}")
        return 0

    if BUGGY not in text:
        # Either fixed upstream with a different line, or the file changed shape.
        # Don't try to be clever — let the user investigate.
        print(f"ℹ️  Buggy line not found in {route_py}.")
        print("   Either Reflex fixed it upstream (great, no action needed)")
        print("   or the file shape changed and this patcher is stale.")
        print("   Check CLAUDE.md → 'Reflex Patch: frontend_path Route-Matching Bug' for details.")
        return 0 if not args.check else 1

    if args.check:
        print(f"⚠️  Reflex needs patching: {route_py}")
        return 1

    patched = text.replace(BUGGY, FIXED, 1)
    route_py.write_text(patched, encoding="utf-8")
    print(f"✅ Patched {route_py}")
    print(f"   '{BUGGY}'")
    print("   →")
    print(f"   '{FIXED}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
