"""GGUF metadata readers (gguf_utils): one reader path, loud on unreadable files.

The files are written with gguf-py itself, tiny and synthetic — no model, no GPU.
"""

from pathlib import Path

import gguf
import numpy as np
import pytest

from aifred.lib import gguf_utils
from aifred.lib.gguf_utils import (
    get_gguf_architecture,
    get_gguf_chat_template,
    get_gguf_layer_count,
    get_gguf_lazy_tensor_bytes,
    get_gguf_native_context,
    read_gguf_field,
)


@pytest.fixture(autouse=True)
def _own_arch_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The architecture reader caches under ~/.cache; tests must not touch it.
    monkeypatch.setattr(gguf_utils, "_ARCH_CACHE_PATH", tmp_path / "arch.json")


@pytest.fixture
def gguf_file(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.gguf"
    writer = gguf.GGUFWriter(path, "llama")
    writer.add_block_count(32)
    writer.add_context_length(8192)
    writer.add_uint32("llama.expert_count", 8)
    writer.add_uint32("llama.expert_used_count", 0)
    writer.add_array("llama.some_array", [5, 6])
    writer.add_chat_template("{{ messages }}")
    writer.add_tensor("w", np.zeros(4, dtype=np.float32))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    return path


@pytest.fixture
def broken_file(tmp_path: Path) -> Path:
    path = tmp_path / "broken.gguf"
    path.write_bytes(b"not a gguf file at all")
    return path


def test_readers_return_metadata(gguf_file: Path) -> None:
    assert get_gguf_layer_count(gguf_file) == 32
    assert get_gguf_native_context(gguf_file) == 8192
    assert get_gguf_architecture(gguf_file) == "llama"
    assert get_gguf_chat_template(gguf_file) == "{{ messages }}"
    assert get_gguf_lazy_tensor_bytes(gguf_file) == 0


def test_read_gguf_field_values(gguf_file: Path) -> None:
    assert read_gguf_field(gguf_file, ".expert_count") == 8
    # A stored 0 is a real answer (dense), not "missing".
    assert read_gguf_field(gguf_file, ".expert_used_count") == 0
    # Arrays are not scalars.
    assert read_gguf_field(gguf_file, ".some_array") is None


def test_missing_field_is_none(gguf_file: Path) -> None:
    assert read_gguf_field(gguf_file, ".no_such_field") is None


def test_missing_template_is_none(tmp_path: Path) -> None:
    path = tmp_path / "no_template.gguf"
    writer = gguf.GGUFWriter(path, "llama")
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    assert get_gguf_chat_template(path) is None
    assert get_gguf_layer_count(path) is None


@pytest.mark.parametrize(
    "reader",
    [
        lambda p: read_gguf_field(p, ".expert_count"),
        get_gguf_layer_count,
        get_gguf_native_context,
        get_gguf_chat_template,
        get_gguf_architecture,
        get_gguf_lazy_tensor_bytes,
    ],
)
def test_unreadable_file_raises(reader, broken_file: Path, tmp_path: Path) -> None:
    with pytest.raises((OSError, ValueError)):
        reader(broken_file)
    with pytest.raises(OSError):
        reader(tmp_path / "absent.gguf")


def test_architecture_cache_not_written_on_failure(broken_file: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        get_gguf_architecture(broken_file)
    assert not (tmp_path / "arch.json").exists()
