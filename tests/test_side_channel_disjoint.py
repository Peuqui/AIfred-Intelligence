"""Isolated-mode disjointness check for calibration variants.

``side_channel_disjoint`` decides whether a calibrated base/speed profile
can be copied verbatim as a TTS/VLM/combo variant: when the profile's
UUID-pinned GPU set never touches a side-channel GPU, the side channel
cannot influence the LLM's VRAM layout, so projection + probes are
skipped. The check must be conservative — any missing information
(no pinning, unknown side-channel GPU, single-GPU host) means False and
the caller runs the full calibration as before.
"""

from __future__ import annotations

from aifred.lib.calibration.llamaswap_io import side_channel_disjoint

LLM_UUIDS = ["GPU-aaaa", "GPU-bbbb"]


def test_disjoint_single_side_channel():
    assert side_channel_disjoint(LLM_UUIDS, ["GPU-cccc"], total_gpus=5)


def test_disjoint_multiple_side_channels():
    assert side_channel_disjoint(LLM_UUIDS, ["GPU-cccc", "GPU-dddd"], total_gpus=5)


def test_side_channel_inside_profile_set():
    assert not side_channel_disjoint(LLM_UUIDS, ["GPU-bbbb"], total_gpus=5)


def test_one_of_several_side_channels_overlaps():
    # Combo case: TTS disjoint but VLM on an LLM GPU → full calibration.
    assert not side_channel_disjoint(
        LLM_UUIDS, ["GPU-cccc", "GPU-aaaa"], total_gpus=5,
    )


def test_shared_side_channel_gpu_is_fine():
    # TTS and VLM on the SAME non-LLM card is still disjoint from the
    # LLM's point of view (capacity on that card is checked elsewhere).
    assert side_channel_disjoint(LLM_UUIDS, ["GPU-cccc", "GPU-cccc"], total_gpus=5)


def test_unpinned_profile_is_conservative():
    # Legacy profile without CUDA_VISIBLE_DEVICES UUIDs → cannot verify.
    assert not side_channel_disjoint([], ["GPU-cccc"], total_gpus=5)


def test_unknown_side_channel_gpu_is_conservative():
    # UUID lookup failed (None) → cannot verify.
    assert not side_channel_disjoint(LLM_UUIDS, [None], total_gpus=5)
    assert not side_channel_disjoint(LLM_UUIDS, [""], total_gpus=5)


def test_no_side_channels_is_conservative():
    # Nothing to be disjoint from — caller bug, refuse the shortcut.
    assert not side_channel_disjoint(LLM_UUIDS, [], total_gpus=5)


def test_single_gpu_host_never_shortcuts():
    # Hardware without a separable side-channel card: always calibrate.
    assert not side_channel_disjoint(["GPU-aaaa"], ["GPU-aaaa"], total_gpus=1)
    assert not side_channel_disjoint(["GPU-aaaa"], ["GPU-bbbb"], total_gpus=1)
