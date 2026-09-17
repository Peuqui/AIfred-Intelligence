"""Modellstart-Abbruch hinter llama-swap: klare Meldung mit der Ursache aus dem
Upstream-Log statt "upstream command exited prematurely" plus Traceback."""
from __future__ import annotations

import asyncio
import time

from aifred.backends.base import (
    BackendInferenceError,
    BackendModelStartError,
    OpenAICompatibleBackend,
)
from aifred.backends.vllm import vLLMBackend

LLAMASWAP_500 = (
    "Error code: 500 - {'error': 'unspecific error: upstream command exited "
    "prematurely', 'src': 'llama-swap'}"
)
GUARD_LINE = (
    "ValueError: VLLM_QWEN4EXP_PLE_HOST_GIB asks for 3.0 GiB of pinned host memory "
    "per rank, but each of the 2 tensor-parallel ranks may pin at most 2.5 GiB"
)
UPSTREAM_HISTORY = (
    "(APIServer pid=421777) INFO 09-17 16:17:37 [vllm.py:2034] Auto-setting X=1\n"
    "(APIServer pid=421777) Traceback (most recent call last):\n"
    "(APIServer pid=421777)     raise ValueError(\n"
    "(APIServer pid=421777) RuntimeError: an older failure\n"
    f"(APIServer pid=422182) {GUARD_LINE}\n"
    "35.56.185.874 W srv         alloc:  - making room for prompt cache entry\n"
)


def test_last_exception_line_takes_the_newest_exception() -> None:
    assert OpenAICompatibleBackend._last_exception_line(UPSTREAM_HISTORY) == GUARD_LINE
    assert OpenAICompatibleBackend._last_exception_line("INFO only\nno errors here\n") is None


def test_llamaswap_exit_becomes_model_start_error_with_cause(monkeypatch) -> None:
    backend = vLLMBackend(base_url="http://127.0.0.1:9/v1")

    async def cause() -> str:
        return GUARD_LINE

    monkeypatch.setattr(backend, "_upstream_exit_cause", cause)
    error = asyncio.run(backend._backend_error(Exception(LLAMASWAP_500), "Flash-Next"))
    assert isinstance(error, BackendModelStartError)
    assert GUARD_LINE in str(error) and "Flash-Next" in str(error)


def test_missing_cause_points_to_the_journal(monkeypatch) -> None:
    backend = vLLMBackend(base_url="http://127.0.0.1:9/v1")

    async def no_cause() -> None:
        return None

    monkeypatch.setattr(backend, "_upstream_exit_cause", no_cause)
    error = asyncio.run(backend._backend_error(Exception(LLAMASWAP_500), "m"))
    assert isinstance(error, BackendModelStartError)
    assert "journalctl -u llama-swap" in str(error)


def test_other_errors_keep_their_classification() -> None:
    backend = vLLMBackend(base_url="http://127.0.0.1:9/v1")
    error = asyncio.run(backend._backend_error(Exception("connection reset"), "m"))
    assert isinstance(error, BackendInferenceError)
    assert not isinstance(error, BackendModelStartError)


def test_upstream_log_stream_that_stays_open_is_read_within_the_bound() -> None:
    """Like llama-swap: history first, then the connection stays open."""

    async def run() -> tuple[str | None, float]:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            body = UPSTREAM_HISTORY.encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                + f"{len(body):x}\r\n".encode() + body + b"\r\n"
            )
            await writer.drain()
            await asyncio.sleep(30)  # never finishes the stream

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        backend = vLLMBackend(base_url=f"http://127.0.0.1:{port}/v1")
        start = time.monotonic()
        cause = await backend._upstream_exit_cause()
        elapsed = time.monotonic() - start
        server.close()
        return cause, elapsed

    cause, elapsed = asyncio.run(run())
    assert cause == GUARD_LINE
    assert elapsed < OpenAICompatibleBackend._UPSTREAM_LOG_COLLECT_S + 1.0
