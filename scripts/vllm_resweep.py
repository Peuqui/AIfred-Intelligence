#!/usr/bin/env python3
"""vLLM-Nachmessmodus: k-Sweep auf den Topologien des Betriebspunkts.

Wie der Kalibrieren-Knopf, aber ohne Topologie-Leiter: die Sprosse(n) des
persistierten Betriebspunkts (oder die per --topology genannten Leiter-Labels)
werden mit k=0 als Referenz gebootet, der k-Sweep laeuft mit Produktions-
Sampling, alle Messungen landen in measurement-matrix.json, dieselbe
Gesamtzeit-Regel entscheidet. --dry-run persistiert nichts.

    venv/bin/python scripts/vllm_resweep.py Qwen3.8-27B-NVFP4-vllm --dry-run
    venv/bin/python scripts/vllm_resweep.py Qwen3.8-27B-NVFP4-vllm \\
        --topology "TP2 across RTX 8000 class" --topology "TP2 across V100 class"

Stoppt llama-swap fuer die Dauer der Messung und startet es danach wieder.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aifred.lib.calibration.vllm_flow import calibrate_vllm_checkpoint  # noqa: E402
from aifred.lib.config import DATA_DIR  # noqa: E402
from aifred.lib.operating_points import operating_point_checkpoint  # noqa: E402
from aifred.lib.process_utils import start_llama_swap, stop_llama_swap  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("entry", help="llama-swap entry name, e.g. Qwen3.8-27B-NVFP4-vllm")
    parser.add_argument("--topology", action="append", default=None,
                        help="ladder label to (re)measure; repeatable; default: the operating point's topology")
    parser.add_argument("--dry-run", action="store_true", help="measure and write the matrix, persist nothing")
    parser.add_argument("--no-side-channel", action="store_true",
                        help="do not reserve the side-channel pair (TTS/VLM card may be used)")
    parser.add_argument("--log", type=Path, default=None,
                        help="progress log file (default: data/logs/vllm_calibration/<entry>/resweep-<date>.log)")
    args = parser.parse_args()

    log_dir = DATA_DIR / "logs" / "vllm_calibration" / args.entry
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.log or log_dir / f"resweep-{time.strftime('%Y%m%d-%H%M')}.log"

    def progress(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} | {msg}"
        print(line, flush=True)
        with log_path.open("a") as f:
            f.write(line + "\n")

    checkpoint = operating_point_checkpoint(args.entry)

    stopped = False
    try:
        stopped = stop_llama_swap()
        progress("🧹 llama-swap stopped" if stopped else "llama-swap was not running")
        result = calibrate_vllm_checkpoint(
            checkpoint=checkpoint, entry_name=args.entry, log_dir=log_dir,
            progress=progress, cancel_check=lambda: False,
            reserve_side_channel=not args.no_side_channel,
            topologies=args.topology, resweep=args.topology is None,
            dry_run=args.dry_run,
        )
        progress(f"✅ re-sweep complete: {result.throughput_tok_s:.1f} tok/s "
                 f"(TP{result.spec.tp}xPP{result.spec.pp}, k={result.spec.k}, ctx {result.spec.mml}); "
                 f"k-sweep {sorted(result.k_sweep.items())}; "
                 f"profile {result.profile_path or 'not persisted (dry run)'}")
        return 0
    except Exception as e:  # noqa: BLE001 — Laufende Messung, Fehler ins Log und nach oben
        progress(f"❌ re-sweep failed: {type(e).__name__}: {e}")
        raise
    finally:
        if stopped:
            progress("🔄 llama-swap restarted" if start_llama_swap() else "⚠️ llama-swap restart failed")


if __name__ == "__main__":
    sys.exit(main())
