"""Scheduler-Auswahllisten: Beschriftungen kommen aus i18n, gespeichert wird der Wert."""
from __future__ import annotations

import pytest

from aifred.lib.i18n import TranslationManager
from aifred.state._agent_editor_mixin import AgentEditorMixin as M

OPTION_LISTS = [
    M._SCHED_TYPE_OPTIONS,
    M._SCHED_DELIVERY_OPTIONS,
    M._DOW_OPTIONS,
    M._MONTH_OPTIONS,
    M._INTERVAL_UNIT_OPTIONS,
    [(p[0], p[0]) for p in M._CRON_PRESETS],
]
LANGUAGES = ("de", "en")


@pytest.mark.parametrize("options", OPTION_LISTS)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_option_is_translated(options: list[tuple[str, str]], lang: str) -> None:
    for key, _value in options:
        assert key in TranslationManager._translations[lang], f"{lang}: {key} fehlt"


@pytest.mark.parametrize("options", OPTION_LISTS)
@pytest.mark.parametrize("lang", LANGUAGES)
def test_label_round_trips_to_its_value(options: list[tuple[str, str]], lang: str) -> None:
    for _key, value in options:
        label = M._sched_label_for_value(options, value, lang)
        assert M._sched_value_for_label(options, label, "UNBEKANNT") == value


def test_known_labels_and_defaults() -> None:
    assert M._sched_label_for_value(M._SCHED_TYPE_OPTIONS, "cron", "de") == "Zeitplan"
    assert M._sched_label_for_value(M._SCHED_TYPE_OPTIONS, "cron", "en") == "Cron"
    assert M._sched_value_for_label(M._DOW_OPTIONS, "Mo–Fr", "*") == "1-5"
    assert M._sched_value_for_label(M._DOW_OPTIONS, "Mon–Fri", "*") == "1-5"
    assert M._sched_value_for_label(M._MONTH_OPTIONS, "März", "*") == "3"
    # Unbekannte Werte/Labels: Wert bleibt sichtbar, Label faellt auf den Standard
    assert M._sched_label_for_value(M._SCHED_DELIVERY_OPTIONS, "mail", "de") == "mail"
    assert M._sched_value_for_label(M._INTERVAL_UNIT_OPTIONS, "Wochen", "minutes") == "minutes"
