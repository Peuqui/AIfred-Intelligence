"""
GPU Detection and Compatibility Checking

Detects GPU compute capability and provides backend compatibility info.
Prevents users from trying to use incompatible backends with their GPU.
"""

import subprocess
from typing import Optional, List
from dataclasses import dataclass


@dataclass
class GPUInfo:
    """GPU information and capabilities"""
    name: str
    compute_capability: float
    vram_mb: int
    has_tensor_cores: bool
    supports_fast_fp16: bool
    recommended_backends: List[str]
    unsupported_backends: List[str]
    warnings: List[str]


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
    BACKEND_REQUIREMENTS = {
        "ollama": {
            "min_compute_capability": 3.5,
            "requires_tensor_cores": False,
            "requires_fast_fp16": False,
            "description": "GGUF models (INT8/Q4/Q8), universal compatibility"
        },
        "vllm": {
            "min_compute_capability": 7.0,
            "requires_tensor_cores": False,
            "requires_fast_fp16": True,
            "description": "AWQ/GPTQ models, requires Volta+ (7.0+)"
        },
        "tabbyapi": {
            "min_compute_capability": 7.0,
            "requires_tensor_cores": False,
            "requires_fast_fp16": True,
            "description": "ExLlamaV2/V3 (EXL2), requires fast FP16"
        }
    }

    # Known problematic GPUs
    KNOWN_ISSUES = {
        "Tesla P40": {
            "fp16_ratio": "1:64",
            "issue": "Extremely slow FP16 performance",
            "recommendation": "Use Ollama (GGUF) only"
        },
        "Tesla P100": {
            "fp16_ratio": "1:2",
            "issue": "Moderate FP16 performance",
            "recommendation": "Ollama recommended, vLLM possible but slower"
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

        Returns:
            GPUInfo if GPU detected, None otherwise
        """
        try:
            # Try nvidia-smi first
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,compute_cap",
                 "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return None

            # Parse output: "NVIDIA Tesla P40, 22919, 6.1"
            line = result.stdout.strip()
            if not line:
                return None

            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                return None

            gpu_name = parts[0]
            vram_mb = int(parts[1])
            compute_cap = float(parts[2])

            # Determine capabilities
            has_tensor_cores = compute_cap >= 7.0
            supports_fast_fp16 = self._check_fast_fp16(gpu_name, compute_cap)

            # Determine backend compatibility
            recommended, unsupported, warnings = self._analyze_compatibility(
                gpu_name, compute_cap, has_tensor_cores, supports_fast_fp16
            )

            self.gpu_info = GPUInfo(
                name=gpu_name,
                compute_capability=compute_cap,
                vram_mb=vram_mb,
                has_tensor_cores=has_tensor_cores,
                supports_fast_fp16=supports_fast_fp16,
                recommended_backends=recommended,
                unsupported_backends=unsupported,
                warnings=warnings
            )

            return self.gpu_info

        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
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
        gpu_name: str,
        compute_cap: float,
        has_tensor_cores: bool,
        supports_fast_fp16: bool
    ) -> tuple[List[str], List[str], List[str]]:
        """
        Analyze which backends are compatible

        Returns:
            (recommended_backends, unsupported_backends, warnings)
        """
        recommended = []
        unsupported = []
        warnings = []

        # Check each backend
        for backend, reqs in self.BACKEND_REQUIREMENTS.items():
            min_cap = reqs["min_compute_capability"]
            needs_tc = reqs["requires_tensor_cores"]
            needs_fp16 = reqs["requires_fast_fp16"]

            # Check requirements
            cap_ok = compute_cap >= min_cap
            tc_ok = not needs_tc or has_tensor_cores
            fp16_ok = not needs_fp16 or supports_fast_fp16

            if cap_ok and tc_ok and fp16_ok:
                recommended.append(backend)
            else:
                unsupported.append(backend)

                # Generate specific warning
                reasons = []
                if not cap_ok:
                    reasons.append(f"requires compute capability {min_cap}+ (you have {compute_cap})")
                if not tc_ok:
                    reasons.append("requires Tensor Cores")
                if not fp16_ok:
                    reasons.append("requires fast FP16 (your GPU has slow FP16)")

                warning = f"{backend}: " + ", ".join(reasons)
                warnings.append(warning)

        # Add known issue warnings
        for known_gpu, issue_info in self.KNOWN_ISSUES.items():
            if known_gpu in gpu_name:
                warnings.append(f"{gpu_name}: {issue_info['issue']}")
                warnings.append(f"Recommendation: {issue_info['recommendation']}")

        return recommended, unsupported, warnings

    def is_backend_compatible(self, backend: str) -> bool:
        """
        Check if a specific backend is compatible

        Args:
            backend: "ollama", "vllm", "tabbyapi"

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
            backend: "ollama", "vllm", "tabbyapi"

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
            reqs = self.BACKEND_REQUIREMENTS.get(backend, {})
            return (
                f"⚠️ {backend} may not work properly on {self.gpu_info.name}\n"
                f"   (Compute Capability: {self.gpu_info.compute_capability})\n"
                f"   {reqs.get('description', '')}"
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
