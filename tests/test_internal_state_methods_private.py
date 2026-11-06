"""Methods only state code calls are private, so no client can trigger them as events.

A public method of a Reflex state is an event handler any connected client can call with its own
arguments. picker_open_for was one: it sets the picker's sandbox root, its capabilities (create
folders, symlinks), the symlink check and the callback that _dispatch_callback looks up by name.
"""

import pytest

from aifred.state import AIState


@pytest.mark.parametrize("name", ["picker_open_for", "stop_media", "refresh_vlm_loaded"])
def test_internal_method_is_no_event_handler(name: str) -> None:
    assert name not in AIState.event_handlers
    assert f"_{name}" not in AIState.event_handlers
    assert callable(getattr(AIState, f"_{name}"))
