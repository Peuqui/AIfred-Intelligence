"""Scheduled jobs over the API (Vue jobs page, lib/api/jobs.py): the options, a job created and changed with the
fields of its delivery only, the rules of lib/scheduler.job_problem and an expression that never fires refused,
switched on and off, its runs, deleted, the next run of an expression before saving. The job store, agents.json
and the channels are temporary stand-ins."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from aifred.lib import agent_config, scheduler
from aifred.lib.i18n import t

CRON = {"name": "Morgengruß", "schedule_type": "cron", "schedule_expr": "0 8 * * *", "message": "Guten Morgen", "agent": "aifred"}


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> scheduler.JobStore:
    job_store = scheduler.JobStore(tmp_path / "jobs.db")
    monkeypatch.setattr(scheduler, "_job_store", job_store)
    monkeypatch.setattr("aifred.lib.file_manager.DOCUMENTS_DIR", tmp_path / "documents")  # a job's folder lies here, not in the real workspace
    agents_file = tmp_path / "agents.json"
    agents = agent_config._default_agents()
    agents["sokrates"]["enabled"] = False
    agents_file.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr(agent_config, "AGENTS_FILE", agents_file)
    channels = {"telegram": SimpleNamespace(name="telegram"), "freeecho2": SimpleNamespace(name="freeecho2")}
    monkeypatch.setattr("aifred.lib.plugin_registry.all_channels", lambda: channels)
    monkeypatch.setattr("aifred.lib.plugin_base.plugin_display_name", lambda channel, lang: channel.name.title())
    return job_store


@pytest.fixture
def client(store: scheduler.JobStore) -> Iterator[TestClient]:
    from aifred.lib.api.app import api_app
    from aifred.lib.auth import sign_username
    from aifred.lib.auth import USERNAME_COOKIE_NAME

    with TestClient(api_app) as test_client:
        test_client.cookies.set(USERNAME_COOKIE_NAME, sign_username("ki"))
        yield test_client


def test_the_options_offer_what_a_job_can_be(client: TestClient) -> None:
    options = client.get("/jobs/options").json()
    assert options["schedule_types"] == ["cron", "interval", "once"] and options["tiers"] == [0, 1, 2, 3, 4]
    assert [preset["key"] for preset in options["cron_presets"]][:2] == ["sched_preset_hourly", "sched_preset_daily"]
    assert "sokrates" not in [agent["id"] for agent in options["agents"]]           # switched off
    assert [channel["id"] for channel in options["channels"]] == ["freeecho2", "telegram"] and options["rooms_channels"] == ["freeecho2"]


def test_a_job_keeps_the_fields_of_its_delivery_only(client: TestClient, store: scheduler.JobStore) -> None:
    review = client.post("/jobs", json={**CRON, "channel": "telegram", "recipient": "123"})
    assert review.status_code == 201 and review.json()["next_run"]
    assert store.get(review.json()["job_id"]).payload == {"message": "Guten Morgen", "agent": "aifred", "delivery": "review"}  # type: ignore[union-attr]
    room = client.post("/jobs", json={**CRON, "delivery": "announce", "channel": "freeecho2", "recipient": "*", "fixed_text": True, "audio_type": "alarm"}).json()
    assert store.get(room["job_id"]).payload == {  # type: ignore[union-attr]
        "message": "Guten Morgen", "agent": "aifred", "delivery": "announce", "channel": "freeecho2", "recipient": "*",
        "fixed_text": True, "audio_type": "alarm",
    }
    sent = client.post("/jobs", json={**CRON, "delivery": "announce", "channel": "telegram", "recipient": "123", "audio_type": "alarm"}).json()
    assert "audio_type" not in store.get(sent["job_id"]).payload  # type: ignore[union-attr]  # no rooms on Telegram


@pytest.mark.parametrize(("change", "key", "values"), [
    ({"schedule_expr": "jeden Morgen"}, "schedule_never_fires", {"kind": "cron", "expression": "jeden Morgen"}),
    ({"fixed_text": True}, "fixed_text_needs_announce", {}),
    ({"delivery": "brieftaube"}, "unknown_delivery", {"value": "brieftaube", "allowed": ", ".join(scheduler.DELIVERY_MODES)}),
    ({"agent": "sokrates"}, "job_agent_unfit", {"agent": "sokrates"}),
    ({"schedule_type": "once", "schedule_expr": "morgen früh"}, "schedule_never_fires",            # no point in time
     {"kind": "once", "expression": "morgen früh"}),
])
def test_a_job_the_scheduler_refuses_is_not_stored(
    client: TestClient, store: scheduler.JobStore, change: dict[str, Any], key: str, values: dict[str, str],
) -> None:
    refused = client.post("/jobs", json={**CRON, **change})
    assert (refused.status_code, refused.json()["detail"]) == (422, t(f"error_{key}").format(**values))
    assert store.list_all() == []


def test_a_job_is_changed_switched_and_deleted(client: TestClient, store: scheduler.JobStore) -> None:
    job_id = client.post("/jobs", json=CRON).json()["job_id"]
    assert client.patch(f"/jobs/{job_id}", json={"enabled": False}).json()["enabled"] is False
    changed = client.put(f"/jobs/{job_id}", json={**CRON, "schedule_type": "interval", "schedule_expr": "3600", "message": "Stündlich"}).json()
    assert (changed["job_id"], changed["message"], changed["schedule_type"], changed["enabled"]) == (job_id, "Stündlich", "interval", False)
    assert client.put(f"/jobs/{job_id}", json={**CRON, "schedule_expr": "falsch"}).status_code == 422
    store.add_run(job_id, "s1", "Erster Lauf")
    assert client.get(f"/jobs/{job_id}/runs").json()[0]["text"] == "Erster Lauf"
    assert client.delete(f"/jobs/{job_id}").status_code == 204 and client.get("/jobs").json() == []
    assert client.delete(f"/jobs/{job_id}").status_code == 404


def test_the_end_of_a_schedule_is_stored_listed_changed_and_refused_when_wrong(client: TestClient, store: scheduler.JobStore) -> None:
    created = client.post("/jobs", json={**CRON, "schedule_expr": "0 8,14,20 * * *", "ends_at": "2099-11-30T18:00"}).json()
    assert created["ends_at"] == "2099-11-30T18:00" and client.get("/jobs").json()[0]["ends_at"] == "2099-11-30T18:00"
    assert client.post("/jobs", json=CRON).json()["ends_at"] == ""                              # optional
    changed = client.put(f"/jobs/{created['job_id']}", json={**CRON, "ends_at": ""}).json()
    assert changed["ends_at"] == ""
    for bad, key, values in (
        ("30.11.2026", "invalid_ends_at", {"value": "30.11.2026"}),
        ("2020-01-01", "ends_before_first_run", None),
    ):
        refused = client.post("/jobs", json={**CRON, "ends_at": bad})
        assert refused.status_code == 422
        if values is not None:
            assert refused.json()["detail"] == t(f"error_{key}").format(**values)
    once = client.post("/jobs", json={**CRON, "schedule_type": "once", "schedule_expr": "2099-01-01T10:00:00", "ends_at": "2099-02-01"})
    assert once.status_code == 422 and once.json()["detail"] == t("error_ends_at_not_for_once")


def test_an_ended_job_is_not_switched_on_over_the_api(client: TestClient, store: scheduler.JobStore) -> None:
    job_id = client.post("/jobs", json={**CRON, "schedule_type": "interval", "schedule_expr": "3600", "ends_at": "2099-01-01"}).json()["job_id"]
    with store._connect() as conn:       # as a run to its end left it: the end behind us, the job off
        conn.execute("UPDATE jobs SET ends_at = '2020-01-01', enabled = 0 WHERE job_id = ?", (job_id,))
        conn.commit()
    refused = client.patch(f"/jobs/{job_id}", json={"enabled": True})
    assert refused.status_code == 422 and refused.json()["detail"] == t("error_job_ended").format(ends_at="2020-01-01")


def test_the_preview_says_when_an_expression_fires(client: TestClient) -> None:
    assert client.post("/jobs/preview", json={"schedule_type": "cron", "schedule_expr": "30 7 * * 1-5"}).json()["next_run"]
    assert client.post("/jobs/preview", json={"schedule_type": "cron", "schedule_expr": "kein cron"}).status_code == 422
