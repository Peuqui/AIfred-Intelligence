#!/usr/bin/env python3
"""Patch Reflex problems AIfred depends on being fixed.

1. ``reflex/utils/exec.py`` — a crashed backend worker is never respawned.
``reflex run`` starts granian with backend hot-reload; in that mode
granian (2.6) ignores ``respawn_failed_workers``, so a worker killed by a
C-level crash (e.g. a libc segfault in opencv/ffmpeg) stays dead while
the master lives on and systemd's ``Restart=always`` never fires. Fix:
no hot-reload (code changes need a service restart anyway) and respawn
failed workers; a crash loop ends the master, then systemd restarts the
service.

2. ``reflex_components_*`` ship ``.pyi`` stubs but no ``py.typed`` marker
(PEP 561, still missing in 0.9.10.post1), so mypy ignores the stubs and
every component (``rx.box``, ``rx.cond`` …) becomes ``Any``. The marker
makes the shipped stubs count.

3. ``reflex_components_core/core/cond.py`` defines TypeVars between the
``@overload``s of ``cond``. mypy needs an overload series to be
contiguous, so it keeps the three Component overloads and drops the seven
value overloads — every ``rx.cond(var, "a", "b")`` then fails with
call-overload. Moving the TypeVars above the series changes nothing at
runtime.

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

# (file relative to site-packages, original text, replacement)
PATCHES: list[tuple[str, str, str]] = [
    (
        "reflex/utils/exec.py",
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
    (
        "reflex_components_core/core/cond.py",
        """@overload
def cond(condition: Any, c1: Component, c2: Any, /) -> Component: ...  # pyright: ignore [reportOverlappingOverload]
""",
        """# AIfred patch (scripts/patch-reflex.py): TypeVars above the overload
# series — mypy drops overloads that follow an interrupting statement.
T = TypeVar("T", covariant=True)
U = TypeVar("U", covariant=True)
LITERAL_STRING_S = TypeVar("LITERAL_STRING_S", bound=LiteralString)


@overload
def cond(condition: Any, c1: Component, c2: Any, /) -> Component: ...  # pyright: ignore [reportOverlappingOverload]
""",
    ),
    (
        "reflex_components_core/core/cond.py",
        """def cond(condition: Any, c1: Any, c2: Component, /) -> Component: ...  # pyright: ignore [reportOverlappingOverload]


T = TypeVar("T", covariant=True)
U = TypeVar("U", covariant=True)
LITERAL_STRING_S = TypeVar("LITERAL_STRING_S", bound=LiteralString)


@overload
def cond(
""",
        """def cond(condition: Any, c1: Any, c2: Component, /) -> Component: ...  # pyright: ignore [reportOverlappingOverload]


@overload
def cond(
""",
    ),
]


def find_reflex_dir() -> Path | None:
    """Locate the reflex package inside the current Python environment."""
    spec = importlib.util.find_spec("reflex")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def mark_typed(site_packages: Path, check: bool) -> int:
    """Add the missing py.typed marker to every reflex_components_* package."""
    status = 0
    for package in sorted(site_packages.glob("reflex_components_*/")):
        if package.name.endswith(".dist-info"):
            continue
        marker = package / "py.typed"
        if marker.exists():
            print(f"✅ Already typed: {package.name}")
        elif check:
            print(f"⚠️  Missing py.typed: {package.name}")
            status = 1
        else:
            marker.touch()
            print(f"✅ Marked typed: {package.name}")
    return status


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
        target = reflex_dir.parent / rel_path
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
    status |= mark_typed(reflex_dir.parent, args.check)
    return status if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
