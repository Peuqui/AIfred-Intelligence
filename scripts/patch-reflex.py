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

4. ``reflex_base/compiler/templates.py`` — the generated ``vite.config.js``
has no ``resolve.dedupe``. Reflex splits shared libraries (react-helmet,
react, …) into several lazy chunks; without dedupe react-helmet ends up
twice and the browser dies with "Identifier 'scrollState' has already been
declared". Since Reflex 0.9 every compile rewrites ``vite.config.js``, so
the ``sed`` of ``scripts/patch-vite-config.sh`` (ExecStartPre, runs BEFORE
the compile) is overwritten at once; the template is the one place that
sticks. (``allowedHosts`` needs no patch: ``vite_allowed_hosts`` in
``rxconfig.py``.)

5. ``reflex_base/vars/base.py`` — ``Field.__get__`` overloads whose self-type is a union with a
TypeVar (``Field[SEQUENCE_TYPE] | Field[SEQUENCE_TYPE | None]``). mypy (2.4) solves the TypeVar
as Never there and takes the first such overload for every field: a ``dict`` field became
``ArrayVar[Never]``, ``AIState.agent_tuning["aifred"]`` an error. One overload per self-type
fixes it; an optional field falls through to ``Var[FIELD_TYPE]``. Type-level only: at runtime
an ``@overload`` is replaced by the implementation below it.

6. ``reflex_base/event/__init__.py`` — ``event = EventNamespace``: the decorator is a class whose
``__new__`` returns an EventCallback. mypy ignores such a ``__new__``, so every ``@rx.event``
method was an ``EventNamespace`` ("not callable" in state code, no checking in the UI). For the
type checker ``event`` becomes the overloaded function the ``__new__`` overloads describe; the
runtime lines stay as they are, under ``else``.

7. Same file, ``EventCallback.__get__`` on an instance returns ``Callable[[Unpack[P]]]`` — no
return type, so mypy took the whole call as ``Any``. ``Callable[[Unpack[P]], Any]`` checks the
arguments of a handler called from state code; its return value stays ``Any``.

8. ``reflex_base/vars/object.py`` — ``ObjectVar.__getitem__`` turns a dataclass value into a bare
``Var``, so ``AIState.agent_tuning["aifred"].model_id`` had no attributes for mypy. An overload
for dataclass values returns ``ObjectVar`` (three edits: TYPE_CHECKING import, TypeVar, overload).

9. ``reflex_base/event/processor/event_processor.py`` — ``enqueue_stream_delta`` (the upload
request) built its event context with ``dataclasses.replace(self._root_context, …)`` and so kept
the root context's txid. Every event enqueued from outside (websocket, API) is a ``fork()`` of
the root context, whose ``parent_txid`` is that txid: while an upload ran, each of them — any
tab, any client — became a child of the upload. The upload request waited for them all and
cancelled them when it ended early. Found 06.11.2026 (0.9.10.post1): a dictation sent straight
to the AI is picked up by the 500 ms poll during the recording's upload, and its turn died when
the tab closed. ``fork()`` gives the upload a txid of its own; it stays a root event, because
the root txid has no tracked future.

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
        "reflex_base/compiler/templates.py",
        """    mainFields: ["browser", "module", "jsnext"],
""",
        """    mainFields: ["browser", "module", "jsnext"],
    // AIfred patch (scripts/patch-reflex.py): one copy of the shared libraries.
    dedupe: ["react-helmet", "react", "react-dom", "@radix-ui/themes", "@emotion/react"],
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
    (
        "reflex_base/vars/base.py",
        """    @overload
    def __get__(
        self: Field[list[V]]
        | Field[set[V]]
        | Field[list[V] | None]
        | Field[set[V] | None],
        instance: None,
        owner: Any,
    ) -> ArrayVar[Sequence[V]]: ...

    @overload
    def __get__(
        self: Field[SEQUENCE_TYPE] | Field[SEQUENCE_TYPE | None],
        instance: None,
        owner: Any,
    ) -> ArrayVar[SEQUENCE_TYPE]: ...

    @overload
    def __get__(
        self: Field[MAPPING_TYPE] | Field[MAPPING_TYPE | None],
        instance: None,
        owner: Any,
    ) -> ObjectVar[MAPPING_TYPE]: ...

    @overload
    def __get__(
        self: Field[SQLA_TYPE] | Field[SQLA_TYPE | None], instance: None, owner: Any
    ) -> ObjectVar[SQLA_TYPE]: ...

    if TYPE_CHECKING:

        @overload
        def __get__(
            self: Field[DATACLASS_TYPE] | Field[DATACLASS_TYPE | None],
            instance: None,
            owner: Any,
        ) -> ObjectVar[DATACLASS_TYPE]: ...

""",
        """    # mypy (2.4) solves a TypeVar in a union self-type as Never and then takes that overload
    # for every field (a dict field became ArrayVar[Never]). One overload per self-type and no
    # union with a TypeVar; an optional field falls through to Var[FIELD_TYPE].
    @overload
    def __get__(self: Field[list[V]], instance: None, owner: Any) -> ArrayVar[Sequence[V]]: ...

    @overload
    def __get__(self: Field[set[V]], instance: None, owner: Any) -> ArrayVar[Sequence[V]]: ...

    @overload
    def __get__(
        self: Field[SEQUENCE_TYPE], instance: None, owner: Any
    ) -> ArrayVar[SEQUENCE_TYPE]: ...

    @overload
    def __get__(
        self: Field[MAPPING_TYPE], instance: None, owner: Any
    ) -> ObjectVar[MAPPING_TYPE]: ...

    @overload
    def __get__(
        self: Field[SQLA_TYPE], instance: None, owner: Any
    ) -> ObjectVar[SQLA_TYPE]: ...

    if TYPE_CHECKING:

        @overload
        def __get__(
            self: Field[DATACLASS_TYPE], instance: None, owner: Any
        ) -> ObjectVar[DATACLASS_TYPE]: ...

""",
    ),
    (
        "reflex_base/event/__init__.py",
        """event = EventNamespace
event.event = event  # pyright: ignore[reportAttributeAccessIssue]
_this = sys.modules[__name__]
event.__path__ = _this.__path__  # pyright: ignore[reportAttributeAccessIssue]
event.__spec__ = _this.__spec__  # pyright: ignore[reportAttributeAccessIssue]
event.__package__ = _this.__package__  # pyright: ignore[reportAttributeAccessIssue]
sys.modules[__name__] = event  # pyright: ignore[reportArgumentType]
""",
        """if TYPE_CHECKING:
    # For a type checker ``event`` is the decorator: mypy ignores a ``__new__`` that returns
    # another type (EventCallback), and every @rx.event method became an EventNamespace.

    @overload
    def event(
        func: None = None,
        *,
        background: bool | None = None,
        supersedes: bool | None = None,
        stop_propagation: bool | None = None,
        prevent_default: bool | None = None,
        throttle: int | None = None,
        debounce: int | None = None,
        temporal: bool | None = None,
    ) -> Callable[[Callable[[BASE_STATE, Unpack[P]], Any]], EventCallback[Unpack[P]]]: ...

    @overload
    def event(
        func: Callable[[BASE_STATE, Unpack[P]], Any],
        *,
        background: bool | None = None,
        supersedes: bool | None = None,
        stop_propagation: bool | None = None,
        prevent_default: bool | None = None,
        throttle: int | None = None,
        debounce: int | None = None,
        temporal: bool | None = None,
    ) -> EventCallback[Unpack[P]]: ...

    def event(func: Any = None, **kwargs: Any) -> Any: ...

else:
    event = EventNamespace
    event.event = event  # pyright: ignore[reportAttributeAccessIssue]
    _this = sys.modules[__name__]
    event.__path__ = _this.__path__  # pyright: ignore[reportAttributeAccessIssue]
    event.__spec__ = _this.__spec__  # pyright: ignore[reportAttributeAccessIssue]
    event.__package__ = _this.__package__  # pyright: ignore[reportAttributeAccessIssue]
    sys.modules[__name__] = event  # pyright: ignore[reportArgumentType]
""",
    ),
    (
        "reflex_base/event/__init__.py",
        """    def __get__(self, instance: Any, owner: Any) -> "Callable[[Unpack[P]]]": ...
""",
        """    def __get__(self, instance: Any, owner: Any) -> "Callable[[Unpack[P]], Any]": ...
""",
    ),
    (
        "reflex_base/vars/object.py",
        """from typing import (
    Any,
    NoReturn,
""",
        """from typing import (
    TYPE_CHECKING,
    Any,
    NoReturn,
""",
    ),
    (
        "reflex_base/vars/object.py",
        """OTHER_KEY_TYPE = TypeVar("OTHER_KEY_TYPE")
""",
        """OTHER_KEY_TYPE = TypeVar("OTHER_KEY_TYPE")

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    DATACLASS_VALUE_TYPE = TypeVar("DATACLASS_VALUE_TYPE", bound=DataclassInstance)
""",
    ),
    (
        "reflex_base/vars/object.py",
        """    @overload
    def __getitem__(
        self: ObjectVar[Mapping[Any, VALUE_TYPE]],
        key: Var | Any,
    ) -> Var[VALUE_TYPE]: ...
""",
        """    if TYPE_CHECKING:
        # A dataclass value is an object as well: its fields stay reachable
        # (``State.tuning["a"].model_id``); the generic overload below gives a bare Var.
        @overload
        def __getitem__(
            self: ObjectVar[Mapping[Any, DATACLASS_VALUE_TYPE]],
            key: Var | Any,
        ) -> ObjectVar[DATACLASS_VALUE_TYPE]: ...

    @overload
    def __getitem__(
        self: ObjectVar[Mapping[Any, VALUE_TYPE]],
        key: Var | Any,
    ) -> Var[VALUE_TYPE]: ...
""",
    ),
    (
        "reflex_base/event/processor/event_processor.py",
        """            ev_ctx=dataclasses.replace(
                self._root_context,
                token=token,
                emit_delta_impl=_emit_delta_impl,
""",
        """            ev_ctx=dataclasses.replace(
                # A txid of its own (patch-reflex.py, 9): replace() kept the root's txid,
                # the parent of every event enqueued from outside, and each of them
                # became a child of this upload.
                self._root_context.fork(token=token),
                emit_delta_impl=_emit_delta_impl,
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
