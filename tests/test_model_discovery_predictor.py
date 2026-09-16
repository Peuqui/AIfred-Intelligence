"""Model dropdowns name the speculative predictor of every entry."""

from aifred.lib.calibration.llamaswap_io import (
    display_model_name,
    entry_badges,
    ple_cascade_path,
    speculative_predictor,
)


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


def test_badges_show_the_ple_cascade_as_a_path() -> None:
    """Die Stufen jenseits des VRAM, in der Reihenfolge der Kaskade."""
    info = {
        "full_cmd": """vllm --speculative-config '{"method":"mtp"}'""",
        "env": {
            "CUDA_VISIBLE_DEVICES": "0,2,1,3,4",
            "VLLM_QWEN4EXP_PLE_STORE_DEVICE": "4",
            "VLLM_QWEN4EXP_PLE_HOST_GIB": "2",
        },
    }
    assert entry_badges("Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm", info) == [
        "PLE→Host→GPU 4"
    ]
    assert entry_badges("Qwen3.8-27B-NVFP4-vllm", info) == ["MTP", "PLE→Host→GPU 4"]

    # Mit SSD-Stufe und ohne Speicherkarte.
    full = {**info, "env": {**info["env"], "VLLM_QWEN4EXP_PLE_DISK": "1"}}
    assert ple_cascade_path(full["env"]) == "PLE→Host→GPU 4→SSD"
    assert ple_cascade_path({"VLLM_QWEN4EXP_PLE_HOST_GIB": "6"}) == "PLE→Host"
    # Ein Host-Anteil von 0 ist keine Stufe.
    assert ple_cascade_path({"VLLM_QWEN4EXP_PLE_HOST_GIB": "0"}) == ""

    # Der sichtbare Index wird auf die Karte abgebildet, die der Nutzer kennt.
    reordered = {"CUDA_VISIBLE_DEVICES": "0,2,1,3,7", "VLLM_QWEN4EXP_PLE_STORE_DEVICE": "4"}
    assert ple_cascade_path(reordered) == "PLE→GPU 7"
    # UUID-Listen (llama.cpp-Eintraege) bleiben beim sichtbaren Index.
    uuids = {"CUDA_VISIBLE_DEVICES": "GPU-abc,GPU-def", "VLLM_QWEN4EXP_PLE_STORE_DEVICE": "1"}
    assert ple_cascade_path(uuids) == "PLE→GPU 1"
    # Without the cascade only the runtime of the named predictor remains.
    plain = {"full_cmd": "vllm", "env": {}}
    assert entry_badges("Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm", plain) == ["spec off"]


def test_display_name_drops_what_the_cascade_badge_already_says() -> None:
    """Der Schluessel bleibt eindeutig, die Anzeige doppelt ihn nicht."""
    base = "Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm"
    for variant in ("-PLE-Disk", "-PLE-Classic"):
        model_id = base.replace("-vllm", f"{variant}-vllm")
        assert display_model_name(model_id, ["PLE→Host→GPU 4"]) == base
    # Ohne Kaskaden-Badge bleibt der Name, wie er ist.
    assert display_model_name(base + "-PLE-Disk", ["MTP"]) == base + "-PLE-Disk"
    assert display_model_name(base, ["PLE→Host"]) == base
