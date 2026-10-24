"""Upload handlers refuse states without login.

Reflex' POST /_upload runs these handlers for any client token; a made-up token
gives a fresh state without login. The handlers must stop before they read a file,
write to disk or start the STT service.
"""

import asyncio

import pytest

from aifred.state import AIState


class _GuardPassed(Exception):
    """Raised by a stand-in the handler only reaches after the login check."""


class _UntouchableFiles:
    def __iter__(self):
        raise _GuardPassed

    def __len__(self):
        raise _GuardPassed


class _FakeState:
    _process_image_upload = AIState._process_image_upload
    _handle_audio = AIState._handle_audio

    def __init__(self, logged_in_user: str):
        self.logged_in_user = logged_in_user
        self.is_uploading_image = False
        self.is_uploading_document = False
        self.debug: list[str] = []

    def add_debug(self, message: str) -> None:
        self.debug.append(message)


UPLOAD_HANDLERS = [
    "handle_image_upload",
    "handle_camera_upload",
    "handle_document_upload",
    "handle_audio_upload",
    "handle_audio_recording",
]


def _first_step(handler_name: str, state: _FakeState, files: object) -> object:
    handler = AIState.event_handlers[handler_name].fn

    async def run() -> object:
        return await handler(state, files).__anext__()
    return asyncio.run(run())


@pytest.mark.parametrize("handler_name", UPLOAD_HANDLERS)
def test_upload_without_login_stops_before_touching_anything(handler_name):
    state = _FakeState(logged_in_user="")
    with pytest.raises(StopAsyncIteration):
        _first_step(handler_name, state, _UntouchableFiles())
    assert state.is_uploading_image is False
    assert state.is_uploading_document is False
    assert len(state.debug) == 1
    assert "not logged in" in state.debug[0]


def test_image_upload_with_login_proceeds():
    state = _FakeState(logged_in_user="peuqui")
    _first_step("handle_image_upload", state, [])
    assert state.is_uploading_image is True


def test_document_upload_with_login_proceeds():
    state = _FakeState(logged_in_user="peuqui")
    _first_step("handle_document_upload", state, [])
    assert state.is_uploading_document is True


def test_audio_upload_with_login_proceeds(monkeypatch):
    from aifred.lib import stt_client

    def stt_reached():
        raise _GuardPassed

    monkeypatch.setattr(stt_client, "get_default_client", stt_reached)
    state = _FakeState(logged_in_user="peuqui")
    with pytest.raises(_GuardPassed):
        _first_step("handle_audio_upload", state, [])
