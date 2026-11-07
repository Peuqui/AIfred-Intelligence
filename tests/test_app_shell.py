"""The Vue app shell under /api/app/: colours and UI texts from their single sources, reachable
before the login; everything else stays behind the login cookie."""

import json
from pathlib import Path

from starlette.testclient import TestClient

from aifred.lib.api.app import api_app
from aifred.theme import COLORS

I18N_DIR = Path(__file__).resolve().parents[1] / "aifred" / "lib" / "i18n"


def test_theme_carries_every_colour_without_login() -> None:
    response = TestClient(api_app).get("/app/theme.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    for name, value in COLORS.items():
        assert f"--aifred-{name.replace('_', '-')}: {value};" in response.text


def test_ui_texts_are_the_json_files_without_login() -> None:
    client = TestClient(api_app)
    for lang in ("de", "en"):
        response = client.get(f"/app/i18n/{lang}")
        assert response.status_code == 200
        assert response.json() == json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def test_unknown_language_is_404() -> None:
    assert TestClient(api_app).get("/app/i18n/xx").status_code == 404


def test_the_shell_exemption_does_not_open_other_routes() -> None:
    client = TestClient(api_app)
    assert client.get("/settings").status_code == 403
    assert client.get("/application").status_code == 403   # only the /app/ folder is exempt
