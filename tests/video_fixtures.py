"""Kleine echte Testvideos (ffmpeg) für die Tests von video_split und der Telegram-Zustellung."""

from __future__ import annotations

import subprocess
from pathlib import Path


def make_video(path: Path, seconds: int, keyframe_every: int, bitrate: str = "400k") -> Path:
    """Rauschen (Größe steuerbar), 10 Bilder/s, B-Bilder an, ein Schlüsselbild alle ``keyframe_every`` Bilder."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "nullsrc=s=320x240:r=10,geq=random(1)*255:128:128", "-t", str(seconds),
            "-c:v", "libx264", "-g", str(keyframe_every), "-keyint_min", str(keyframe_every),
            "-sc_threshold", "0", "-bf", "2", "-b:v", bitrate, str(path),
        ],
        check=True,
    )
    return path
