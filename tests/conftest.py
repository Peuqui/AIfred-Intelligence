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
def _isolate_audit_db(tmp_path):
    """Tests dürfen nicht in die echte data/security/audit.db schreiben.

    ToolKit.execute_streaming() schreibt in seinem finally-Block IMMER eine
    Audit-Zeile — jeder Test, der ein Tool ausführt (test_tool_loop_breaker),
    legte so Dummy-Zeilen ({'content': 'A'}, {'a': 1, 'b': 2}) in die
    Produktiv-DB und verdrängte die echten Einträge aus der "Letzte 50"-
    Ansicht. Für die Testdauer auf tmp_path umbiegen."""
    import aifred.lib.security as sec
    saved_path = sec._audit_db_path
    saved_init = sec._audit_db_initialized
    sec._audit_db_path = tmp_path / "audit.db"
    sec._audit_db_initialized = False
    yield
    sec._audit_db_path = saved_path
    sec._audit_db_initialized = saved_init


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
