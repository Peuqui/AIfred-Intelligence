"""Autoscan prunes an entry when its --model file OR its local draft model is gone."""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "llama_swap_autoscan",
    Path(__file__).resolve().parent.parent / "scripts" / "llama-swap-autoscan.py",
)
autoscan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(autoscan)


def _dflash_cmd(target: Path, draft: str) -> str:
    return (
        f"python -m vllm.entrypoints.openai.api_server --model {target} "
        f"""--speculative-config '{{"method":"dflash","model":"{draft}","num_speculative_tokens":7}}'"""
    )


def test_entry_with_target_and_draft_present_is_kept(tmp_path):
    target, draft = tmp_path / "target", tmp_path / "draft"
    target.mkdir()
    draft.mkdir()
    assert autoscan._missing_model_files(_dflash_cmd(target, str(draft))) == []


def test_missing_draft_marks_entry_stale(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    draft = tmp_path / "deleted-draft"
    assert autoscan._missing_model_files(_dflash_cmd(target, str(draft))) == [draft]


def test_missing_model_file_marks_entry_stale(tmp_path):
    gguf = tmp_path / "gone.gguf"
    assert autoscan._missing_model_files(f"llama-server --model {gguf} --port 9999") == [gguf]


def test_mtp_and_hub_drafts_need_no_local_file(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    mtp = f"""vllm --model {target} --speculative-config '{{"method":"mtp","num_speculative_tokens":4}}'"""
    hub = _dflash_cmd(target, "org/some-draft")
    assert autoscan._missing_model_files(mtp) == []
    assert autoscan._missing_model_files(hub) == []
