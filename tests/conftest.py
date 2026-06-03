# Standalone scripts that pytest should not collect
import pytest

collect_ignore = ["test_dashscope_tts.py", "test_dashscope_voice_clone.py"]


@pytest.fixture(autouse=True, scope="session")
def _no_debug_log_file():
    """Tests dürfen nicht in die echte data/logs/aifred_debug.log schreiben.

    log_message() schreibt sonst Test-Output (room1, test-room, wohnzimmer,
    …) in die Produktions-Log-Datei. Für die Testdauer das Datei-Logging
    abschalten — Verhalten wird über Asserts geprüft, nicht über Log-Inhalt."""
    import aifred.lib.logging_utils as lu
    saved = lu.FILE_DEBUG_ENABLED
    lu.FILE_DEBUG_ENABLED = False
    yield
    lu.FILE_DEBUG_ENABLED = saved


@pytest.fixture(autouse=True)
def _reset_freeecho2_alert_state():
    """Modulglobalen Alert-Queue-State vor/nach jedem Test leeren.

    _alert_queues/_alert_workers/_playback_done halten asyncio.Queue- und
    Event-Objekte, die an den Loop gebunden sind, in dem sie zuerst benutzt
    wurden. Ohne Reset lebt ein Event aus Test A weiter und wird in Test Bs
    eigenem asyncio.run-Loop wiederverwendet → 'bound to a different event
    loop'. In Produktion ist das kein Problem (alles läuft im einen
    ws-Loop), aber zwischen isolierten Tests muss der State frisch sein."""
    try:
        import aifred.plugins.channels.freeecho2_channel as fe
    except ImportError:
        yield
        return
    fe._alert_queues.clear()
    fe._alert_workers.clear()
    fe._playback_done.clear()
    yield
    fe._alert_queues.clear()
    fe._alert_workers.clear()
    fe._playback_done.clear()
