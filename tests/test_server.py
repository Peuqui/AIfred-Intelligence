"""The server (aifred/server.py): one app with the API under /api, the folders under /_upload/ and the root that
leads to the Vue app, its lifespans entered in order and left in reverse."""

import contextlib
from collections.abc import AsyncIterator

from starlette.testclient import TestClient

from aifred.server import Lifespan, create_app


def test_the_lifespans_start_in_order_and_end_in_reverse() -> None:
    seen: list[str] = []

    def part(name: str) -> Lifespan:
        @contextlib.asynccontextmanager
        async def lifespan() -> AsyncIterator[None]:
            seen.append(f"start {name}")
            yield
            seen.append(f"end {name}")
        return lifespan

    with TestClient(create_app([part("events"), part("hub")])):
        assert seen == ["start events", "start hub"]
    assert seen == ["start events", "start hub", "end hub", "end events"]


def test_the_root_leads_relatively_to_the_vue_app() -> None:
    # Relative: behind nginx under /aifred/ the browser lands on /aifred/api/app/.
    with TestClient(create_app([])) as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "api/app/"



def test_the_sandbox_libraries_are_served_where_generated_html_names_them() -> None:
    # Generated HTML loads e.g. /vendor/three.min.js (VENDOR_URL in the tool texts); until 08.11.2026 only the
    # Reflex frontend served the folder, under /aifred/vendor/, so an artifact in the user's browser found nothing.
    from aifred.lib.config import VENDOR_DIR, VENDOR_URL

    library = VENDOR_DIR / "three.min.js"
    with TestClient(create_app([])) as client:
        response = client.get(f"{VENDOR_URL}/{library.name}")
        outside = client.get(f"{VENDOR_URL}/../aifred/server.py")
    assert response.status_code == 200 and response.content == library.read_bytes()
    assert "javascript" in response.headers["content-type"]
    assert outside.status_code == 404
