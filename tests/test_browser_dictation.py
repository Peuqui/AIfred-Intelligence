"""Browser dictation (custom.js startBrowserDictation → AIState.handle_browser_dictation).

The browser's recognition delivers ``{"text", "error"}`` once, when the dictation ends; the text
then takes the same way as a dictation through the STT-Suite (``_deliver_transcript``). A
dictation that broke off goes into the input field and is never sent on its own.
"""

import asyncio

from aifred.state import AIState


class _FakeState:
    _deliver_transcript = AIState._deliver_transcript

    def __init__(self, *, logged_in_user: str = "peuqui", show_transcription: bool = False):
        self.logged_in_user = logged_in_user
        self.show_transcription = show_transcription
        self.ui_language = "de"
        self.current_user_input = ""
        self.debug: list[str] = []
        self.sent: list[str] = []

    def add_debug(self, message: str) -> None:
        self.debug.append(message)

    async def send_message(self):
        self.sent.append(self.current_user_input)
        yield


def _dictate(state: _FakeState, result: dict[str, str] | None) -> list[str]:
    """Run the handler; the events it yields, by kind (toast-error, toast-info, script)."""
    handler = AIState.event_handlers["handle_browser_dictation"].fn

    async def run() -> list[object]:
        return [event async for event in handler(state, result)]

    kinds = []
    for event in asyncio.run(run()):
        if event is None:  # from the stand-in send_message
            continue
        args = str(event.args)
        if "__toast" in args:
            kinds.append("toast-error" if '["error"]' in args else "toast-info")
        elif "javascript_code" in args:
            kinds.append("script")
    return kinds


def test_without_login_nothing_happens():
    state = _FakeState(logged_in_user="")
    assert _dictate(state, {"text": "Hallo", "error": ""}) == []
    assert state.sent == [] and state.current_user_input == ""
    assert "not logged in" in state.debug[0]


def test_the_click_that_stops_brings_nothing():
    state = _FakeState()
    assert _dictate(state, None) == []
    assert state.sent == [] and state.debug == []


def test_a_finished_dictation_is_sent_when_editing_is_off():
    state = _FakeState(show_transcription=False)
    assert _dictate(state, {"text": " Wie wird das Wetter morgen? ", "error": ""}) == []
    assert state.sent == ["Wie wird das Wetter morgen?"]


def test_a_finished_dictation_goes_into_the_field_when_editing_is_on():
    state = _FakeState(show_transcription=True)
    assert _dictate(state, {"text": "Wie wird das Wetter morgen?", "error": ""}) == ["script"]
    assert state.sent == []
    assert state.current_user_input == "Wie wird das Wetter morgen?"


def test_a_broken_off_dictation_goes_into_the_field_and_is_not_sent():
    state = _FakeState(show_transcription=False)
    assert _dictate(state, {"text": "Schreib bitte an", "error": "network"}) == ["toast-error", "script"]
    assert state.sent == []
    assert state.current_user_input == "Schreib bitte an"


def test_a_broken_off_dictation_without_text_only_reports():
    state = _FakeState()
    assert _dictate(state, {"text": "", "error": "not-allowed"}) == ["toast-error"]
    assert state.sent == [] and state.current_user_input == ""


def test_nothing_recognised_is_reported_and_not_sent():
    state = _FakeState()
    assert _dictate(state, {"text": "  ", "error": ""}) == ["toast-info"]
    assert state.sent == [] and state.current_user_input == ""


def test_a_mail_address_forces_the_field_as_with_the_stt_suite():
    state = _FakeState(show_transcription=False)
    assert _dictate(state, {"text": "schreib an max at example punkt com", "error": ""}) == ["script"]
    assert state.sent == []
