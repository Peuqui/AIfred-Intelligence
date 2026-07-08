"""
GPU Detection and Compatibility Checking

Detects GPU compute capability and provides backend compatibility info.
Prevents users from trying to use incompatible backends with their GPU.
"""

from typing import Optional, List, Dict, Any, TypedDict
from dataclasses import dataclass, field
from . import nvidia_smi


class GPUDict(TypedDict):
    """Type for parsed GPU info from nvidia-smi"""
    name: str
    vram_mb: int
    compute_cap: float


class BackendRequirement(TypedDict):
    """Type for backend requirements"""
    min_compute_capability: float
    requires_tensor_cores: bool
    requires_fast_fp16: bool
    description: str


class KnownIssue(TypedDict):
    """Type for known GPU issues"""
    fp16_ratio: str
    issue: str
    recommendation: str


@dataclass
class GPUInfo:
    """GPU information and capabilities"""
    name: str
    compute_capability: float  # Lowest CC across all GPUs (for display)
    vram_mb: int
    has_tensor_cores: bool
    supports_fast_fp16: bool
    recommended_backends: List[str]
    unsupported_backends: List[str]
    warnings: List[str]
    # Multi-GPU info
    gpu_count: int = 1
    total_vram_mb: int = 0
    all_gpu_names: List[str] = field(default_factory=list)
    all_gpu_vram_mb: List[int] = field(default_factory=list)
    all_gpu_compute_caps: List[float] = field(default_factory=list)
    # Per-backend compatible GPU indices, e.g. {"vllm": [0], "llamacpp": [0, 1]}
    backend_gpu_indices: Dict[str, List[int]] = field(default_factory=dict)


class GPUDetector:
    """
    Detect GPU capabilities and provide backend compatibility information

    Usage:
        detector = GPUDetector()
        gpu_info = detector.detect()
        if gpu_info:
            print(f"GPU: {gpu_info.name}")
            print(f"Compute Capability: {gpu_info.compute_capability}")
            print(f"Recommended Backends: {gpu_info.recommended_backends}")
    """

    # Compute Capability → Architecture mapping
    ARCH_MAP = {
        (3, 0): "Kepler",
        (3, 5): "Kepler",
        (5, 0): "Maxwell",
        (5, 2): "Maxwell",
        (6, 0): "Pascal",
        (6, 1): "Pascal",  # Tesla P40, P100
        (7, 0): "Volta",   # V100
        (7, 5): "Turing",  # RTX 2080, T4
        (8, 0): "Ampere",  # A100
        (8, 6): "Ampere",  # RTX 3090, A40
        (8, 9): "Ada Lovelace",  # RTX 4090
        (9, 0): "Hopper",  # H100
    }

    # Backend requirements
    BACKEND_REQUIREMENTS: Dict[str, BackendRequirement] = {
        "ollama": {
            "min_compute_capability": 3.5,
            "requires_tensor_cores": False,
            "requires_fast_fp16": False,
            "description": "GGUF models (INT8/Q4/Q8), universal compatibility"
        },
        "llamacpp": {
            "min_compute_capability": 3.5,
            "requires_tensor_cores": False,
            "requires_fast_fp16": False,
            "description": "llama.cpp + llama-swap (GGUF), universal compatibility"
        },
        "vllm": {
            "min_compute_capability": 7.0,
            "requires_tensor_cores": False,
            "requires_fast_fp16": True,
            "description": "AWQ/GPTQ models, requires Volta+ (7.0+)"
        }
    }

    # Known problematic GPUs
    KNOWN_ISSUES: Dict[str, Dict[str, Any]] = {
        "Tesla P40": {
            "fp16_ratio": "1:64",
            "issue": "Extremely slow FP16 performance",
            "recommendation": "Use Ollama or llama.cpp (GGUF) only"
        },
        "Tesla P100": {
            "fp16_ratio": "1:2",
            "issue": "Moderate FP16 performance",
            "recommendation": "Ollama/llama.cpp recommended, vLLM possible but slower"
        },
        "Tesla K80": {
            "compute_capability": 3.7,
            "issue": "Too old for modern inference frameworks",
            "recommendation": "Upgrade GPU"
        }
    }

    def __init__(self):
        self.gpu_info: Optional[GPUInfo] = None

    def detect(self) -> Optional[GPUInfo]:
        """
        Detect GPU and return GPUInfo

        For multi-GPU systems, checks backend compatibility per GPU.
        A backend is available if at least one GPU meets its requirements.

        Returns:
            GPUInfo if GPU detected, None otherwise
        """
        try:
            rows = nvidia_smi.query("name,memory.total,compute_cap")
            if not rows:
                return None

            gpu_list: List[GPUDict] = []
            for row in rows:
                try:
                    gpu_list.append({
                        "name": row["name"],
                        "vram_mb": int(row["memory.total"]),
                        "compute_cap": float(row["compute_cap"])
                    })
                except (ValueError, KeyError):
                    continue

            if not gpu_list:
                return None

            # Sort by compute capability (lowest first for display)
            gpu_list.sort(key=lambda x: x["compute_cap"])
            lowest_gpu = gpu_list[0]

            gpu_name = lowest_gpu["name"]
            vram_mb = lowest_gpu["vram_mb"]
            compute_cap = lowest_gpu["compute_cap"]  # Lowest CC (for display)
            gpu_count = len(gpu_list)
            total_vram_mb = sum(g["vram_mb"] for g in gpu_list)

            # Log multi-GPU detection
            if len(gpu_list) > 1:
                gpu_names = [f"{g['name']} ({g['compute_cap']})" for g in gpu_list]
                print(f"🎮 Multi-GPU detected: {', '.join(gpu_names)}")

            # Determine capabilities using BEST GPU (for backend eligibility)
            max_compute_cap = max(g["compute_cap"] for g in gpu_list)
            has_tensor_cores = max_compute_cap >= 7.0
            supports_fast_fp16 = any(
                self._check_fast_fp16(g["name"], g["compute_cap"]) for g in gpu_list
            )

            # Determine backend compatibility per GPU
            recommended, unsupported, warnings, backend_gpu_indices = (
                self._analyze_compatibility(gpu_list)
            )

            self.gpu_info = GPUInfo(
                name=gpu_name,
                compute_capability=compute_cap,
                vram_mb=vram_mb,
                has_tensor_cores=has_tensor_cores,
                supports_fast_fp16=supports_fast_fp16,
                recommended_backends=recommended,
                unsupported_backends=unsupported,
                warnings=warnings,
                gpu_count=gpu_count,
                total_vram_mb=total_vram_mb,
                all_gpu_names=[g["name"] for g in gpu_list],
                all_gpu_vram_mb=[g["vram_mb"] for g in gpu_list],
                all_gpu_compute_caps=[g["compute_cap"] for g in gpu_list],
                backend_gpu_indices=backend_gpu_indices,
            )

            return self.gpu_info

        except (ValueError, KeyError):
            return None

    def _check_fast_fp16(self, gpu_name: str, compute_cap: float) -> bool:
        """
        Check if GPU has fast FP16 performance

        Pascal (6.x) has very slow FP16 (1:64 ratio for later Pascal like P40)
        Volta+ (7.0+) has fast FP16
        """
        # Known slow FP16 GPUs
        if "P40" in gpu_name or "P4" in gpu_name:
            return False

        # Pascal generation (6.x) generally has slow FP16
        if 6.0 <= compute_cap < 7.0:
            # P100 is exception (6.0) - has decent FP16
            if "P100" in gpu_name:
                return True
            return False

        # Volta+ (7.0+) has fast FP16
        return compute_cap >= 7.0

    def _analyze_compatibility(
        self,
        gpu_list: List[GPUDict]
    ) -> tuple[List[str], List[str], List[str], Dict[str, List[int]]]:
        """
        Analyze backend compatibility per GPU.

        A backend is compatible if AT LEAST ONE GPU meets its requirements.

        Returns:
            (recommended_backends, unsupported_backends, warnings, backend_gpu_indices)
        """
        recommended = []
        unsupported = []
        warnings = []
        backend_gpu_indices: Dict[str, List[int]] = {}

        for backend, reqs in self.BACKEND_REQUIREMENTS.items():
            min_cap = reqs["min_compute_capability"]
            needs_fp16 = reqs["requires_fast_fp16"]

            compatible_indices = []
            for i, gpu in enumerate(gpu_list):
                cap_ok = gpu["compute_cap"] >= min_cap
                fp16_ok = not needs_fp16 or self._check_fast_fp16(gpu["name"], gpu["compute_cap"])
                if cap_ok and fp16_ok:
                    compatible_indices.append(i)

            backend_gpu_indices[backend] = compatible_indices

            if compatible_indices:
                recommended.append(backend)

                # Warn if not ALL GPUs are compatible (partial compatibility)
                if len(compatible_indices) < len(gpu_list) and len(gpu_list) > 1:
                    compatible_names = [gpu_list[i]["name"] for i in compatible_indices]
                    incompatible = [
                        f"{gpu_list[i]['name']} (CC {gpu_list[i]['compute_cap']})"
                        for i in range(len(gpu_list))
                        if i not in compatible_indices
                    ]
                    compatible_vram = sum(gpu_list[i]["vram_mb"] for i in compatible_indices)
                    compatible_vram_gb = compatible_vram // 1024
                    warnings.append(
                        f"{backend}: uses {', '.join(compatible_names)} only "
                        f"({compatible_vram_gb} GB) — "
                        f"{', '.join(incompatible)} not compatible (CC < {min_cap})"
                    )
            else:
                unsupported.append(backend)
                best_cc = max(gpu["compute_cap"] for gpu in gpu_list)
                warnings.append(
                    f"{backend}: requires compute capability {min_cap}+ "
                    f"(best GPU has {best_cc})"
                )

        # Add known issue warnings per GPU
        for gpu in gpu_list:
            for known_gpu, issue_info in self.KNOWN_ISSUES.items():
                if known_gpu in gpu["name"]:
                    warnings.append(f"{gpu['name']}: {str(issue_info['issue'])}")

        return recommended, unsupported, warnings, backend_gpu_indices

    def is_backend_compatible(self, backend: str) -> bool:
        """
        Check if a specific backend is compatible

        Args:
            backend: "ollama", "vllm"

        Returns:
            True if compatible, False otherwise
        """
        if not self.gpu_info:
            self.detect()

        if not self.gpu_info:
            # No GPU detected or detection failed - allow all backends
            return True

        return backend.lower() in self.gpu_info.recommended_backends

    def get_compatibility_message(self, backend: str) -> Optional[str]:
        """
        Get user-friendly compatibility message for a backend

        Args:
            backend: "ollama", "vllm"

        Returns:
            Warning message if incompatible, None if compatible
        """
        if not self.gpu_info:
            self.detect()

        if not self.gpu_info:
            return None

        backend = backend.lower()

        if backend in self.gpu_info.unsupported_backends:
            # Find relevant warning
            for warning in self.gpu_info.warnings:
                if warning.startswith(f"{backend}:"):
                    return warning

            # Generic warning
            reqs: BackendRequirement | None = self.BACKEND_REQUIREMENTS.get(backend)
            desc = reqs.get('description', '') if reqs else ''
            return (
                f"⚠️ {backend} may not work properly on {self.gpu_info.name}\n"
                f"   (Compute Capability: {self.gpu_info.compute_capability})\n"
                f"   {desc}"
            )

        return None


# Global instance
_detector = GPUDetector()


def detect_gpu() -> Optional[GPUInfo]:
    """Convenience function to detect GPU"""
    return _detector.detect()


def is_backend_compatible(backend: str) -> bool:
    """Convenience function to check backend compatibility"""
    return _detector.is_backend_compatible(backend)


def get_compatibility_message(backend: str) -> Optional[str]:
    """Convenience function to get compatibility message"""
    return _detector.get_compatibility_message(backend)
