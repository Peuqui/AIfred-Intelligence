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
from ..formatting import format_number
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
# OOM-Retry: Inductor/Compile braucht Workspace auf der per GMU absichtlich
# vollen Karte (vLLM fuellt den KV-Pool IMMER bis zum Budget, unabhaengig vom
# Kontext). Erst die Reserve erhoehen (kostet Pool-Bloecke, KEIN Kontext-
# Token — Kontext-Vorrang), erst danach den Kontext halbieren.
OOM_RESERVE_STEP_MB = 1024
OOM_MAX_RESERVE_STEPS = 2

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
    # Speed-Kandidat (schnellste kontext-reduzierte Topologie, informativ)
    speed_label: str = ""
    speed_k: int = 0
    speed_tps: float = 0.0
    speed_mml: int = 0


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
            yield TopologyCandidate([smi[g.uuid]], 1, 1, None, f"TP1 on {g.name}")

    # 2) TP innerhalb einer Klasse (alle Karten der Klasse)
    for cls in classes:
        if len(cls) < 2:
            continue
        budget = sum(g.free_mb - VLLM_VRAM_RESERVE_MB for g in cls)
        if budget >= need_mb:
            ids = [smi[g.uuid] for g in cls]
            yield TopologyCandidate(ids, len(cls), 1, None,
                                    f"TP{len(cls)} across {cls[0].name} class")

    # 3) TP×PP-Gitter ueber die Klassen (TP = kleinste Klassenstaerke)
    if len(classes) >= 2:
        tp = min(len(c) for c in classes)
        if tp >= 1:
            stage_gpus = [c[:tp] for c in classes]
            ids = [smi[g.uuid] for stage in stage_gpus for g in stage]
            partition = _seed_partition(meta, stage_gpus)
            yield TopologyCandidate(
                ids, tp, len(classes), partition,
                f"TP{tp}×PP{len(classes)} grid",
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


def _gmu_for(gpus_in_use: list[GPU], reserve_mb: int = VLLM_VRAM_RESERVE_MB) -> float:
    """GMU aus fester MB-Reserve: min ueber (Kapazitaet−Reserve)/Kapazitaet."""
    return round(min((g.total_mb - reserve_mb) / g.total_mb
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


def _k_candidates(meta: VllmModelMeta, runtime: dict) -> list[int]:
    """Zu messende Spekulationstiefen: block_size-guenstige k, gross → klein.

    Strukturell unmoegliche k werden gar nicht erst gebootet: vLLM rundet
    Capture-Groessen auf Vielfache von k+1 — liegt k+1 ueber dem
    Stack-Limit (max_capture_size, dieser 1Cat/sm70-Stack: 8), existiert
    keine gueltige Groesse und der Boot stirbt IMMER (dreifach belegt,
    Lauf 5 2026-08-29). Spart ~2 min Boot je ausgeschlossenem k.
    """
    if not _mtp_worthwhile(meta):
        return []
    allowed = meta.allowed_k_block_sizes()
    base_block = allowed[0]
    good = [k for k, block in allowed.items() if k > 0 and block <= base_block * 2]
    limit = runtime.get("max_capture_size")
    if limit:
        good = [k for k in good if k + 1 <= int(limit)]
    good.sort(reverse=True)
    return good[:3]


def _display_checkpoint_name(checkpoint: Path) -> str:
    """HF-Cache-Snapshots heissen nach der Commit-ID — fuer Menschen den
    Repo-Namen zeigen (…/models--Org--Name/snapshots/<hash>)."""
    if checkpoint.parent.name == "snapshots":
        repo = checkpoint.parent.parent.name
        if repo.startswith("models--"):
            return repo.removeprefix("models--").replace("--", "/", 1)
    return checkpoint.name


@dataclass
class _RungResult:
    """Messergebnis einer Topologie-Sprosse (k=0)."""
    spec: VllmSpec
    label: str
    tps: float
    coherence: tuple[int, int]
    full_context: bool          # traegt den vollen nativen Kontext


def _measure_topology(
    cand: TopologyCandidate,
    entry_name: str,
    meta: VllmModelMeta,
    gpus: list[GPU],
    smi: dict[str, int],
    log_dir: Path,
    progress: Callable[[str], None],
    cancel_check: Callable[[], bool] | None,
) -> _RungResult | None:
    """Eine Sprosse booten und messen; None = Sprosse nicht nutzbar.

    Kontext-Strategie: Start mit nativem Kontext. Nennt vLLM selbst eine
    Grenze, wird sie uebernommen; ein nacktes OOM halbiert den Kontext.
    So tritt jede Sprosse mit ihrem maximal tragbaren Kontext an.
    """
    cand_gpus = [g for g in gpus if smi[g.uuid] in cand.gpu_ids]
    reserve_mb = VLLM_VRAM_RESERVE_MB
    native = meta.native_context or 32768
    mml = native
    progress(f"🚀 Trying {cand.label} (GPUs {cand.gpu_ids}, GMU {_gmu_for(cand_gpus)})...")

    server: VllmServer | None = None
    spec: VllmSpec | None = None
    max_attempts = 6
    for attempt in range(max_attempts):
        spec = VllmSpec(
            checkpoint=meta.checkpoint, served_name=entry_name,
            gpu_ids=cand.gpu_ids, tp=cand.tp, pp=cand.pp,
            gmu=_gmu_for(cand_gpus, reserve_mb), mml=mml, k=0,
            block_size=meta.allowed_k_block_sizes()[0],
            pp_partition=cand.pp_partition,
            language_model_only=meta.multimodal,
        )
        port = find_free_port()
        log = log_dir / f"boot-{cand.tp}x{cand.pp}-try{attempt}.log"
        try:
            server = boot_vllm(spec, port, log, BOOT_TIMEOUT_S, cancel_check)
            progress(f"   ↳ boot OK, ctx {format_number(mml)}")
            break
        except VllmBootError as e:
            retries_left = attempt < max_attempts - 1
            if (retries_left and e.parsed_max_len
                    and MIN_USEFUL_CONTEXT <= e.parsed_max_len < mml):
                # vLLM nennt die Grenze selbst — uebernehmen, neu booten
                mml = e.parsed_max_len
                progress(f"   ↳ context capped by vLLM: retry with {format_number(mml)}")
                continue
            if retries_left and e.oom:
                # Stufe 1+2: Reserve erhoehen — der Compile-Workspace braucht
                # Luft auf der per GMU vollen Karte; kostet nur Pool-Bloecke,
                # der Kontext bleibt unangetastet (Kontext-Vorrang).
                if reserve_mb < VLLM_VRAM_RESERVE_MB + OOM_MAX_RESERVE_STEPS * OOM_RESERVE_STEP_MB:
                    reserve_mb += OOM_RESERVE_STEP_MB
                    progress(
                        f"   ↳ OOM: raising per-GPU reserve to "
                        f"{format_number(reserve_mb)} MB "
                        f"(GMU {_gmu_for(cand_gpus, reserve_mb)}), ctx kept"
                    )
                    continue
                # Stufe 3+: erst jetzt den Kontext halbieren
                if mml // 2 >= MIN_USEFUL_CONTEXT:
                    mml //= 2
                    progress(f"   ↳ OOM persists: retry with ctx {format_number(mml)}")
                    continue
            progress(f"   ↳ boot failed: {e.reason}")
            logger.info(f"boot failure detail: {e.log_tail[-1500:]}")
            break

    if server is None or spec is None:
        return None
    # Sonden absturzsicher: ein HTTP 500 des Servers (z.B. kaputter
    # Spec-Pfad) ist ein Sprossen-Urteil, kein Flow-Abbruch — und der
    # Server wird IMMER heruntergefahren (Lauf 2026-08-29: geleakte
    # 25 GB nach ungefangener Probe-Exception).
    try:
        ok, total, _ = probe_coherence(server)
        tps = max(probe_throughput(server)) if ok == total else 0.0
    except Exception as probe_err:  # noqa: BLE001
        progress(f"   ↳ probe crashed ({type(probe_err).__name__}) — rung rejected")
        return None
    finally:
        server.shutdown()
    if ok < total:
        progress(f"   ↳ incoherent ({ok}/{total}) — rung rejected")
        return None
    progress(f"   ↳ {cand.label}: {format_number(tps, 1)} tok/s (coherence {ok}/{total})")
    return _RungResult(spec=spec, label=cand.label, tps=tps,
                       coherence=(ok, total), full_context=(spec.mml >= native))


def _sweep_k(
    rung: _RungResult,
    meta: VllmModelMeta,
    gpus: list[GPU],
    smi: dict[str, int],
    runtime: dict,
    log_dir: Path,
    progress: Callable[[str], None],
    cancel_check: Callable[[], bool] | None,
    allow_ctx_reduction: bool = False,
) -> tuple[VllmSpec, float, int, dict[int, float]]:
    """k-Sweep auf einer Topologie: (best_spec, best_tps, best_k, sweep).

    ``allow_ctx_reduction``: Der Draftkopf kostet KV-Budget — nennt vLLM
    beim k-Boot eine kleinere Kontextgrenze, wird sie uebernommen und neu
    gebootet. Nur fuer den Speed-Kandidaten erlaubt (dort ist reduzierter
    Kontext akzeptabel); Sieg-Kandidaten muessen ihr k beim vollen
    Sprossen-Kontext tragen (Kontext-Vorrang), sonst ist das k abgelehnt.
    """
    sweep: dict[int, float] = {0: rung.tps}
    best_k, best_tps = 0, rung.tps
    best_mml = rung.spec.mml
    for k in _k_candidates(meta, runtime):
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        allowed = meta.allowed_k_block_sizes()
        capture = _capture_sizes_for(k, runtime)
        spec_attn = _spec_attn_for(rung.spec, gpus, smi, runtime)
        mml_k = rung.spec.mml
        server = None
        # vLLMs Grenzschaetzung ist mit geladenem Draftkopf zu optimistisch
        # (Nachmessung 2026-08-29: k=7 brauchte ZWEI Uebernahme-Runden) —
        # deshalb iterative Uebernahme statt einem einzigen Retry.
        for attempt in range(3):
            spec_k = VllmSpec(
                **{**rung.spec.__dict__, "k": k, "mml": mml_k,
                   "block_size": allowed[k],
                   "capture_sizes": capture, "spec_attn_backend": spec_attn},
            )
            port = find_free_port()
            log = log_dir / f"boot-{rung.spec.tp}x{rung.spec.pp}-k{k}-try{attempt}.log"
            if attempt == 0:
                progress(f"🎲 Probing k={k} on {rung.label} (block {allowed[k]}, "
                         f"capture {capture}, spec-attn {spec_attn or 'default'})...")
            try:
                server = boot_vllm(spec_k, port, log, BOOT_TIMEOUT_S, cancel_check)
                break
            except VllmBootError as e:
                if (attempt < 2 and allow_ctx_reduction and e.parsed_max_len
                        and MIN_USEFUL_CONTEXT <= e.parsed_max_len < mml_k):
                    mml_k = e.parsed_max_len
                    progress(f"   ↳ k={k} needs smaller ctx: retry with {format_number(mml_k)}")
                    continue
                progress(f"   ↳ k={k} boot failed: {e.reason}")
                break
        if server is None:
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
        sweep[k] = tps
        progress(f"   ↳ k={k}: {format_number(tps, 1)} tok/s")
        if tps > best_tps:
            best_k, best_tps, best_mml = k, tps, mml_k

    if best_k:
        best_spec = VllmSpec(
            **{**rung.spec.__dict__, "k": best_k, "mml": best_mml,
               "block_size": meta.allowed_k_block_sizes()[best_k],
               "capture_sizes": _capture_sizes_for(best_k, runtime),
               "spec_attn_backend": _spec_attn_for(rung.spec, gpus, smi, runtime)},
        )
    else:
        best_spec = rung.spec
    return best_spec, best_tps, best_k, sweep


def calibrate_vllm_checkpoint(
    checkpoint: Path,
    entry_name: str,
    log_dir: Path,
    progress: Callable[[str], None],
    cancel_check: Callable[[], bool] | None = None,
) -> VllmCalibrationResult:
    """Kompletter Suchlauf. progress() bekommt englische Statuszeilen.

    Auswahlregel (Peuqui 2026-08-29): Kontext maximieren VOR Tempo.
    Der k-Sweep laeuft auf ALLEN kohaerenten Voll-Kontext-Topologien
    (der Spekulationsgewinn ist topologie-abhaengig — 27B: Gitter
    Faktor 2,5 vs. TP2 Faktor 1,4); kontext-reduzierte Sprossen werden
    gemessen und berichtet, gewinnen aber nur, wenn keine Sprosse den
    nativen Kontext traegt.
    """
    runtime = load_vllm_runtime()  # frueh scheitern, wenn die Umgebung fehlt

    progress(f"🔬 Analyzing checkpoint {_display_checkpoint_name(checkpoint)}...")
    meta = analyze_checkpoint(checkpoint)
    gib = 1024 ** 3
    progress(
        f"   {meta.architecture}, {meta.num_layers} layers, "
        f"{format_number(meta.total_bytes / gib, 1)} GiB, "
        f"native ctx {format_number(meta.native_context)}, "
        f"multimodal={meta.multimodal}, giants={meta.giant_layers}"
    )
    if meta.mtp.present:
        progress(
            f"   MTP block: {format_number(meta.mtp.bytes_total / gib, 2)} GiB "
            f"{meta.mtp.dominant_dtype}, worthwhile={_mtp_worthwhile(meta)}"
        )

    reserved = side_channel_uuids()
    gpus = eligible_gpus(reserved)
    if not gpus:
        raise RuntimeError("no eligible GPUs (all reserved for side channels)")
    progress(f"🖥️ Eligible GPUs: {[f'{g.name}({g.total_mb}MB)' for g in gpus]}")

    # --- Phase C/D: ALLE Topologie-Kandidaten booten und messen ----------
    # Der erste bootende Kandidat ist selten der schnellste (27B: TP=1 auf
    # einer RTX bootet, aber TP=2 ist Faktor 1,5 schneller) — deshalb wird
    # jede Sprosse gemessen.
    smi = _smi_index_by_uuid()
    uuid_by_smi = {v: k for k, v in smi.items()}
    rungs: list[_RungResult] = []
    for cand in topology_ladder(meta, gpus, runtime):
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        result = _measure_topology(cand, entry_name, meta, gpus, smi,
                                   log_dir, progress, cancel_check)
        if result is not None:
            rungs.append(result)

    if not rungs:
        raise RuntimeError("no topology candidate booted coherently")

    # Kontext-Vorrang: nur Voll-Kontext-Sprossen konkurrieren um den Sieg;
    # reduzierte Sprossen sind Fallback, wenn es keine volle gibt.
    pool = [r for r in rungs if r.full_context]
    fallback_pool = not pool
    if fallback_pool:
        progress("⚠️ No topology carries the native context — "
                 "falling back to reduced-context rungs")
        pool = list(rungs)
    pool.sort(key=lambda r: r.tps, reverse=True)
    progress(
        "📈 Topologies for k-sweep: "
        + ", ".join(f"{r.label} ({format_number(r.tps, 1)} tok/s)" for r in pool)
    )

    # Speed-Kandidat: die schnellste kontext-reduzierte Sprosse bekommt
    # ihren Sweep ZUSAETZLICH (informativ — z.B. V100+XQA als moegliche
    # Speed-Variante), konkurriert aber nicht um den Betriebspunkt.
    reduced = [r for r in rungs if not r.full_context]
    speed_candidate = max(reduced, key=lambda r: r.tps) if (reduced and not fallback_pool) else None
    if speed_candidate is not None:
        progress(
            f"🏎️ Reduced-context speed candidate: {speed_candidate.label} "
            f"(ctx {format_number(speed_candidate.spec.mml)}) — measured for info only"
        )

    # --- Phase E: k-Sweep auf JEDER Sieg-Kandidaten-Topologie ------------
    best_spec: VllmSpec | None = None
    best_tps, best_k = 0.0, 0
    best_rung: _RungResult | None = None
    best_sweep: dict[int, float] = {}
    for rung in pool:
        spec_r, tps_r, k_r, sweep_r = _sweep_k(
            rung, meta, gpus, smi, runtime, log_dir, progress, cancel_check)
        progress(f"   ↳ {rung.label} best: k={k_r}, {format_number(tps_r, 1)} tok/s")
        if tps_r > best_tps:
            best_spec, best_tps, best_k = spec_r, tps_r, k_r
            best_rung, best_sweep = rung, sweep_r

    sc_spec = None
    sc_tps, sc_k = 0.0, 0
    if speed_candidate is not None:
        sc_spec, sc_tps, sc_k, _ = _sweep_k(
            speed_candidate, meta, gpus, smi, runtime, log_dir,
            progress, cancel_check, allow_ctx_reduction=True)
        progress(
            f"🏎️ Speed candidate result: {speed_candidate.label} k={sc_k}, "
            f"{format_number(sc_tps, 1)} tok/s at ctx "
            f"{format_number(sc_spec.mml)} (info only — "
            f"operating point stays full-context)"
        )

    assert best_spec is not None and best_rung is not None
    ok, total = best_rung.coherence
    progress(f"🏁 Best point: {best_rung.label}, k={best_k}, "
             f"{format_number(best_tps, 1)} tok/s "
             f"(TP{best_spec.tp}×PP{best_spec.pp}, ctx {format_number(best_spec.mml)})")

    # --- Phase F: Persistierung -----------------------------------------
    profile_path = persist_operating_point(best_spec, best_tps, best_sweep, meta)
    progress(f"💾 Operating point saved: {profile_path}")

    # Verwendete GPUs im Log dokumentieren (UUID-Rueckverfolgbarkeit)
    used = [uuid_by_smi.get(i, str(i)) for i in best_spec.gpu_ids]
    logger.info(f"vllm calibration done: {entry_name} on {used}")

    return VllmCalibrationResult(
        spec=best_spec, throughput_tok_s=best_tps,
        coherence=(ok, total), k_sweep=best_sweep, profile_path=profile_path,
        speed_label=speed_candidate.label if speed_candidate else "",
        speed_k=sc_k, speed_tps=sc_tps,
        speed_mml=sc_spec.mml if sc_spec else 0,
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
