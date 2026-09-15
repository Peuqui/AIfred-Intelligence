"""vLLM seeding in the llama-swap autoscan.

A checkpoint already served by an entry must not be seeded again just
because that entry was renamed (14.09.2026: the nvidia Flash-Next entry,
renamed to carry MTP in its name, would have come back under the repo
name on the next restart). Seeded names mark an MTP draft block.
"""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(scope="module")
def autoscan():
    sys.path.insert(0, str(SCRIPTS))
    loader = importlib.machinery.SourceFileLoader(
        "llama_swap_autoscan", str(SCRIPTS / "llama-swap-autoscan.py")
    )
    spec = importlib.util.spec_from_loader("llama_swap_autoscan", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_registered_model_paths_resolve_renamed_entries(autoscan, tmp_path: Path) -> None:
    checkpoint = tmp_path / "snapshots" / "abc"
    checkpoint.mkdir(parents=True)
    link = tmp_path / "alias"
    link.symlink_to(checkpoint)
    config = tmp_path / "config.yaml"
    config.write_text(
        "models:\n"
        "  Qwen3.8-Flash-Next-180B-A4B-NVFP4-MTP-vllm:\n"
        f"    cmd: python -m vllm.entrypoints.openai.api_server --model {link} --port 1\n"
        "    ttl: 3600\n"
        "groups:\n"
    )

    registered = autoscan.registered_model_paths(config)

    assert checkpoint.resolve() in registered


@pytest.mark.parametrize(
    ("base", "has_mtp", "expected"),
    [
        ("Qwen3.8-Flash-Next-NVFP4", True, "Qwen3.8-Flash-Next-NVFP4-MTP-vllm"),
        ("Qwen3.8-Flash-Next-NVFP4", False, "Qwen3.8-Flash-Next-NVFP4-vllm"),
        ("Qwen3.8-27B-MTP-NVFP4", True, "Qwen3.8-27B-MTP-NVFP4-vllm"),
    ],
)
def test_vllm_seed_name_marks_mtp(autoscan, base: str, has_mtp: bool, expected: str) -> None:
    assert autoscan.vllm_seed_name(base, has_mtp) == expected
