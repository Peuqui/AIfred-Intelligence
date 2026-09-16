"""Model dropdowns name the speculative predictor of every entry."""

from aifred.lib.calibration.llamaswap_io import entry_badges, speculative_predictor


def test_vllm_speculative_config_method() -> None:
    cmd = """python -m vllm --model /m --speculative-config '{"method":"dflash","num_speculative_tokens":7}'"""
    assert speculative_predictor(cmd) == "DFlash"
    assert speculative_predictor(cmd.replace("dflash", "mtp")) == "MTP"
    assert speculative_predictor(cmd.replace("dflash", "dspark")) == "DSpark"


def test_llamacpp_spec_type() -> None:
    assert speculative_predictor("llama-server --model x.gguf --spec-type draft-mtp --spec-draft-n-max 3") == "MTP"
    assert speculative_predictor("llama-server --model x.gguf --spec-type ngram-mod") == "n-gram"


def test_no_predictor_and_unknown_method() -> None:
    assert speculative_predictor("llama-server --model x.gguf -c 4096") == ""
    assert speculative_predictor("""vllm --speculative-config '{"method":"medusa"}'""") == "medusa"


def test_badges_skip_a_predictor_the_name_already_carries() -> None:
    """The dropdown named "…-MTP-vllm · MTP" twice (Peuqui, 16.09.2026)."""
    info = {"full_cmd": """vllm --speculative-config '{"method":"mtp"}'""", "env": {}}
    assert entry_badges("Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm", info) == []
    assert entry_badges("Qwen3.8-27B-NVFP4-vllm", info) == ["MTP"]
    # A variant number belongs to the same predictor: "DFlash2" runs DFlash.
    dflash = {"full_cmd": """vllm --speculative-config '{"method":"dflash"}'""", "env": {}}
    assert entry_badges("Qwen3.8-27B-NVFP4-DFlash2-vllm", dflash) == []
    # A name that only contains the letters elsewhere still gets the badge.
    assert entry_badges("Qwen3-Promtper-vllm", info) == ["MTP"]


def test_badges_mark_a_named_predictor_that_is_switched_off() -> None:
    """A missing badge must not mean both "as named" and "speculation off"."""
    off = {"full_cmd": "vllm --model /m", "env": {}}
    assert entry_badges("Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm", off) == ["spec off"]
    # A name without a predictor says nothing about speculation, so no badge.
    assert entry_badges("Qwen3-0.6B-vllm", off) == []


def test_badges_name_the_ple_overflow_card() -> None:
    info = {
        "full_cmd": """vllm --speculative-config '{"method":"mtp"}'""",
        "env": {"VLLM_QWEN4EXP_PLE_STORE_DEVICE": "4", "VLLM_QWEN4EXP_PLE_HOST_GIB": "2"},
    }
    assert entry_badges("Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm", info) == ["PLE→GPU 4"]
    assert entry_badges("Qwen3.8-27B-NVFP4-vllm", info) == ["MTP", "PLE→GPU 4"]
    # Without the cascade only the runtime of the named predictor remains.
    plain = {"full_cmd": "vllm", "env": {}}
    assert entry_badges("Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm", plain) == ["spec off"]
