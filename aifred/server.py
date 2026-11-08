"""AIfred as a server: the API (with the Vue app under /api/app/) and the folders under /_upload/ from one process, with
every task of the process (``lib/server_startup.SERVER_LIFESPANS``). The systemd unit starts it:

    venv/bin/python -m aifred.server

One process; when it dies, systemd starts it again (``Restart=always``).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable, Sequence

from fastapi import FastAPI
from starlette.responses import RedirectResponse

Lifespan = Callable[[], contextlib.AbstractAsyncContextManager[None]]


def create_app(lifespans: Sequence[Lifespan]) -> FastAPI:
    """The app with ``lifespans`` entered in order at the start and left in reverse at the end."""
    from .lib.api import api_app
    from .lib.upload_files import mount_upload_folders

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            for part in lifespans:
                await stack.enter_async_context(part())
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/api", api_app)
    mount_upload_folders(app)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # Relative, so it holds behind any prefix (nginx /aifred/ → the server's /).
        return RedirectResponse("api/app/")

    return app


def main() -> None:
    import uvicorn

    from .lib.config import AIFRED_SERVER_HOST, AIFRED_SERVER_PORT
    from .lib.server_startup import SERVER_LIFESPANS

    uvicorn.run(create_app(SERVER_LIFESPANS), host=AIFRED_SERVER_HOST, port=AIFRED_SERVER_PORT, log_level="warning")


if __name__ == "__main__":
    main()
