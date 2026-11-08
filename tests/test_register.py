"""Registering (POST /api/auth/register): a name on the whitelist gets an account and is logged in at once. Until
08.11.2026 only the Reflex login dialog could. A name that is not allowed and one that has an account already get
the same answer: the form does not tell which names exist."""

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from aifred.lib import session_storage
from aifred.lib.api.app import api_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(session_storage, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(session_storage, "WHITELIST_FILE", tmp_path / "allowed_users.json")
    (tmp_path / "allowed_users.json").write_text(json.dumps(["Anna"]))
    return TestClient(api_app)


def test_a_name_on_the_whitelist_gets_an_account_and_is_logged_in(client: TestClient) -> None:
    answer = client.post("/auth/register", json={"username": " Anna ", "password": "geheim"})
    assert answer.status_code == 201 and answer.json() == {"user": "anna"}
    assert "HttpOnly" in answer.headers["set-cookie"]
    assert client.get("/auth/me").json() == {"user": "anna"}
    assert session_storage.verify_account("anna", "geheim")


def test_a_name_not_allowed_and_a_taken_one_get_the_same_answer(client: TestClient) -> None:
    client.post("/auth/register", json={"username": "anna", "password": "geheim"})
    fresh = TestClient(api_app)
    taken = fresh.post("/auth/register", json={"username": "anna", "password": "anders"})
    unknown = fresh.post("/auth/register", json={"username": "bert", "password": "x"})
    assert taken.status_code == unknown.status_code == 403
    assert taken.json() == unknown.json()
    assert session_storage.verify_account("anna", "geheim")                 # the first password stays
    assert not session_storage.account_exists("bert")
