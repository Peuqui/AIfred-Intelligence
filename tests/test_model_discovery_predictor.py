"""Model dropdowns name the speculative predictor of every entry."""

from aifred.lib.calibration.llamaswap_io import speculative_predictor


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
