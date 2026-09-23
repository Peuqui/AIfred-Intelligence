"""Budget der PLE-Überlaufkaskade für vLLM-Einträge."""

import pytest

from aifred.lib.calibration.ple_cascade import plan_ple_cascade

# 32-GB-Sammelkarte, gemessene Reserven: VLM 8,6 GiB, TTS 7,5 GiB.
CARD_MB = 32_768
VLM_MB = 8_770
TTS_MB = 7_680


def _plan(**kwargs):
    defaults = dict(
        ple_bytes=47 * 1024**3,
        compute_gpu_ids=[0, 2, 1, 3],
        side_channel_gpu=4,
        side_channel_total_mb=CARD_MB,
        side_channel_reserved_mb=0,
        host_share_gib=2,
        safety_mb=1024,
    )
    defaults.update(kwargs)
    return plan_ple_cascade(**defaults)


def test_no_cascade_without_a_ple_table() -> None:
    assert _plan(ple_bytes=0) is None


def test_store_card_keeps_the_side_channels_free() -> None:
    plan = _plan(side_channel_reserved_mb=VLM_MB + TTS_MB)
    # 8770 + 7680 + 1024 = 17474 MiB stay free = 17,06 GiB, rounded up.
    assert plan.env["VLLM_QWEN4EXP_PLE_STORE_RESERVE_GIB"] == "17.1"
    assert plan.env["VLLM_QWEN4EXP_PLE_HOST_GIB"] == "2"
    # Die Karte haengt hinten an, ihr sichtbarer Index ist der letzte.
    assert plan.visible_gpu_ids == [0, 2, 1, 3, 4]
    assert plan.env["VLLM_QWEN4EXP_PLE_STORE_DEVICES"] == "4"
    # 32768 - 17474 = 15294 MiB are left for the table.
    assert plan.store_gib == pytest.approx(15294 / 1024)
    assert plan.uses_store_card


def test_variant_without_side_channels_keeps_only_the_safety_margin() -> None:
    plan = _plan(side_channel_reserved_mb=0)
    assert plan.env["VLLM_QWEN4EXP_PLE_STORE_RESERVE_GIB"] == "1"


def test_host_share_only_when_the_card_has_nothing_left() -> None:
    # Ein Seitenkanal, der die Karte fuellt: keine Store-Stufe, keine
    # Sichtbarkeit — sonst legt der Worker dort nur einen CUDA-Kontext an.
    plan = _plan(side_channel_reserved_mb=CARD_MB)
    assert plan.env == {"VLLM_QWEN4EXP_PLE_HOST_GIB": "2"}
    assert plan.visible_gpu_ids == [0, 2, 1, 3]
    assert not plan.uses_store_card


def test_host_share_only_without_a_spare_card() -> None:
    assert _plan(side_channel_gpu=None).env == {"VLLM_QWEN4EXP_PLE_HOST_GIB": "2"}
    # Rechnet die Sammelkarte selbst mit, gibt es keine freie Karte.
    assert _plan(side_channel_gpu=2).visible_gpu_ids == [0, 2, 1, 3]


def test_index_is_the_visible_position_not_the_pci_index() -> None:
    plan = _plan(compute_gpu_ids=[5, 7], side_channel_gpu=9)
    assert plan.visible_gpu_ids == [5, 7, 9]
    assert plan.env["VLLM_QWEN4EXP_PLE_STORE_DEVICES"] == "2"


def test_negative_settings_are_refused() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _plan(host_share_gib=-1)
    with pytest.raises(ValueError, match="non-negative"):
        _plan(safety_mb=-1)
