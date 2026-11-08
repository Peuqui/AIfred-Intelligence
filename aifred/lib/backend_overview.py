"""What the server knows about its LLM backends without a browser: the GPUs (detected once per process), the
backends they can run, the backend in use (settings.json) and the cloud providers with the state of their key."""

from __future__ import annotations

from dataclasses import dataclass

from .config import BACKEND_LABELS, CLOUD_API_PROVIDERS
from .gpu_detection import GPUInfo


@dataclass(frozen=True)
class GpuCard:
    name: str
    vram_mb: int
    compute_capability: float


@dataclass(frozen=True)
class GpuOverview:
    cards: tuple[GpuCard, ...]  # ordered by compute capability, lowest first (as detected)
    total_vram_gb: float
    unsupported_backends: tuple[str, ...]  # no card meets their needs


@dataclass(frozen=True)
class BackendChoice:
    id: str
    label: str


@dataclass(frozen=True)
class CloudProvider:
    id: str
    label: str
    key_configured: bool


@dataclass(frozen=True)
class BackendOverview:
    backend_type: str
    backends: tuple[BackendChoice, ...]  # the ones these GPUs can run, and the cloud
    cloud_provider: str
    cloud_providers: tuple[CloudProvider, ...]
    gpu: GpuOverview | None  # None: no GPU found (nvidia-smi missing or silent)


def available_backends(gpu: GPUInfo | None) -> list[str]:
    """The backends to offer: those at least one card can run, and the cloud; every backend when no GPU
    was found or none fits (the user may still point one at another machine)."""
    if gpu is None or not gpu.recommended_backends:
        return list(BACKEND_LABELS)
    return [*gpu.recommended_backends, *(["cloud_api"] if "cloud_api" not in gpu.recommended_backends else [])]


def _gpu_overview(gpu: GPUInfo) -> GpuOverview:
    from .gpu_utils import total_actual_vram_gb

    cards = tuple(
        GpuCard(name, vram_mb, compute)
        for name, vram_mb, compute in zip(gpu.all_gpu_names, gpu.all_gpu_vram_mb, gpu.all_gpu_compute_caps, strict=True)
    )
    return GpuOverview(cards, total_actual_vram_gb(gpu), tuple(gpu.unsupported_backends))


def backend_overview() -> BackendOverview:
    """Detects the GPUs on the first call (nvidia-smi): call it off the event loop."""
    from ..backends.cloud_api import is_cloud_api_configured
    from .gpu_detection import server_gpu_info
    from .settings import persisted_settings

    saved = persisted_settings()
    gpu = server_gpu_info()
    return BackendOverview(
        backend_type=saved["backend_type"],
        backends=tuple(BackendChoice(backend, BACKEND_LABELS[backend]) for backend in available_backends(gpu)),
        cloud_provider=saved["cloud_api_provider"],
        cloud_providers=tuple(
            CloudProvider(provider, entry["name"], is_cloud_api_configured(provider))
            for provider, entry in CLOUD_API_PROVIDERS.items()
        ),
        gpu=_gpu_overview(gpu) if gpu else None,
    )
