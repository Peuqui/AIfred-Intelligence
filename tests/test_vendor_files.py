"""The local JS libraries are served with CORS: a module script (and a page in the chat's sandbox frame) asks for them
from an origin that is not the app's."""

from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

from aifred.lib.vendor_files import VendorFiles


def test_a_library_may_be_read_from_any_origin(tmp_path: Path) -> None:
    (tmp_path / "three").mkdir()
    (tmp_path / "three" / "three.module.min.js").write_text("export const REVISION = '160';")
    app = Starlette()
    app.mount("/vendor", VendorFiles(directory=str(tmp_path)))
    response = TestClient(app).get("/vendor/three/three.module.min.js")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["content-type"].startswith("text/javascript")
