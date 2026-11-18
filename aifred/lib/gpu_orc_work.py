"""Tells GPU-Orc while AIfred has work running, so that nothing restarts the machine under it.

Work is a turn: every message that gets an answer runs as one (the chat, every channel, the scheduler, the
API), and the pipeline registry knows them all. A single lease, owner ``GPU_ORC_WORK_OWNER``, stands for them:
it is reported while at least one turn runs and ended when the last one is over. GPU-Orc ends a lease by itself
when the renewals stop, so a crash of AIfred holds nothing back. No model is asked: the count decides.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .config import GPU_ORC_WORK_OWNER, GPU_ORC_WORK_POLL_S, GPU_ORC_WORK_TTL_S
from .gpu_orc_client import GpuOrcClient, GpuOrcError, get_gpu_orc_client
from .logging_utils import log_message
from .pipeline_registry import running_pipeline_count


class WorkReporter:
    def __init__(
        self,
        client: GpuOrcClient,
        running_turns: Callable[[], int],
        *,
        owner: str = GPU_ORC_WORK_OWNER,
        poll_s: float = GPU_ORC_WORK_POLL_S,
        ttl_s: float = GPU_ORC_WORK_TTL_S,
    ) -> None:
        self._client = client
        self._running_turns = running_turns
        self._owner = owner
        self._poll_s = poll_s
        self._ttl_s = ttl_s
        self._reported = False   # GPU-Orc holds a lease of ours
        self._failing = False    # the last report did not get through: log once, not on every poll

    def report_once(self) -> None:
        """Bring GPU-Orc's lease in line with the turns that run now. Blocking (HTTP)."""
        turns = self._running_turns()
        try:
            if turns:
                self._client.work_begin(self._owner, f"AIfred: {turns} running turn(s)", self._ttl_s)
                self._reported = True
            elif self._reported:
                self._client.work_end(self._owner)
                self._reported = False
        except GpuOrcError as error:
            if not self._failing:
                log_message(f"⚠️ GPU-Orc did not take the work report, the weekly reboot may not wait for AIfred: {error}", "warning")
            self._failing = True
            return
        self._failing = False

    async def run(self) -> None:
        """Report until cancelled; ends a lease still held when the server stops."""
        try:
            while True:
                await asyncio.to_thread(self.report_once)
                await asyncio.sleep(self._poll_s)
        except asyncio.CancelledError:
            if self._reported:
                await asyncio.to_thread(self._end_on_shutdown)
            raise

    def _end_on_shutdown(self) -> None:
        try:
            self._client.work_end(self._owner)
        except GpuOrcError as error:
            log_message(f"⚠️ GPU-Orc did not take the end of the work report: {error}", "warning")


def make_work_reporter() -> WorkReporter | None:
    """``None`` when GPU-Orc is off (empty ``GPU_ORC_URL``): there is nobody to report to."""
    client = get_gpu_orc_client()
    return None if client is None else WorkReporter(client, running_pipeline_count)
