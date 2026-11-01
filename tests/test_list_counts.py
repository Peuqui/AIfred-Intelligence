"""Eine Liste sagt, wie viel es insgesamt gibt, und keine feste Obergrenze kappt sie.

Kontrakt:
- Ohne Angabe liefert ein Werkzeug LIST_DEFAULT Einträge, ``limit`` holt beliebig viele.
- ``total_count`` nennt alle Treffer, nicht nur die gezeigten.
- Zusammenfassungen richten sich nach dem Kontext des Modells.
"""

import asyncio
import json

import pytest

from aifred.lib.config import HISTORY_SUMMARY_MAX_RATIO, LIST_DEFAULT
from aifred.lib.context_manager import calculate_max_summaries
from aifred.lib.plugin_modules import ModuleAccess
from aifred.plugins.tools.nextcloud.settings import NextcloudSettings
from aifred.plugins.tools.nextcloud.store import NextcloudStore
from aifred.plugins.tools.nextcloud.tools import get_nextcloud_tools

WRITE = ModuleAccess({"calendar": "write", "tasks": "write", "contacts": "write", "notes": "write", "files": "off"})


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch):
    store = NextcloudStore(NextcloudSettings("https://nextcloud.invalid/nextcloud", "aifred", WRITE), "unused")
    rows = [{"id": f"book/{number}.vcf", "name": f"Kontakt {number}"} for number in range(1000)]
    monkeypatch.setattr(store, "contacts", lambda _query: rows)
    tool = next(t for t in get_nextcloud_tools(store) if t.name == "nextcloud_search")

    def run(**arguments: object) -> dict:
        answer: dict = json.loads(asyncio.run(tool.executor(entity_type="contacts", **arguments)))
        return answer

    yield run
    store.close()


def test_a_default_page_and_the_real_total(search) -> None:
    answer = search()
    assert answer["total_count"] == 1000 and len(answer["results"]) == LIST_DEFAULT


def test_limit_has_no_ceiling(search) -> None:
    assert len(search(limit=800)["results"]) == 800


@pytest.mark.parametrize("context", [32_768, 350_000, 1_040_000])
def test_summaries_follow_the_model(context: int) -> None:
    assert calculate_max_summaries(context) == int(context * HISTORY_SUMMARY_MAX_RATIO) // 500


def test_a_tiny_context_keeps_one_summary() -> None:
    assert calculate_max_summaries(1000) == 1
