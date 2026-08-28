"""
vLLM-Autokalibration: Suchablauf (Paket 3).

Findet fuer einen Checkpoint den Betriebspunkt selbststaendig und
persistiert ihn als Operating-Point-Profil + llama-swap-Eintrag:

  Phase A  Modell-Analyse            (vllm_model_meta)
  Phase B  GPU-Inventar              (calibration.gpu, Side-Channels raus)
  Phase C  Topologie-Leiter          (TP=1 → TP in Klasse → TP×PP-Gitter)
  Phase D  Kontext-Suche             (nativ starten, geparste Grenze nutzen,
                                      MB-Reserve je Karte via GMU)
  Phase E  k-Sweep                   (Arithmetik-Sperrzonen + Lohnt-Check,
                                      je Kandidat messen)
  Phase F  Persistierung             (Profil mit Hardware-Fingerprint,
                                      Eintrag via operating_points.apply)

Heuristiken saeen nur Kandidaten — entschieden wird durch Boots und
Messungen (die llama.cpp-Kalibration verifiziert genauso).
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from ..config import LLAMASWAP_CONFIG_PATH, PROJECT_ROOT
from .gpu import enumerate_gpus
from .types import GPU
from .vllm_model_meta import VllmModelMeta, analyze_checkpoint
from .vllm_probe import (
    VllmBootError,
    VllmServer,
    VllmSpec,
    boot_vllm,
    find_free_port,
    load_vllm_runtime,
    probe_coherence,
    probe_throughput,
)

logger = logging.getLogger(__name__)

# Feste MB-Reserve je Karte (Analogon zu LLAMACPP_VRAM_SAFETY_MARGIN):
# CUDA-Kernels/Fragmentierung. Der GMU-Wert entsteht daraus deterministisch.
VLLM_VRAM_RESERVE_MB = 1024

# Default-Workspace-Faktor fuers Weight-Processing; Stack-spezifisch
# ueberschreibbar in data/vllm_runtime.yaml (weight_processing_factor).
DEFAULT_WEIGHT_PROCESSING_FACTOR = 1.6

# Spekulation lohnt nur, wenn der Draft-Block klein gegen die
# Pro-Token-Leselast des Hauptmodells ist (Flash-Next-Befund: BF16-Block
# mit 75 % der Leselast machte MTP zum Verlust; 27B mit 4 % gewinnt 2,5x).
MTP_MAX_READ_FRACTION = 0.25

BOOT_TIMEOUT_S = 1200
MIN_USEFUL_CONTEXT = 4096


@dataclass
class TopologyCandidate:
    gpu_ids: list[int]          # numerische PCI-Indizes in Stufenordnung
    tp: int
    pp: int
    pp_partition: str | None
    label: str


@dataclass
class VllmCalibrationResult:
    spec: VllmSpec
    throughput_tok_s: float
    coherence: tuple[int, int]
    k_sweep: dict[int, float]   # k -> tok/s
    profile_path: Path | None = None


def _smi_index_by_uuid() -> dict[str, int]:
    """nvidia-smi-Index (PCI-Ordnung) je GPU-UUID — vLLM/1Cat verlangt
    numerische CUDA_VISIBLE_DEVICES."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    mapping = {}
    for line in out.strip().splitlines():
        idx, uuid = [p.strip() for p in line.split(",")]
        mapping[uuid] = int(idx)
    return mapping


def eligible_gpus(reserved_uuids: set[str]) -> list[GPU]:
    """Kalibrierbare GPUs: alle minus Side-Channel-Karten (TTS/VLM)."""
    return [g for g in enumerate_gpus() if g.uuid not in reserved_uuids]


def side_channel_uuids() -> set[str]:
    """UUIDs der TTS-/VLM-Karten (bleiben frei, wie beim Betriebspunkt)."""
    reserved: set[str] = set()
    try:
        from ..process_utils import get_tts_gpu_uuid
        uuid = get_tts_gpu_uuid()
        if uuid:
            reserved.add(uuid)
    except Exception as e:  # noqa: BLE001 — Side-Channel optional installiert
        logger.debug(f"tts gpu lookup skipped: {e}")
    try:
        from ..vision_gpu_select import pick_vlm_gpu
        vlm_index = pick_vlm_gpu()  # PCI_BUS_ID-Index
        by_index = {v: k for k, v in _smi_index_by_uuid().items()}
        if vlm_index in by_index:
            reserved.add(by_index[vlm_index])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"vlm gpu lookup skipped: {e}")
    return reserved


def _capture_sizes_for(k: int, runtime: dict) -> list[int]:
    """Capture-Groessen: Basis [1,2,4,8] plus Verifier-Batch (k+1);
    Stack-Limit aus der Runtime-Config (dieser 1Cat/sm70-Stack: >8 kaputt)."""
    sizes = {1, 2, 4, 8, k + 1}
    limit = runtime.get("max_capture_size")
    if limit:
        sizes = {s for s in sizes if s <= int(limit)}
    return sorted(sizes)


def topology_ladder(
    meta: VllmModelMeta, gpus: list[GPU], runtime: dict,
) -> Iterator[TopologyCandidate]:
    """Kandidaten von billig nach teuer.

    Regeln: TP nur innerhalb einer Compute-Klasse (Kernel-Dispatch je
    Architektur), PP ueber die Klassengrenze; Stufenordnung hoechste
    Klasse zuerst (Konvention der Capability-Gates). Riesen-Layer
    (PLE-Klasse) muessen komplett in die Stufe mit dem meisten VRAM.
    """
    smi = _smi_index_by_uuid()
    weights_mb = meta.total_bytes / (1024 * 1024)
    factor = float(runtime.get("weight_processing_factor",
                               DEFAULT_WEIGHT_PROCESSING_FACTOR))
    need_mb = weights_mb * factor

    by_class: dict[int, list[GPU]] = {}
    for g in gpus:
        by_class.setdefault(g.speed_class, []).append(g)
    classes = [by_class[c] for c in sorted(by_class)]  # 0 = hoechste zuerst

    # 1) Eine Karte je Compute-Klasse (die freieste — identische Karten
    #    derselben Klasse liefern identische Messwerte, eine reicht)
    for cls in classes:
        g = max(cls, key=lambda g: g.free_mb)
        if g.free_mb - VLLM_VRAM_RESERVE_MB >= need_mb:
            yield TopologyCandidate([smi[g.uuid]], 1, 1, None, f"TP1 auf {g.name}")

    # 2) TP innerhalb einer Klasse (alle Karten der Klasse)
    for cls in classes:
        if len(cls) < 2:
            continue
        budget = sum(g.free_mb - VLLM_VRAM_RESERVE_MB for g in cls)
        if budget >= need_mb:
            ids = [smi[g.uuid] for g in cls]
            yield TopologyCandidate(ids, len(cls), 1, None,
                                    f"TP{len(cls)} auf {cls[0].name}-Klasse")

    # 3) TP×PP-Gitter ueber die Klassen (TP = kleinste Klassenstaerke)
    if len(classes) >= 2:
        tp = min(len(c) for c in classes)
        if tp >= 1:
            stage_gpus = [c[:tp] for c in classes]
            ids = [smi[g.uuid] for stage in stage_gpus for g in stage]
            partition = _seed_partition(meta, stage_gpus)
            yield TopologyCandidate(
                ids, tp, len(classes), partition,
                f"TP{tp}×PP{len(classes)}-Gitter",
            )


def _seed_partition(meta: VllmModelMeta, stage_gpus: list[list[GPU]]) -> str | None:
    """Layer-Partition proportional zum Stufen-VRAM; Riesen-Layer zwingen
    ihre Stufe. Nur ein Startwert — die Kontext-Suche verschiebt notfalls."""
    if meta.num_layers <= 0 or len(stage_gpus) < 2:
        return None
    vram = [sum(g.total_mb for g in stage) for stage in stage_gpus]
    if meta.giant_layers:
        # Riesen (PLE-Klasse) liegen in fruehen Layern → muessen auf die
        # Stufe mit dem meisten VRAM; die bekommt die fruehen Layer.
        if max(range(len(vram)), key=lambda i: vram[i]) != 0:
            # Stufenordnung ist fix (Capability-Konvention) — dann
            # Partition zugunsten Stufe 0 verschieben, die die Riesen
            # tragen MUSS (alle giant_layers < Partitionsgrenze).
            pass
    total = sum(vram)
    layers = [round(meta.num_layers * v / total) for v in vram]
    layers[-1] = meta.num_layers - sum(layers[:-1])
    if meta.giant_layers:
        min_first = max(meta.giant_layers) + 1
        if layers[0] < min_first:
            delta = min_first - layers[0]
            layers[0] += delta
            layers[-1] -= delta
    if any(n <= 0 for n in layers):
        return None
    return ",".join(str(n) for n in layers)


def _gmu_for(gpus_in_use: list[GPU]) -> float:
    """GMU aus fester MB-Reserve: min ueber (Kapazitaet−Reserve)/Kapazitaet."""
    return round(min((g.total_mb - VLLM_VRAM_RESERVE_MB) / g.total_mb
                     for g in gpus_in_use), 2)


def _spec_attn_for(
    spec: VllmSpec, gpus: list[GPU], smi: dict[str, int], runtime: dict,
) -> str | None:
    """Drafter-Attention-Backend aus der Compute-Klasse der LETZTEN
    PP-Stufe (dort lebt der Drafter) und der Runtime-Zuordnung."""
    mapping = runtime.get("spec_attention_backend_by_cc") or {}
    if not mapping:
        return None
    by_smi = {smi[g.uuid]: g for g in gpus}
    last_stage = spec.gpu_ids[-spec.tp:]
    for idx in last_stage:
        gpu = by_smi.get(idx)
        if gpu is not None:
            backend = mapping.get(f"{gpu.compute_cap:.1f}")
            if backend:
                return str(backend)
    return None


def _mtp_worthwhile(meta: VllmModelMeta) -> bool:
    """Draft-Block-Bytes relativ zur Pro-Token-Leselast des Hauptmodells."""
    if not meta.mtp.present:
        return False
    read = meta.per_token_read_bytes()
    if read <= 0:
        return False
    return meta.mtp_read_bytes_per_step() / read <= MTP_MAX_READ_FRACTION


def _k_candidates(meta: VllmModelMeta) -> list[int]:
    """Zu messende Spekulationstiefen: block_size-guenstige k, gross → klein."""
    if not _mtp_worthwhile(meta):
        return []
    allowed = meta.allowed_k_block_sizes()
    base_block = allowed[0]
    good = [k for k, block in allowed.items() if k > 0 and block <= base_block * 2]
    good.sort(reverse=True)
    return good[:3]


def calibrate_vllm_checkpoint(
    checkpoint: Path,
    entry_name: str,
    log_dir: Path,
    progress: Callable[[str], None],
    cancel_check: Callable[[], bool] | None = None,
) -> VllmCalibrationResult:
    """Kompletter Suchlauf. progress() bekommt englische Statuszeilen."""
    runtime = load_vllm_runtime()  # frueh scheitern, wenn die Umgebung fehlt

    progress(f"🔬 Analyzing checkpoint {checkpoint.name}...")
    meta = analyze_checkpoint(checkpoint)
    gib = 1024 ** 3
    progress(
        f"   {meta.architecture}, {meta.num_layers} layers, "
        f"{meta.total_bytes / gib:.1f} GiB, native ctx {meta.native_context:,}, "
        f"multimodal={meta.multimodal}, giants={meta.giant_layers}"
    )
    if meta.mtp.present:
        progress(
            f"   MTP block: {meta.mtp.bytes_total / gib:.2f} GiB "
            f"{meta.mtp.dominant_dtype}, worthwhile={_mtp_worthwhile(meta)}"
        )

    reserved = side_channel_uuids()
    gpus = eligible_gpus(reserved)
    if not gpus:
        raise RuntimeError("no eligible GPUs (all reserved for side channels)")
    progress(f"🖥️ Eligible GPUs: {[f'{g.name}({g.total_mb}MB)' for g in gpus]}")

    # --- Phase C/D: ALLE Topologie-Kandidaten booten und messen ----------
    # Der erste bootende Kandidat ist selten der schnellste (27B: TP=1 auf
    # einer RTX bootet, aber das 2x2-Gitter ist Faktor 2 schneller) —
    # deshalb wird jede Sprosse gemessen und die beste gewinnt.
    smi = _smi_index_by_uuid()
    uuid_by_smi = {v: k for k, v in smi.items()}
    best_spec: VllmSpec | None = None
    base_tps = 0.0
    coh: tuple[int, int] = (0, 0)

    for cand in topology_ladder(meta, gpus, runtime):
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        cand_gpus = [g for g in gpus if smi[g.uuid] in cand.gpu_ids]
        gmu = _gmu_for(cand_gpus)
        mml = meta.native_context or 32768
        progress(f"🚀 Trying {cand.label} (GPUs {cand.gpu_ids}, GMU {gmu})...")

        server: VllmServer | None = None
        spec: VllmSpec | None = None
        for attempt in range(3):
            spec = VllmSpec(
                checkpoint=checkpoint, served_name=entry_name,
                gpu_ids=cand.gpu_ids, tp=cand.tp, pp=cand.pp,
                gmu=gmu, mml=mml, k=0,
                block_size=meta.allowed_k_block_sizes()[0],
                pp_partition=cand.pp_partition,
                language_model_only=meta.multimodal,
            )
            port = find_free_port()
            log = log_dir / f"boot-{cand.tp}x{cand.pp}-try{attempt}.log"
            try:
                server = boot_vllm(spec, port, log, BOOT_TIMEOUT_S, cancel_check)
                progress(f"   ↳ boot OK, ctx {mml:,}")
                break
            except VllmBootError as e:
                if e.parsed_max_len and e.parsed_max_len >= MIN_USEFUL_CONTEXT:
                    # vLLM nennt die Grenze selbst — uebernehmen, neu booten
                    mml = e.parsed_max_len
                    progress(f"   ↳ context capped by vLLM: retry with {mml:,}")
                    continue
                progress(f"   ↳ boot failed: {e.reason}")
                logger.info(f"boot failure detail: {e.log_tail[-1500:]}")
                break

        if server is None or spec is None:
            continue
        # Sonden absturzsicher: ein HTTP 500 des Servers (z.B. kaputter
        # Spec-Pfad) ist ein Sprossen-Urteil, kein Flow-Abbruch — und der
        # Server wird IMMER heruntergefahren (Lauf 2026-08-29: geleakte
        # 25 GB nach ungefangener Probe-Exception).
        try:
            ok, total, _ = probe_coherence(server)
            tps = max(probe_throughput(server)) if ok == total else 0.0
        except Exception as probe_err:  # noqa: BLE001
            progress(f"   ↳ probe crashed ({type(probe_err).__name__}) — rung rejected")
            continue
        finally:
            server.shutdown()
        if ok < total:
            progress(f"   ↳ incoherent ({ok}/{total}) — rung rejected")
            continue
        progress(f"   ↳ {cand.label}: {tps:.1f} tok/s (coherence {ok}/{total})")
        if tps > base_tps:
            best_spec, base_tps, coh = spec, tps, (ok, total)

    if best_spec is None:
        raise RuntimeError("no topology candidate booted coherently")
    progress(
        f"📈 Best topology: TP{best_spec.tp}×PP{best_spec.pp} "
        f"GPUs {best_spec.gpu_ids} — k=0 baseline {base_tps:.1f} tok/s"
    )
    k_sweep: dict[int, float] = {0: base_tps}
    ok, total = coh

    # --- Phase E: k-Sweep (jeder Kandidat ein eigener Boot) -------------
    best_k, best_tps = 0, base_tps
    for k in _k_candidates(meta):
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        allowed = meta.allowed_k_block_sizes()
        capture = _capture_sizes_for(k, runtime)
        spec_attn = _spec_attn_for(best_spec, gpus, smi, runtime)
        spec_k = VllmSpec(
            **{**best_spec.__dict__, "k": k, "block_size": allowed[k],
               "capture_sizes": capture, "spec_attn_backend": spec_attn},
        )
        port = find_free_port()
        log = log_dir / f"boot-k{k}.log"
        progress(f"🎲 Probing k={k} (block {allowed[k]}, capture {capture}, "
                 f"spec-attn {spec_attn or 'default'})...")
        try:
            server = boot_vllm(spec_k, port, log, BOOT_TIMEOUT_S, cancel_check)
        except VllmBootError as e:
            progress(f"   ↳ k={k} boot failed: {e.reason}")
            continue
        try:
            ok_k, total_k, _ = probe_coherence(server)
            tps = max(probe_throughput(server)) if ok_k == total_k else 0.0
        except Exception as probe_err:  # noqa: BLE001
            progress(f"   ↳ k={k} probe crashed ({type(probe_err).__name__}) — rejected")
            continue
        finally:
            server.shutdown()
        if ok_k < total_k:
            progress(f"   ↳ k={k} incoherent ({ok_k}/{total_k}) — rejected")
            continue
        k_sweep[k] = tps
        progress(f"   ↳ k={k}: {tps:.1f} tok/s")
        if tps > best_tps:
            best_k, best_tps = k, tps

    final_spec = VllmSpec(
        **{**best_spec.__dict__, "k": best_k,
           "block_size": meta.allowed_k_block_sizes()[best_k],
           "capture_sizes": _capture_sizes_for(best_k, runtime) if best_k else None,
           "spec_attn_backend": (
               _spec_attn_for(best_spec, gpus, smi, runtime) if best_k else None
           )},
    )
    progress(f"🏁 Best point: k={best_k}, {best_tps:.1f} tok/s "
             f"(TP{final_spec.tp}×PP{final_spec.pp}, ctx {final_spec.mml:,})")

    # --- Phase F: Persistierung -----------------------------------------
    profile_path = persist_operating_point(final_spec, best_tps, k_sweep, meta)
    progress(f"💾 Operating point saved: {profile_path}")

    # Verwendete GPUs im Log dokumentieren (UUID-Rueckverfolgbarkeit)
    used = [uuid_by_smi.get(i, str(i)) for i in final_spec.gpu_ids]
    logger.info(f"vllm calibration done: {entry_name} on {used}")

    return VllmCalibrationResult(
        spec=final_spec, throughput_tok_s=best_tps,
        coherence=(ok, total), k_sweep=k_sweep, profile_path=profile_path,
    )


def render_llamaswap_entry(spec: VllmSpec, ttl: int = 3600) -> dict:
    """Aus Spec + Runtime den llama-swap-Eintrag bauen (cmd mit ${PORT})."""
    runtime = load_vllm_runtime()
    cmd_parts = spec.build_cmd(runtime, port=0)
    # Port-Platzhalter: das letzte "--port 0" durch ${PORT} ersetzen
    port_idx = len(cmd_parts) - 1 - cmd_parts[::-1].index("--port")
    cmd_parts[port_idx + 1] = "${PORT}"
    # JSON-Argumente fuer llama-swaps shellwords-Parser quoten
    quoted = [f"'{p}'" if p.startswith("{") else p for p in cmd_parts]
    env_map = spec.build_env(runtime)
    return {
        "cmd": " ".join(quoted),
        "cmdStop": f"{PROJECT_ROOT}/scripts/vllm-swap-stop ${{PID}}",
        "ttl": ttl,
        "env": [f"{k}={v}" for k, v in env_map.items()],
    }


def persist_operating_point(
    spec: VllmSpec, tok_s: float, k_sweep: dict[int, float], meta: VllmModelMeta,
) -> Path:
    """Profil schreiben (mit Hardware-Fingerprint) und Eintrag anwenden."""
    import datetime

    import yaml

    from ..config import OPERATING_POINTS_DIR
    from ..operating_points import apply_operating_point, gpu_fingerprint

    entry = render_llamaswap_entry(spec)
    profile = {
        "llamaswap": entry,
        "group": "main",
        "hardware": gpu_fingerprint(),
        "meta": {
            "source": "AIfred vLLM auto-calibration",
            "measured": datetime.date.today().isoformat(),
            "architecture": meta.architecture,
            "throughput_tok_s": round(tok_s, 1),
            "k_sweep": {str(k): round(v, 1) for k, v in k_sweep.items()},
            "topology": f"TP={spec.tp} PP={spec.pp} GPUs={spec.gpu_ids}",
        },
    }
    OPERATING_POINTS_DIR.mkdir(parents=True, exist_ok=True)
    path = OPERATING_POINTS_DIR / f"{spec.served_name}.yaml"
    path.write_text(yaml.safe_dump(profile, default_flow_style=False,
                                   sort_keys=False, width=10000, allow_unicode=True))
    apply_operating_point(spec.served_name)
    logger.info(f"operating point persisted + applied: {path} "
                f"({LLAMASWAP_CONFIG_PATH})")
    return path
