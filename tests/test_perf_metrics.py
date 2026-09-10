"""decode_tokens_per_second — one decode definition for every backend."""

from aifred.lib.perf_metrics import decode_tokens_per_second


def test_server_rate_wins() -> None:
    assert decode_tokens_per_second(
        server_rate=48.6, tokens_generated=400, inference_time=20.0, first_token_s=5.0
    ) == 48.6


def test_fallback_excludes_time_to_first_token() -> None:
    # 400 tokens, 5 s until the first one, 25 s total -> 20 s of decode.
    rate = decode_tokens_per_second(
        tokens_generated=400, inference_time=25.0, first_token_s=5.0
    )
    assert rate == 20.0
    # The old wall-clock division would have reported 16 tok/s.
    assert rate > 400 / 25.0


def test_unmeasurable_returns_zero() -> None:
    assert decode_tokens_per_second(tokens_generated=0, inference_time=10.0, first_token_s=1.0) == 0.0
    assert decode_tokens_per_second(tokens_generated=50, inference_time=10.0, first_token_s=None) == 0.0
    assert decode_tokens_per_second(tokens_generated=1, inference_time=2.0, first_token_s=2.0) == 0.0
