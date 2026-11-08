"""What a turn does while no text comes reaches its pages as a phase (``turn_phase``, debug_bus.report_phase): the
Automatik call, the compression (also one whose summary fails), the pages of a web search; each ends when its work
does. The image description is checked with the turn (test_turn_service.py). Every phase the server reports has its
entry in the browser (frontend/src/lib/turnProgress.ts); a phase without one would break the chat's line there.
The model calls are stand-ins, the session file and the event hub run for real.
"""
import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from fake_web_search import FakeWebSearch

from aifred.lib import context_manager, debug_bus, intent_detector, session_events, session_storage, turn_service
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.runtime_settings import RuntimeSettings

ROOT = Path(__file__).resolve().parents[1]
SESSION = "f" * 32
REPORT = re.compile(r"""\breport_phase\(\s*["']([a-z_]*)["']""")
ENTRY = re.compile(r"^  ([a-z_]+): \{ icon:", re.MULTILINE)


def _reported() -> dict[str, set[str]]:
    """Each phase name the server code reports, with the files that report it (the empty name ends a phase)."""
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "aifred").rglob("*.py")):
        for name in REPORT.findall(path.read_text(encoding="utf-8")):
            if name:
                found.setdefault(name, set()).add(path.name)
    return found


def test_the_browser_knows_every_phase_the_server_reports() -> None:
    reported = _reported()
    assert {"intent", "images", "compress", "scraping"} <= set(reported)   # the pattern still finds the calls
    known = set(ENTRY.findall((ROOT / "frontend" / "src" / "lib" / "turnProgress.ts").read_text(encoding="utf-8")))
    assert {name: sorted(files) for name, files in reported.items() if name not in known} == {}


def test_the_patterns_see_calls_and_entries() -> None:
    assert REPORT.findall('report_phase("scraping", current=1)\nreport_phase("")') == ["scraping", ""]
    assert ENTRY.findall('const PHASES = {\n  intent: { icon: "🤖", text: () => t("x") },\n};') == ["intent"]


# ── The phases as the pages see them ─────────────────────────────────────


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A session on disk (debug lines go to its file) with an empty event stream."""
    monkeypatch.setattr(session_storage, "SESSION_DIR", tmp_path)
    session_storage._session_meta_cache.clear()
    session_storage.save_session(SESSION, {"data": {"chat_history": [], "llm_history": []}}, owner="ki")
    session_events._channels.pop(SESSION, None)
    yield
    session_storage._session_meta_cache.clear()
    session_events._channels.pop(SESSION, None)


def _phase() -> str | None:
    """The phase a page that connects now is told (the stream keeps only the newest); None: none was reported."""
    phases = [event.data["phase"] for event in session_events.replay_after(SESSION, 0) if event.kind == "turn_phase"]
    assert len(phases) <= 1
    return phases[0] if phases else None


def _view() -> RuntimeSettings:
    view = RuntimeSettings(
        backend_type="ollama", backend_url="http://x", ui_language="de",
        enable_tts=False, automatik_model_id="", agent_tuning=default_agent_tuning(),
        tts_autoplay=False, tts_agents={}, max_debate_rounds=3, consensus_type="majority",
    )
    view.agent_tuning["aifred"].model_id = "qwen3:8b"
    return view


def _in_session(work: Any) -> Any:
    with debug_bus.session_scope(SESSION):
        return asyncio.run(work)


def test_the_automatik_call_is_a_phase_that_ends_with_it(session: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    async def detect(*_args: Any, **_kwargs: Any) -> tuple[None, str, dict[str, Any], bool, str, float]:
        seen.append(_phase())
        return None, "de", {}, False, "", 0.0

    monkeypatch.setattr(intent_detector, "detect_query_intent_and_addressee", detect)
    _in_session(turn_service._detect_intent(_view(), None, "Wie wird das Wetter?"))
    assert seen == ["intent"]
    assert _phase() == ""


def test_a_message_without_an_automatik_call_reports_no_phase(session: None) -> None:
    _in_session(turn_service._detect_intent(_view(), None, "https://example.org/seite"))
    assert _phase() is None


def _context() -> turn_service.TurnContext:
    return turn_service.TurnContext(
        SESSION, _view(), None, "Frage", "Frage", turn_service._Intent(None, "de", {}, False, 0.0), "browser",
        "standard", True, "", None, [],
    )


def _summaries(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]], seen: list[str | None]) -> None:
    """The compression yields ``events``; ``seen`` gets the phase the pages are told after each one."""
    async def summarize(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        for event in events:
            yield event
            seen.append(_phase())

    monkeypatch.setattr(context_manager, "summarize_history_if_needed", summarize)
    monkeypatch.setattr(context_manager, "largest_compression_model", lambda _models: "qwen3:8b")


def test_a_compression_is_a_phase_until_the_history_is_replaced(session: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    summary = [{"role": "system", "content": "[Summary] Früher ging es ums Wetter."}]
    _summaries(monkeypatch, [
        {"type": "progress", "phase": "compress"},
        {"type": "history_update", "chat_history": summary, "llm_history": summary},
    ], seen)
    _in_session(turn_service.compress_history(_context(), ["aifred"], 32768, 1000, 0, "Test"))
    assert seen == ["compress", "compress"]
    assert _phase() == ""
    stored = session_storage.load_session(SESSION)
    assert stored is not None and stored["data"]["chat_history"] == summary


def test_a_compression_whose_summary_fails_still_ends_its_phase(session: None, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    _summaries(monkeypatch, [{"type": "progress", "phase": "compress"}, {"type": "debug", "message": "❌ Summary error"}], seen)
    _in_session(turn_service.compress_history(_context(), ["aifred"], 32768, 1000, 0, "Test"))
    assert seen == ["compress", "compress"]
    assert _phase() == ""


def test_a_history_that_fits_reports_no_phase(session: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _summaries(monkeypatch, [{"type": "debug", "message": "📊 History: 100 / 32.768 tok (0%)"}], [])
    _in_session(turn_service.compress_history(_context(), ["aifred"], 32768, 1000, 0, "Test"))
    assert _phase() is None


def test_the_pages_of_a_web_search_are_counted_and_the_phase_ends(session: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import research_tools

    seen: list[dict[str, Any]] = []
    web = FakeWebSearch(
        urls=["https://a.test", "https://b.test", "https://c.test"],
        pages=[{"success": True, "content": "Sonne", "url": "https://a.test", "word_count": 1}],
        progress=[(0, 0), (1, 0), (1, 1)],
        after_progress=lambda: seen.append(
            next(event.data for event in session_events.replay_after(SESSION, 0) if event.kind == "turn_phase")),
    )
    web.install(monkeypatch)
    _context, sources = _in_session(research_tools.run_web_search(["Wetter morgen"], []))
    assert sources == 1
    assert [(p["phase"], p["current"], p["total"], p["failed"]) for p in seen] == [
        ("scraping", 0, 3, 0), ("scraping", 1, 3, 0), ("scraping", 1, 3, 1),
    ]
    assert _phase() == ""
