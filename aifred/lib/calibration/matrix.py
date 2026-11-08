"""What the calibration matrix shows for a model: one row per VLM choice (no VLM first), one column per TTS
engine one can calibrate against (no TTS first).

llama.cpp: a cell is a llama-swap profile of the model (the base, a TTS variant, a VLM variant or both),
done when it was really calibrated (``gpu_model`` on its cache entry), failed when the last try left a
failure record. vLLM: the base cell is the model's operating point on this hardware; every other cell a
pair of describer and TTS engine on the side-channel card, done when their reserves fit on it together, failed
when they do not (``calibration.service.side_channel_pair_fits``; vLLM computes only on the cards the side channel
leaves, so a profile per combination would measure the same thing each time).

Which cells a run calibrates is the choice of the client that starts it, no part of the matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CellStatus = Literal["done", "failed", "open"]


@dataclass(frozen=True)
class MatrixColumn:
    tts_key: str  # "" = no TTS
    label: str


@dataclass(frozen=True)
class MatrixCell:
    key: str  # cell_key(vlm_key, tts_key)
    status: CellStatus


@dataclass(frozen=True)
class MatrixRow:
    vlm_key: str  # "" = no VLM
    label: str
    cells: tuple[MatrixCell, ...]  # in the order of the columns


@dataclass(frozen=True)
class CalibrationMatrix:
    columns: tuple[MatrixColumn, ...]
    rows: tuple[MatrixRow, ...]
    tts_problem: str  # why there is no TTS column although engines may exist ("" = the TTS service answered)


def cell_key(vlm_key: str, tts_key: str) -> str:
    """``"<vlm_key>|<tts_key>"``, either side empty for none (``"|"`` is the base)."""
    return f"{vlm_key}|{tts_key}"


def variant_id(model_id: str, key: str) -> str:
    """The llama-swap profile (and cache entry) of a llama.cpp cell: the model itself,
    ``<model>-tts-<engine>``, ``<model>-vlm-<vlm>`` or ``<model>-tts-<engine>-vlm-<vlm>``."""
    vlm_key, tts_key = key.split("|", 1)
    if vlm_key and tts_key:
        return f"{model_id}-tts-{tts_key}-vlm-{vlm_key}"
    if vlm_key:
        return f"{model_id}-vlm-{vlm_key}"
    if tts_key:
        return f"{model_id}-tts-{tts_key}"
    return model_id


def engine_columns(lang: str | None) -> tuple[tuple[MatrixColumn, ...], str]:
    """The "no TTS" column and one per installed local GPU engine; a TTS service that does not answer gives
    no engine column and says why."""
    from ..i18n import t
    from ..tts_client import TTSSuiteUnreachable
    from ..tts_service import (
        engine_label_short,
        installed_gpu_engine_keys,
        unreachable_text,
    )

    none = MatrixColumn("", t("calibration_matrix_no_tts", lang=lang))
    try:
        engines = tuple(MatrixColumn(key, engine_label_short(key, lang)) for key in installed_gpu_engine_keys())
    except TTSSuiteUnreachable as unreachable:
        return (none,), unreachable_text(unreachable, lang)
    return (none, *engines), ""


def _vlm_rows(lang: str | None) -> list[tuple[str, str]]:
    """``(vlm_key, label)`` of every row: no VLM, then the describers one can calibrate."""
    from ..i18n import t
    from ..vision_routing import vlm_calibration_choices

    return [("", t("calibration_matrix_no_vlm", lang=lang)),
            *((choice["key"], choice["label"]) for choice in vlm_calibration_choices())]


def _status(done: bool, failed: bool) -> CellStatus:
    if failed:
        return "failed"
    return "done" if done else "open"


def _llamacpp_status(model_id: str, key: str) -> CellStatus:
    from ..model_vram_cache import (
        is_calibration_failed,
        is_model_calibrated,
        is_tts_variant_calibrated,
        is_vlm_variant_calibrated,
        load_cache,
    )

    if not model_id:
        return "open"
    vlm_key, tts_key = key.split("|", 1)
    variant = variant_id(model_id, key)
    if vlm_key and tts_key:
        entry = load_cache().get(variant)
        done = bool(entry and entry.get("gpu_model"))
    elif vlm_key:
        done = is_vlm_variant_calibrated(model_id, vlm_key)
    elif tts_key:
        done = is_tts_variant_calibrated(model_id, tts_key)
    else:
        done = is_model_calibrated(model_id)
    return _status(done, is_calibration_failed(variant))


def _vllm_status(model_id: str, key: str) -> CellStatus:
    from .. import sidechannel_vram_cache
    from ..operating_points import is_vllm_calibrated

    vlm_key, tts_key = key.split("|", 1)
    if not vlm_key and not tts_key:
        return "done" if model_id and is_vllm_calibrated(model_id) else "open"
    verdict = sidechannel_vram_cache.fits(vlm_key, tts_key)
    return "open" if verdict is None else _status(verdict, not verdict)


def matrix(backend_type: str, model_id: str, lang: str | None = None) -> CalibrationMatrix | None:
    """The matrix of ``model_id`` under ``backend_type``; None for a backend without one (Ollama and the
    cloud calibrate without variants)."""
    if backend_type == "llamacpp":
        status = _llamacpp_status
    elif backend_type == "vllm":
        status = _vllm_status
    else:
        return None
    columns, problem = engine_columns(lang)
    rows = tuple(
        MatrixRow(vlm_key, label, tuple(
            MatrixCell(key, status(model_id, key))
            for key in (cell_key(vlm_key, column.tts_key) for column in columns)
        ))
        for vlm_key, label in _vlm_rows(lang)
    )
    return CalibrationMatrix(columns, rows, problem)


def all_cell_keys() -> list[str]:
    """Every cell of the matrix as it is now (the engines and describers change at run time)."""
    columns, _problem = engine_columns(None)
    return [cell_key(vlm_key, column.tts_key) for vlm_key, _label in _vlm_rows(None) for column in columns]


def clear_failure(model_id: str, key: str) -> bool:
    """Drop the failure record of a llama.cpp cell's profile, so the next run tries it again (a user
    ticking a failed cell asks for exactly that). False when there was none, the key is no cell or names a
    describer that is gone."""
    from ..model_vram_cache import load_cache, save_cache
    from ..vision_routing import vlm_calibration_choices

    if not model_id or "|" not in key:
        return False
    vlm_key = key.split("|", 1)[0]
    if vlm_key and vlm_key not in {choice["key"] for choice in vlm_calibration_choices()}:
        return False
    variant = variant_id(model_id, key)
    cache = load_cache()
    entry = cache.get(variant)
    if not entry or not entry.get("failure_status"):
        return False
    entry.pop("failure_status")
    cache[variant] = entry
    save_cache(cache)
    return True
