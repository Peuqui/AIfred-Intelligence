"""The gate for the models of a turn (lib/model_gate.py): turns with the same models run side by side, a turn with
other models waits for the running ones, in the order they came; a cancelled one leaves the line. Real asyncio and
real threads, no stand-ins."""
import asyncio
import threading

import pytest

from aifred.lib import turn_debate, turn_service
from aifred.lib.agent_tuning import default_agent_tuning
from aifred.lib.model_gate import ModelGate, ModelKey
from aifred.lib.runtime_settings import RuntimeSettings

SMALL: ModelKey = frozenset({"small"})
BIG: ModelKey = frozenset({"big"})
OTHER: ModelKey = frozenset({"other"})


async def _turn(gate: ModelGate, key: ModelKey, name: str, log: list[str], release: asyncio.Event) -> None:
    async with gate.hold(key, label=name):
        log.append(f"start {name}")
        await release.wait()
        log.append(f"end {name}")


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_turns_with_the_same_models_run_side_by_side() -> None:
    async def scenario() -> list[str]:
        gate, log, release = ModelGate(), list[str](), asyncio.Event()
        tasks = [asyncio.create_task(_turn(gate, BIG, name, log, release)) for name in ("a", "b")]
        await _settle()
        started = list(log)
        release.set()
        await asyncio.gather(*tasks)
        return started

    assert asyncio.run(scenario()) == ["start a", "start b"]


def test_a_turn_with_other_models_waits_until_the_running_ones_are_done() -> None:
    async def scenario() -> tuple[list[str], list[tuple[str, ...]]]:
        gate, log, release_a, release_b = ModelGate(), list[str](), asyncio.Event(), asyncio.Event()
        waits: list[tuple[str, ...]] = []

        async def waiting() -> None:
            async with gate.hold(OTHER, label="b", on_wait=waits.append):
                log.append("start b")
                await release_b.wait()

        first = asyncio.create_task(_turn(gate, BIG, "a", log, release_a))
        await _settle()
        second = asyncio.create_task(waiting())
        await _settle()
        assert log == ["start a"]
        release_a.set()
        await _settle()
        assert log == ["start a", "end a", "start b"]
        release_b.set()
        await asyncio.gather(first, second)
        return log, waits

    assert asyncio.run(scenario())[1] == [("a",)]


def test_nothing_overtakes_a_waiting_turn() -> None:
    # A third turn with the running turn's models would starve the waiting one if it could pass.
    async def scenario() -> list[str]:
        gate, log, release = ModelGate(), list[str](), asyncio.Event()
        tasks = [asyncio.create_task(_turn(gate, key, name, log, release)) for key, name in ((BIG, "a"), (OTHER, "b"), (BIG, "c"))]
        await _settle()
        started = list(log)
        release.set()
        await asyncio.gather(*tasks)
        return started

    assert asyncio.run(scenario()) == ["start a"]


def test_a_cancelled_waiting_turn_leaves_the_line() -> None:
    async def scenario() -> list[str]:
        gate, log, release = ModelGate(), list[str](), asyncio.Event()
        first = asyncio.create_task(_turn(gate, BIG, "a", log, release))
        await _settle()
        second = asyncio.create_task(_turn(gate, OTHER, "b", log, release))
        third = asyncio.create_task(_turn(gate, BIG, "c", log, release))
        await _settle()
        second.cancel()
        await _settle()
        started = list(log)   # c does not wait for b any more: it runs with a
        release.set()
        await asyncio.gather(first, third)
        with pytest.raises(asyncio.CancelledError):
            await second
        return started

    assert asyncio.run(scenario()) == ["start a", "start c"]


def test_a_failing_turn_frees_its_place() -> None:
    async def scenario() -> None:
        gate = ModelGate()
        with pytest.raises(RuntimeError):
            async with gate.hold(BIG, label="a"):
                raise RuntimeError("turn failed")
        async with gate.hold(OTHER, label="b", on_wait=lambda _blockers: pytest.fail("nobody runs")):
            pass

    asyncio.run(scenario())


def test_turns_on_two_threads_with_their_own_loops_wait_for_each_other() -> None:
    gate = ModelGate()
    log: list[str] = []
    first_in, first_may_end = threading.Event(), threading.Event()

    def first() -> None:
        async def work() -> None:
            async with gate.hold(BIG, label="a"):
                log.append("start a")
                first_in.set()
                await asyncio.to_thread(first_may_end.wait)
                log.append("end a")
        asyncio.run(work())

    def second() -> None:
        async def work() -> None:
            async with gate.hold(OTHER, label="b", on_wait=lambda _blockers: first_may_end.set()):
                log.append("start b")
        asyncio.run(work())

    thread_a = threading.Thread(target=first)
    thread_a.start()
    assert first_in.wait(5)
    thread_b = threading.Thread(target=second)
    thread_b.start()
    thread_a.join(10)
    thread_b.join(10)
    assert log == ["start a", "end a", "start b"]


def _view() -> RuntimeSettings:
    view = RuntimeSettings(
        backend_type="llamacpp", backend_url="http://x", ui_language="de",
        enable_tts=False, automatik_model_id="helper", agent_tuning=default_agent_tuning(),
        tts_autoplay=False, tts_agents={}, max_debate_rounds=3, consensus_type="majority",
    )
    view.agent_tuning["aifred"].model_id = "big"
    return view


def test_a_turn_calls_the_models_of_its_agents_and_the_automatik() -> None:
    assert turn_service.turn_models(_view(), turn_debate.TurnPlan("aifred")) == frozenset({"helper", "big"})
