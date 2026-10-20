"""Card choice for the TTS reserve of a calibrated variant: unused by the LLM first, else the cheapest LLM card."""
from __future__ import annotations

import pytest

from aifred.lib.calibration import pick_tts_reserve_gpu
from aifred.lib.calibration.types import GPU


def _gpu(uuid: str, free_mb: int) -> GPU:
    return GPU(uuid=uuid, name=uuid, compute_cap=7.0, total_mb=free_mb, free_mb=free_mb, speed_class=0, first_in_class=False)


GPUS = [_gpu("A", 48000), _gpu("B", 48000), _gpu("C", 32000), _gpu("D", 32000), _gpu("E", 32000)]


def test_a_card_the_llm_does_not_use_takes_the_reserve() -> None:
    assert pick_tts_reserve_gpu(GPUS, [1, 1, 0, 0, 0], 8000) == "C"  # most room, then the lower position


def test_the_card_with_the_most_room_among_the_unused_ones_wins() -> None:
    assert pick_tts_reserve_gpu(GPUS, [0, 1, 1, 1, 1], 8000) == "A"


def test_an_unused_card_that_is_too_small_is_skipped_for_a_card_the_llm_uses() -> None:
    assert pick_tts_reserve_gpu(GPUS, [1, 1, 1, 1, 0], 30000) == "E"
    assert pick_tts_reserve_gpu(GPUS, [1, 1, 1, 1, 0], 40000) == "A"  # only A and B hold 40000 MiB; equal shares, lower position


def test_when_the_llm_uses_every_card_the_one_with_the_smallest_share_per_mib_is_reduced() -> None:
    # shares per MiB: A 20/48000, B 8/48000, C 10/32000, D 10/32000, E 12/32000
    assert pick_tts_reserve_gpu(GPUS, [20, 8, 10, 10, 12], 8000) == "B"


def test_the_vlm_reserve_counts_against_the_capacity_of_its_card() -> None:
    gpus = [_gpu("A", 20000), _gpu("B", 12000)]
    assert pick_tts_reserve_gpu(gpus, [1, 0], 9000) == "B"
    assert pick_tts_reserve_gpu(gpus, [1, 0], 9000, vlm_gpu_uuid="B", vlm_reserve_mb=4000) == "A"  # B cannot hold both


def test_no_card_holds_the_reserve() -> None:
    assert pick_tts_reserve_gpu(GPUS, [1, 1, 1, 1, 1], 60000) is None


def test_a_split_that_does_not_match_the_gpus_is_an_error() -> None:
    with pytest.raises(ValueError):
        pick_tts_reserve_gpu(GPUS, [1, 1], 8000)
