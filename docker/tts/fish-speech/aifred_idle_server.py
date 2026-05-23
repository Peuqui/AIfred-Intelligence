"""AIfred idle-watchdog wrapper for the Fish-Speech S2 Pro API server.

XTTS / MOSS / Qwen3-TTS each ship their own ``server.py`` that carries an
idle watchdog: a background thread terminates the container after
``*_KEEP_ALIVE`` minutes of inactivity so the GPU VRAM is freed. Fish has
no own server — it runs the upstream ``tools/api_server.py`` (a Kui ASGI
app) — so there is no request handler of ours to hook into.

This wrapper closes that gap with identical behaviour: it wraps the
upstream ASGI app in a framework-agnostic middleware that

  * resets an idle timer on every real work request,
  * answers ``/keep_alive`` itself (timer reset without producing audio),

and runs the same polling watchdog the other engines use. The only
difference from XTTS is *where* the timer reset sits — ASGI middleware
instead of the request handler — because Fish's handlers are upstream
code we do not patch.

KEEP_ALIVE minutes come from ``FISH_SPEECH_KEEP_ALIVE`` (default 30),
exactly like ``XTTS_KEEP_ALIVE`` / ``MOSS_KEEP_ALIVE`` / ``QWEN3_KEEP_ALIVE``.
``FISH_SPEECH_KEEP_ALIVE=0`` disables auto-shutdown.
"""
import json
import multiprocessing
import os
import re
import signal
import threading
import time

import uvicorn
from loguru import logger

KEEP_ALIVE_MINUTES = int(os.environ.get("FISH_SPEECH_KEEP_ALIVE", "30"))

# Health-checks, UI and docs must NOT count as activity — AIfred's
# is_running() probe hits /v1/health, and would otherwise keep the
# container alive forever. /keep_alive is handled explicitly below.
_IGNORED_PREFIXES = (
    "/v1/health", "/keep_alive", "/ui", "/openapi", "/docs", "/redoc",
    "/favicon",
)

# Shared between the ASGI middleware and the watchdog thread. Both run in
# the same process (uvicorn workers=1), so a plain module global is safe.
_last_request_time = time.time()


def _shutdown_container() -> None:
    """Terminate the container so the GPU can drop its ~20 GB.

    The wrapper is PID 1 in the container (started via ``exec``), so
    SIGTERM to ourselves takes uvicorn — and the container — down.
    Docker ``restart: "no"`` keeps it down; AIfred re-creates it on
    demand. Escalate to SIGKILL if a wedged CUDA context stalls the
    graceful shutdown.
    """
    logger.info(
        f"⏰ Fish-Speech auto-shutdown after {KEEP_ALIVE_MINUTES} min inactivity "
        f"— terminating container"
    )
    time.sleep(0.5)  # let the log line flush
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError as exc:
        logger.error(f"SIGTERM to self failed: {exc}")
    time.sleep(15)
    logger.warning("Graceful shutdown stalled — escalating to SIGKILL")
    os.kill(os.getpid(), signal.SIGKILL)


def _idle_watchdog_loop() -> None:
    """Polling watchdog — exits the container after KEEP_ALIVE idle.

    Plain while-loop (same approach as xtts/server.py): observable in the
    logs, robust against thread-lifecycle quirks.
    """
    poll_interval = max(30, min(120, KEEP_ALIVE_MINUTES * 60 // 10))
    logger.info(
        f"⏰ Idle watchdog started: {KEEP_ALIVE_MINUTES} min window, "
        f"poll every {poll_interval}s"
    )
    while True:
        time.sleep(poll_interval)
        idle = time.time() - _last_request_time
        if idle < KEEP_ALIVE_MINUTES * 60:
            continue
        _shutdown_container()
        return  # _shutdown_container does not return; for clarity


class IdleTrackerMiddleware:
    """ASGI middleware — resets the idle timer on real work requests and
    answers /keep_alive. Pure ASGI, no Kui internals."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path == "/keep_alive":
                global _last_request_time
                _last_request_time = time.time()
                body = json.dumps({
                    "status": "ok",
                    "keep_alive_minutes": KEEP_ALIVE_MINUTES,
                }).encode()
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
            if path and not any(
                path.startswith(p) for p in _IGNORED_PREFIXES
            ):
                _last_request_time = time.time()
        await self.app(scope, receive, send)


def create_app():
    """Build the upstream Kui app and wrap it in the idle tracker."""
    from tools.api_server import create_app as _upstream_create_app
    return IdleTrackerMiddleware(_upstream_create_app())


if __name__ == "__main__":
    import pyrootutils
    pyrootutils.setup_root(__file__, indicator=".project-root", pythonpath=True)

    # spawn — same as upstream api_server.py; the ModelManager forks
    # worker processes and inherits a clean interpreter state.
    multiprocessing.set_start_method("spawn", force=True)

    from tools.server.api_utils import parse_args

    args = parse_args()
    # Upstream create_app() reads its args back from this env var.
    os.environ["FISH_API_SERVER_ARGS"] = json.dumps(vars(args))

    # IPv6 is [xxxx::xxxx]:port, IPv4 is host:port.
    match = re.search(r"\[([^\]]+)\]:(\d+)$", args.listen)
    if match:
        host, port = match.groups()
    else:
        host, port = args.listen.split(":")

    app = create_app()

    if KEEP_ALIVE_MINUTES > 0:
        threading.Thread(
            target=_idle_watchdog_loop, name="idle-watchdog", daemon=True,
        ).start()
    else:
        logger.info("⏰ Fish-Speech auto-shutdown disabled (FISH_SPEECH_KEEP_ALIVE=0)")

    # Pass the app instance (not an import string) → single process, one
    # module load, so middleware and watchdog share the same global.
    uvicorn.run(app, host=host, port=int(port), log_level="info")
