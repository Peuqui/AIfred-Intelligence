"""InferenceWork — one definition of prefill and decode work for every backend,
summed over a whole turn."""

import pytest

from aifred.lib.perf_metrics import InferenceWork, ThinkingClock


def test_sum_is_time_weighted_not_an_average_of_rates() -> None:
    # A cold 30,000-token prefill and a 12-token follow-up: the mean of the
    # two rates (1,000 and 12) would say ~506 tok/s, the work says ~964.
    cold = InferenceWork(prefill_tokens=30000, prefill_s=30.0, decode_tokens=100, decode_s=2.0)
    follow_up = InferenceWork(prefill_tokens=12, prefill_s=1.0, decode_tokens=300, decode_s=8.0)
    total = cold + follow_up
    assert total.prefill_rate() == pytest.approx(30012 / 31.0)
    assert total.decode_rate() == pytest.approx(400 / 10.0)


def test_one_unmeasured_request_makes_the_sum_unknown() -> None:
    total = InferenceWork(prefill_tokens=500, prefill_s=1.0, decode_tokens=50, decode_s=1.0) + InferenceWork.unmeasured()
    assert total.prefill_rate() is None
    assert total.decode_rate() == 0.0


def test_round_trip_through_dict() -> None:
    work = InferenceWork(prefill_tokens=7, prefill_s=None, decode_tokens=3, decode_s=0.5)
    assert InferenceWork.from_dict(work.to_dict()) == work


def test_wall_clock_decode_excludes_time_to_first_token() -> None:
    # 400 tokens, 5 s until the first one, 25 s total -> 20 s of decode.
    work = InferenceWork.from_wall_clock(
        prompt_tokens=1000, cached_tokens=None, tokens_generated=400, first_token_s=5.0, elapsed_s=25.0,
    )
    assert work.decode_rate() == 20.0
    assert work.decode_rate() > 400 / 25.0  # the old whole-request division


def test_wall_clock_prefill_counts_only_computed_tokens() -> None:
    work = InferenceWork.from_wall_clock(
        prompt_tokens=10000, cached_tokens=9000, tokens_generated=10, first_token_s=2.0, elapsed_s=3.0,
    )
    assert work.prefill_tokens == 1000
    assert work.prefill_rate() == 500.0


def test_wall_clock_unknown_cache_hits_make_prefill_unmeasurable() -> None:
    work = InferenceWork.from_wall_clock(
        prompt_tokens=10000, cached_tokens=None, tokens_generated=10, first_token_s=2.0, elapsed_s=3.0,
    )
    assert work.prefill_rate() is None


def test_wall_clock_nothing_generated() -> None:
    work = InferenceWork.from_wall_clock(
        prompt_tokens=10, cached_tokens=0, tokens_generated=0, first_token_s=None, elapsed_s=1.0,
    )
    assert work.decode_rate() == 0.0
    assert work.decode_s == 0.0


def test_thinking_adds_up_across_blocks() -> None:
    work = InferenceWork(thinking_s=3.0) + InferenceWork(thinking_s=4.5)
    assert work.thinking_s == 7.5


def test_thinking_clock_counts_every_block() -> None:
    clock = ThinkingClock()
    clock.observe("<think>", 1.0)
    clock.observe("plan", 2.0)
    clock.observe("</think>\n\nIch schaue nach.", 4.0)   # 3 s
    clock.observe("<think>Ergebnis prüfen", 10.0)
    clock.observe("</think>\n\nFertig.", 12.5)             # 2.5 s
    assert clock.total_s == 5.5


def test_thinking_clock_finds_tags_split_across_chunks() -> None:
    clock = ThinkingClock()
    clock.observe("<thi", 1.0)
    clock.observe("nk>denke</th", 2.0)
    clock.observe("ink>Antwort", 5.0)
    assert clock.total_s == 3.0
