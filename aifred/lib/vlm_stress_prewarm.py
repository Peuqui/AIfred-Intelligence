"""VLM Stress-Prewarm — Worst-Case VRAM Measurement.

Loads a VLM, fires a deliberately heavy inference (large image + max-context
+ long generation), polls GPU memory.used at 100 ms intervals, and returns
the observed peak. The calibration uses this as the static reserve when no
hand-measured entry exists in :data:`config.VLM_VRAM_BUDGET_MB`.

Why not :func:`vision_prewarm.prewarm_vlm`? That helper does a no-op
``generate(prompt="")`` which only measures the *idle* footprint after load
— it never touches the compute buffer or KV-cache. Empirically the idle vs.
bulk-load delta is 500–700 MB (see commit fc9ed2d on 4B-VLM), so calibrating
against idle leads to OOMs in production.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR

logger = logging.getLogger(__name__)

_STRESS_IMAGE_DIR = DATA_DIR / "calibration"
_STRESS_IMAGE_PATH = _STRESS_IMAGE_DIR / "vlm_stress_image.jpg"


def _generate_stress_image(path: Path, *, size: int = 1536) -> None:
    """Render a deterministic high-content image: random rectangles, lines
    and text on a noisy background. Triggers the VLM's vision encoder to
    allocate its full intermediate buffer (4096 image tokens) and gives the
    decoder plenty to describe — both needed to reach the production peak.

    Deterministic via fixed PRNG seed so cache invalidation only happens on
    explicit size/config changes, not run-to-run jitter.
    """
    from PIL import Image, ImageDraw, ImageFont  # heavy import — lazy
    import random

    rng = random.Random(42)
    img = Image.new("RGB", (size, size), color=(20, 20, 30))
    draw = ImageDraw.Draw(img)

    # Noise pixels — gives the encoder texture to chew on
    pixels = img.load()
    if pixels is not None:
        for y in range(0, size, 4):
            for x in range(0, size, 4):
                if rng.random() < 0.4:
                    pixels[x, y] = (
                        rng.randint(0, 255),
                        rng.randint(0, 255),
                        rng.randint(0, 255),
                    )

    # Many small rectangles — distinct objects to enumerate
    for _ in range(200):
        x0 = rng.randint(0, size - 100)
        y0 = rng.randint(0, size - 100)
        w = rng.randint(20, 200)
        h = rng.randint(20, 200)
        color = (rng.randint(50, 255), rng.randint(50, 255), rng.randint(50, 255))
        draw.rectangle([x0, y0, x0 + w, y0 + h], fill=color, outline=(255, 255, 255))

    # Diagonal lines — additional geometry
    for _ in range(50):
        x0, y0 = rng.randint(0, size), rng.randint(0, size)
        x1, y1 = rng.randint(0, size), rng.randint(0, size)
        draw.line([x0, y0, x1, y1], fill=(255, 255, 0), width=rng.randint(2, 8))

    # Text scattered across — invites longer descriptions
    try:
        font = ImageFont.load_default()
    except OSError:
        font = None
    words = ["CAT", "DOG", "TREE", "HOUSE", "CAR", "PERSON", "SKY", "WATER", "BOOK", "LAMP"]
    for _ in range(30):
        draw.text(
            (rng.randint(0, size - 200), rng.randint(0, size - 30)),
            rng.choice(words),
            fill=(255, 255, 255),
            font=font,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="JPEG", quality=88)
    logger.info("vlm_stress_prewarm: generated stress image at %s (%dx%d)", path, size, size)


def _ensure_stress_image() -> Path:
    """Return the stress image path, generating it on first use."""
    if not _STRESS_IMAGE_PATH.exists():
        _generate_stress_image(_STRESS_IMAGE_PATH)
    return _STRESS_IMAGE_PATH


def _image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _gpu_used_mb(gpu_index: int) -> int:
    """One-shot read of memory.used for a single GPU index."""
    from .nvidia_smi import query
    rows = query("memory.used", gpu_index=gpu_index)
    try:
        return int(rows[0]["memory.used"]) if rows else 0
    except ValueError as e:
        logger.warning("vlm_stress_prewarm: nvidia-smi read failed (%s)", e)
        return 0


class _PeakMonitor:
    """Background task that samples memory.used every ``interval`` seconds
    and tracks the maximum. Use as an async context manager."""

    def __init__(self, gpu_index: int, interval: float = 0.1) -> None:
        self._gpu = gpu_index
        self._interval = interval
        self._peak = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        while not self._stop.is_set():
            used = _gpu_used_mb(self._gpu)
            if used > self._peak:
                self._peak = used
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def __aenter__(self) -> "_PeakMonitor":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    @property
    def peak_mb(self) -> int:
        return self._peak


async def stress_prewarm_vlm(
    model: str,
    *,
    num_ctx: int,
    gpu_index: int,
    host: Optional[str] = None,
    load_timeout_seconds: float = 240.0,
    infer_timeout_seconds: float = 180.0,
) -> Optional[int]:
    """Load ``model`` into Ollama, run a stress inference, return observed
    peak VRAM in MiB on ``gpu_index``. Returns ``None`` on any failure
    (caller decides on fallback — e.g. ``VLM_VRAM_BUDGET_MB`` weight × 1.4
    or a conservative default).

    The function leaves the model **unloaded** at exit (``keep_alive=0``)
    so the subsequent calibration probe sees the full free VRAM again —
    the *measured peak* (plus caller-side headroom) is what gets subtracted
    from the budget, not the live VRAM.
    """
    try:
        from ollama import AsyncClient
    except ImportError as e:
        logger.error("vlm_stress_prewarm: ollama python client missing: %s", e)
        return None

    from .config import resolve_vlm_host
    effective_host = host or resolve_vlm_host()
    client = AsyncClient(host=effective_host)

    stress_image_path = _ensure_stress_image()
    image_b64 = _image_b64(stress_image_path)

    # ── Phase 1: load into VRAM ────────────────────────────────────
    logger.info(
        "vlm_stress_prewarm: loading model=%s num_ctx=%d on gpu=%d",
        model, num_ctx, gpu_index,
    )
    try:
        await asyncio.wait_for(
            client.generate(
                model=model,
                prompt="",
                keep_alive=-1,
                options={"num_ctx": num_ctx},
                stream=False,
            ),
            timeout=load_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("vlm_stress_prewarm: load timed out after %.0fs", load_timeout_seconds)
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("vlm_stress_prewarm: load failed: %s", e)
        return None

    # ── Phase 2: stress inference + peak monitoring ────────────────
    stress_prompt = (
        "Describe this image in extensive detail. Enumerate every visible "
        "object, color, geometric shape, line and any text fragments you "
        "can identify. Be thorough — list at least twenty distinct elements "
        "with their approximate positions."
    )
    peak: int = 0
    try:
        async with _PeakMonitor(gpu_index, interval=0.1) as monitor:
            await asyncio.wait_for(
                client.generate(
                    model=model,
                    prompt=stress_prompt,
                    images=[image_b64],
                    keep_alive=-1,
                    options={
                        "num_ctx": num_ctx,
                        "num_predict": 512,
                        "temperature": 0.7,
                    },
                    stream=False,
                ),
                timeout=infer_timeout_seconds,
            )
        peak = monitor.peak_mb
        logger.info(
            "vlm_stress_prewarm: peak VRAM observed = %d MiB on gpu=%d",
            peak, gpu_index,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "vlm_stress_prewarm: stress inference timed out after %.0fs",
            infer_timeout_seconds,
        )
        # Still return what we measured — partial signal beats none
        peak = max(peak, _gpu_used_mb(gpu_index))
    except Exception as e:  # noqa: BLE001
        logger.warning("vlm_stress_prewarm: stress inference failed: %s", e)
        peak = max(peak, _gpu_used_mb(gpu_index))

    # ── Phase 3: cleanup — unload so calibration sees free VRAM ────
    try:
        await asyncio.wait_for(
            client.generate(
                model=model,
                prompt="",
                keep_alive=0,
                stream=False,
            ),
            timeout=20.0,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("vlm_stress_prewarm: unload failed (continuing): %s", e)

    return peak if peak > 0 else None


def _query_gpu_uuid(gpu_index: int) -> Optional[str]:
    """Resolve a PCI_BUS_ID-ordered GPU index to its persistent NVIDIA UUID
    via nvidia-smi. Returns ``None`` on failure."""
    from .nvidia_smi import query
    rows = query("uuid", gpu_index=gpu_index)
    if not rows:
        logger.warning("vlm_stress_prewarm: UUID lookup for gpu=%d failed", gpu_index)
        return None
    return rows[0]["uuid"] or None


async def resolve_vlm_reserve(
    num_ctx: int,
    model_id_override: Optional[str] = None,
) -> tuple[Optional[str], int]:
    """Determine ``(vlm_gpu_uuid, reserve_mb)`` for a VLM model. Returns
    ``(None, 0)`` when no work is required.

    Two call modes:

    * **Phase 1 — no override.** Reads :func:`is_vision_active` and
      :func:`get_active_vlm_model` from the plugin settings; if vision is
      off, returns ``(None, 0)``. This is the path the BASE/TTS
      calibration takes when the user did not pick any VLM checkbox in
      the modal.
    * **Phase 2 — explicit override.** The modal loop passes a concrete
      ``model_id`` (e.g. ``qwen3-vl:8b-instruct-q8_0``); ``vision_mode``
      and ``vlm.model`` from settings.json are ignored. This is how the
      multi-variant loop measures each user-selected VLM independently.

    Resolution order — hardware-agnostic, no hand-pinned tables:

    1. **JSON cache** :mod:`vlm_vram_cache` — previous stress-prewarm runs.
       Adds :data:`config.LLAMACPP_VLM_HEADROOM_MB` on top.
    2. **Stress prewarm** (cold path) — fires a worst-case inference, writes
       the peak to the cache, returns ``peak + headroom``.

    Failure of step 2 returns ``(uuid, 0)`` — caller should warn and skip
    the reservation. The alternative (a fabricated default) would either
    over-allocate (waste VRAM) or under-allocate (OOM) without any signal
    to the user; better to surface the problem than paper over it.
    """
    # 1. Resolve model id — explicit override wins over plugin settings
    if model_id_override:
        model_id: Optional[str] = model_id_override
    else:
        from .vision_prewarm import is_vision_active, get_active_vlm_model
        if not is_vision_active():
            return None, 0
        model_id = get_active_vlm_model()
    if not model_id:
        return None, 0

    # 2. GPU selection + UUID lookup
    from .vision_gpu_select import pick_vlm_gpu
    try:
        gpu_idx = pick_vlm_gpu()
    except RuntimeError as e:
        logger.info("vlm reserve: no GPU available for VLM (%s) — skipping reserve", e)
        return None, 0
    uuid = _query_gpu_uuid(gpu_idx)
    if not uuid:
        return None, 0

    # 3. Resolve reserve_mb — agnostic, cache + stress prewarm only.
    from .config import LLAMACPP_VLM_HEADROOM_MB
    from . import vlm_vram_cache

    # 3a. Cache hit — stress-measured peak + runtime headroom.
    cached = vlm_vram_cache.get(model_id, num_ctx)
    if cached:
        reserve = cached + LLAMACPP_VLM_HEADROOM_MB
        logger.info(
            "vlm reserve: cache hit for %s (num_ctx=%d) → %d + %d MiB headroom = %d",
            model_id, num_ctx, cached, LLAMACPP_VLM_HEADROOM_MB, reserve,
        )
        return uuid, reserve

    # 3b. Cold path — stress prewarm + persist.
    logger.info(
        "vlm reserve: cold path — stress-prewarming %s at num_ctx=%d on gpu=%d",
        model_id, num_ctx, gpu_idx,
    )
    peak = await stress_prewarm_vlm(model_id, num_ctx=num_ctx, gpu_index=gpu_idx)
    if peak is None:
        logger.warning(
            "vlm reserve: stress prewarm failed for %s — caller should warn user "
            "(no reservation applied)", model_id,
        )
        return uuid, 0
    vlm_vram_cache.put(model_id, num_ctx, peak)
    reserve = peak + LLAMACPP_VLM_HEADROOM_MB
    logger.info(
        "vlm reserve: stress-measured %s peak=%d MiB + headroom %d = %d",
        model_id, peak, LLAMACPP_VLM_HEADROOM_MB, reserve,
    )
    return uuid, reserve
