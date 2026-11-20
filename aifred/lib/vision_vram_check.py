"""Passt ein Describer-Profil (``-visiond``) gerade neben das Geladene? (:func:`check_visiond_fits`, gefragt von
``vision_routing.fitting_visiond`` für jede Wahl eines Beschreibers dieses Rechners)."""

from __future__ import annotations

from dataclasses import dataclass

from .user_error import UserError


@dataclass
class VRAMCheckResult:
    fits: bool
    needed_mb: int
    free_mb: int
    gpu_index: int
    reason: UserError | None = None  # why it does not fit (or what was skipped): ``reason.message(lang)`` for a page

    @property
    def message(self) -> str:
        """The reason in English, for logs and the debug console."""
        return str(self.reason) if self.reason else ""


def check_visiond_fits(profile: str) -> VRAMCheckResult:
    """Passt das llama-swap-Describer-Profil ``<base>-visiond`` (oder eine
    Platzierungs-Variante ``-visiond-gpu<N>``) neben das, was gerade
    geladen ist?

    Bedarf = Burn-in-Peak aus ``vlm_vram_cache`` beim Kontext des Profils
    plus ``VLM_HEADROOM_MIB`` (dieselbe Reserve wie in der
    Kalibrierung). Verglichen wird mit dem freien VRAM der Karte(n), auf
    die das Profil per ``CUDA_VISIBLE_DEVICES`` gepinnt ist (ohne Pin: alle
    Karten), plus dem, was STT- und TTS-Worker dort belegen — die räumt GPU-Orc vor dem
    Laden frei — llama.cpp verteilt das Profil über genau diese Karten, also
    zählt ihre Summe. Ist das Profil schon geladen, passt es.
    """
    from . import vlm_vram_cache
    from .calibration.llamaswap_io import parse_llamaswap_config
    from .config import LLAMASWAP_CONFIG_PATH, VLM_HEADROOM_MIB
    from .nvidia_smi import query
    from .vision_routing import loaded_llamaswap_profiles

    if profile in loaded_llamaswap_profiles():
        return VRAMCheckResult(
            fits=True, needed_mb=0, free_mb=0, gpu_index=-1,
            reason=UserError("vram_profile_loaded", profile=profile),
        )

    from .vlm_naming import VISIOND_SUFFIX, visiond_home

    entry = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)[profile]
    base = visiond_home(profile).removesuffix(VISIOND_SUFFIX)
    num_ctx = int(entry["current_context"])
    peak_mb = vlm_vram_cache.get(base, num_ctx)
    if peak_mb is None:
        return VRAMCheckResult(
            fits=False, needed_mb=0, free_mb=0, gpu_index=-1,
            reason=UserError("vram_not_measured", model=base, context=num_ctx),
        )
    needed_mb = peak_mb + VLM_HEADROOM_MIB

    # Was GPU-Orc vor dem Laden freiräumt, zählt als frei: alles auf den Karten außer den Modellen von
    # llama-swap (Rangfolge Hauptmodell > Describer > TTS > STT, release_side_channels_for_describer).
    # Gezählt wird nur, was die Dienste selbst gemessen melden; ein Halter ohne Zahl gibt keinen Platz her.
    from .config import GPU_ORC_MODEL_SERVER
    from .gpu_orc_client import GpuOrcError, get_gpu_orc_client
    client = get_gpu_orc_client()
    try:
        # GPU-Orc off (empty GPU_ORC_URL): nobody clears the cards, so only the free VRAM counts.
        cards = [] if client is None else client.card_status()
    except GpuOrcError as error:
        # Who holds what is unknown, so is the room: no placement is claimed (like an unmeasured VLM
        # above), and the reason is shown instead of an error in the page.
        return VRAMCheckResult(
            fits=False, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
            reason=UserError("vram_card_usage_unknown", profile=profile, error=error),
        )
    releasable = {
        card.uuid: sum(
            holder.memory_mib or 0 for holder in card.holders if holder.consumer != GPU_ORC_MODEL_SERVER
        )
        for card in cards
    }
    rows = query("index,uuid,memory.free") or []
    free_by_uuid = {
        r["uuid"]: (int(r["index"]), int(r["memory.free"]) + releasable.get(r["uuid"], 0))
        for r in rows
    }
    pinned = [
        u.strip() for u in str(entry["env"].get("CUDA_VISIBLE_DEVICES", "")).split(",")
        if u.strip()
    ]
    if pinned:
        missing = [u for u in pinned if u not in free_by_uuid]
        if missing:
            return VRAMCheckResult(
                fits=False, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
                reason=UserError("vram_pinned_gpu_missing", profile=profile, gpus=", ".join(missing)),
            )
        candidates = [free_by_uuid[u] for u in pinned]
    else:
        candidates = list(free_by_uuid.values())
    if not candidates:
        return VRAMCheckResult(
            fits=False, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
            reason=UserError("vram_no_gpus"),
        )
    free_mb = sum(free for _, free in candidates)
    gpu_index = candidates[0][0] if len(candidates) == 1 else -1
    if free_mb >= needed_mb:
        return VRAMCheckResult(
            fits=True, needed_mb=needed_mb, free_mb=free_mb, gpu_index=gpu_index,
        )
    return VRAMCheckResult(
        fits=False, needed_mb=needed_mb, free_mb=free_mb, gpu_index=gpu_index,
        reason=UserError("vram_needs", model=base, needed=needed_mb, free=free_mb, gpus=len(candidates)),
    )
