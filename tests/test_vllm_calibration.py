"""vLLM-Kalibration (Paket 3): Modell-Analyse, Probe-Spec, Suchbausteine
und Betriebspunkt-Persistenz. Alle Tests laufen ohne GPU/Server —
Safetensors werden synthetisch gebaut, GPU-Inventar und Runtime werden
gemockt. Die Ground-Truth-Werte stammen aus den vermessenen Modellen
(Flash-Next: k=4/Block 16, Sperrzone k=5-8/Block 48; 27B: k=7 erlaubt).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import yaml

import aifred.lib.calibration.vllm_flow as vllm_flow
import aifred.lib.calibration.vllm_probe as vllm_probe
import aifred.lib.operating_points as operating_points
from aifred.lib.calibration.types import GPU
from aifred.lib.calibration.vllm_model_meta import (
    VllmModelMeta,
    MtpInfo,
    _component,
    _layer_index,
    analyze_checkpoint,
)
from aifred.lib.calibration.vllm_probe import VllmSpec


# ---------------------------------------------------------------------------
# Synthetischer Checkpoint (Safetensors-Header mit data_offsets, ohne GPU)
# ---------------------------------------------------------------------------

def _write_safetensors(path: Path, tensors: dict[str, tuple[str, int]]) -> None:
    """Minimal gueltige Safetensors-Datei: {name: (dtype, nbytes)}."""
    header: dict = {}
    offset = 0
    for name, (dtype, nbytes) in tensors.items():
        header[name] = {
            "dtype": dtype,
            "shape": [nbytes],  # Shape ist fuer die Analyse irrelevant
            "data_offsets": [offset, offset + nbytes],
        }
        offset += nbytes
    blob = json.dumps(header).encode()
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(blob)))
        f.write(blob)
        f.write(b"\0" * offset)


@pytest.fixture
def moe_checkpoint(tmp_path: Path) -> Path:
    """MoE-Modell mit Riesen-Layer (PLE-Klasse), MoE-MTP-Block und QSA."""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    (ckpt / "config.json").write_text(json.dumps({
        "architectures": ["TestMoeForCausalLM"],
        "text_config": {
            "num_hidden_layers": 4,
            "max_position_embeddings": 65536,
            "indexer_compress_ratio": 4,
            "num_experts": 8,
            "num_experts_per_tok": 2,
        },
        "vision_config": {},
    }))
    tensors: dict[str, tuple[str, int]] = {}
    for layer in range(4):
        tensors[f"model.language_model.layers.{layer}.attn.weight"] = ("F16", 1000)
        for e in range(8):
            tensors[f"model.language_model.layers.{layer}.mlp.experts.{e}.w"] = ("U8", 500)
    # Riesen-Layer 1: PLE-Tabelle (Faktor >4 ueber dem Median)
    tensors["model.language_model.layers.1.ple.table"] = ("F8_E4M3", 200_000)
    # MoE-MTP-Block mit Skalen (quantisiert) + eigenen Experten
    tensors["mtp.layers.0.attn.weight"] = ("BF16", 400)
    for e in range(8):
        tensors[f"mtp.layers.0.mlp.experts.{e}.w"] = ("U8", 100)
        tensors[f"mtp.layers.0.mlp.experts.{e}.w_scale"] = ("F8_E4M3", 10)
    tensors["model.visual.encoder.weight"] = ("F16", 3000)
    _write_safetensors(ckpt / "model.safetensors", tensors)
    return ckpt


def test_analyze_moe_checkpoint(moe_checkpoint: Path) -> None:
    m = analyze_checkpoint(moe_checkpoint)
    assert m.architecture == "TestMoeForCausalLM"
    assert m.num_layers == 4
    assert m.native_context == 65536
    assert m.multimodal is True
    assert m.compress_ratio == 4
    assert m.giant_layers == [1]          # PLE-Layer erkannt
    assert m.mtp.present and m.mtp.quantized
    # Gewichte + Skalen: beide werden beim Expert-Read gelesen
    assert m.mtp.expert_bytes == 8 * 100 + 8 * 10
    # Experten-Bytes des Hauptmodells: 4 Layer x 8 Experten x 500
    assert m.expert_bytes >= 4 * 8 * 500
    assert m.ple_bytes == 200_000


def test_per_token_read_moe(moe_checkpoint: Path) -> None:
    m = analyze_checkpoint(moe_checkpoint)
    read = m.per_token_read_bytes()
    # Vision und PLE lesen nicht mit; Experten nur zu 2/8
    assert read < m.total_bytes - m.ple_bytes - 3000
    # MoE-MTP-Schritt liest seine Experten ebenfalls anteilig
    assert m.mtp_read_bytes_per_step() < m.mtp.bytes_total


def test_qsa_block_size_arithmetic() -> None:
    meta = VllmModelMeta(
        checkpoint=Path("."), architecture="x", num_layers=1,
        native_context=1, total_bytes=1, layer_bytes={},
        component_bytes={}, giant_layers=[], compress_ratio=4,
    )
    allowed = meta.allowed_k_block_sizes()
    # Ground-Truth Flash-Next: k<=4 -> Block 16, k=5-8 -> Block 48
    assert allowed[4] == 16
    assert all(allowed[k] == 48 for k in (5, 6, 7, 8))
    # Ohne QSA (ratio 1): alles Block 16
    meta_plain = VllmModelMeta(
        checkpoint=Path("."), architecture="x", num_layers=1,
        native_context=1, total_bytes=1, layer_bytes={},
        component_bytes={}, giant_layers=[], compress_ratio=1,
    )
    assert set(meta_plain.allowed_k_block_sizes().values()) == {16}


def test_tensor_name_parsing() -> None:
    assert _layer_index("model.language_model.layers.17.mlp.w") == 17
    assert _layer_index("lm_head.weight") is None
    assert _component("mtp.layers.0.w") == "mtp"
    assert _component("model.visual.enc.w") == "model.visual"


# ---------------------------------------------------------------------------
# Probe-Spec: Kommandozeile und Environment
# ---------------------------------------------------------------------------

RUNTIME = {
    "python": "/usr/bin/python3",
    "base_env": {"NCCL_P2P_DISABLE": "1"},
    "base_args": ["--trust-remote-code"],
    "max_capture_size": 8,
}


def test_spec_build_cmd_json_compact(tmp_path: Path) -> None:
    spec = VllmSpec(
        checkpoint=tmp_path, served_name="m", gpu_ids=[0, 2, 1, 4],
        tp=2, pp=2, k=4, capture_sizes=[1, 2, 4, 5, 8],
        pp_partition="24,24",
    )
    cmd = spec.build_cmd(RUNTIME, port=1234)
    spec_json = cmd[cmd.index("--speculative-config") + 1]
    # JSON ohne Leerzeichen: ein argv-Token, llama-swap-shellwords-sicher
    assert " " not in spec_json
    assert json.loads(spec_json)["num_speculative_tokens"] == 4
    comp_json = cmd[cmd.index("--compilation-config") + 1]
    assert json.loads(comp_json)["cudagraph_capture_sizes"] == [1, 2, 4, 5, 8]
    assert "--async-scheduling" in cmd  # PP>1


def test_spec_build_env_switches(tmp_path: Path) -> None:
    spec = VllmSpec(checkpoint=tmp_path, served_name="m",
                    gpu_ids=[1, 4], tp=1, pp=2, k=4, pp_partition="10,20")
    env = spec.build_env(RUNTIME)
    assert env["CUDA_VISIBLE_DEVICES"] == "1,4"
    assert env["VLLM_PP_LAYER_PARTITION"] == "10,20"
    assert env["VLLM_1CAT_ENABLE_SM70_MTP_DEFAULTS"] == "1"
    # Spekulation ueber PP: qwen3_5-Schalter gesetzt
    assert env["VLLM_QWEN35_MTP_SHARE_IO_WEIGHTS"] == "0"
    k0 = VllmSpec(checkpoint=tmp_path, served_name="m", gpu_ids=[1])
    assert "VLLM_1CAT_ENABLE_SM70_MTP_DEFAULTS" not in k0.build_env(RUNTIME)


# ---------------------------------------------------------------------------
# Suchbausteine
# ---------------------------------------------------------------------------

def _gpu(uuid: str, name: str, cc: float, total: int, free: int,
         speed_class: int) -> GPU:
    return GPU(uuid=uuid, name=name, compute_cap=cc, total_mb=total,
               free_mb=free, speed_class=speed_class, first_in_class=False)


MINI_GPUS = [
    _gpu("GPU-a", "RTX 8000", 7.5, 49152, 48000, 0),
    _gpu("GPU-b", "RTX 8000", 7.5, 49152, 48000, 0),
    _gpu("GPU-c", "V100", 7.0, 32768, 32000, 1),
    _gpu("GPU-d", "V100", 7.0, 32768, 32000, 1),
]
SMI = {"GPU-a": 0, "GPU-b": 2, "GPU-c": 1, "GPU-d": 4}


def _meta(total_gib: float, giants: list[int] | None = None) -> VllmModelMeta:
    return VllmModelMeta(
        checkpoint=Path("."), architecture="x", num_layers=48,
        native_context=65536, total_bytes=int(total_gib * 1024**3),
        layer_bytes={}, component_bytes={}, giant_layers=giants or [],
        mtp=MtpInfo(False),
    )


def test_topology_ladder_small_model(monkeypatch) -> None:
    monkeypatch.setattr(vllm_flow, "_smi_index_by_uuid", lambda: SMI)
    rungs = list(vllm_flow.topology_ladder(_meta(20.0), MINI_GPUS, RUNTIME))
    # Kleines Modell: Einzelkarten zuerst, dann Klassen-TP, dann Gitter
    assert rungs[0].tp == 1 and rungs[0].pp == 1
    grid = rungs[-1]
    assert grid.tp == 2 and grid.pp == 2
    # Stufenordnung: hoechste Klasse zuerst (RTX 0,2 dann V100 1,4)
    assert grid.gpu_ids == [0, 2, 1, 4]


def test_topology_ladder_large_model(monkeypatch) -> None:
    monkeypatch.setattr(vllm_flow, "_smi_index_by_uuid", lambda: SMI)
    rungs = list(vllm_flow.topology_ladder(_meta(120.0), MINI_GPUS, RUNTIME))
    # 120 GiB x 1.6 passt auf keine Karte und keine Einzel-Klasse:
    # nur das Gitter bleibt
    assert len(rungs) == 1
    assert (rungs[0].tp, rungs[0].pp) == (2, 2)


def test_seed_partition_giants() -> None:
    meta = _meta(120.0, giants=[0, 1])
    stages = [MINI_GPUS[:2], MINI_GPUS[2:]]
    partition = vllm_flow._seed_partition(meta, stages)
    assert partition is not None
    first, second = (int(x) for x in partition.split(","))
    assert first + second == 48
    assert first >= 2  # Riesen-Layer 0+1 muessen komplett in Stufe 0


def test_capture_sizes_limit() -> None:
    assert vllm_flow._capture_sizes_for(4, RUNTIME) == [1, 2, 4, 5, 8]
    # Stack-Limit 8: Verifier-Batch 10 faellt raus
    assert vllm_flow._capture_sizes_for(9, RUNTIME) == [1, 2, 4, 8]
    # Ohne Limit bleibt er drin
    assert 10 in vllm_flow._capture_sizes_for(9, {"python": "x"})


def test_gmu_from_fixed_reserve() -> None:
    gmu = vllm_flow._gmu_for(MINI_GPUS[2:])  # V100: (32768-1024)/32768
    assert gmu == round((32768 - 1024) / 32768, 2)


def test_mtp_worthwhile_ratio() -> None:
    # Dense 20 GiB, kleiner Draft-Block (4 %): lohnt
    dense = _meta(20.0)
    dense.mtp = MtpInfo(True, bytes_total=int(0.8 * 1024**3))
    assert vllm_flow._mtp_worthwhile(dense) is True
    # Draft-Block liest mehr als 25 % der Leselast: lohnt nicht
    heavy = _meta(20.0)
    heavy.mtp = MtpInfo(True, bytes_total=int(6.0 * 1024**3))
    assert vllm_flow._mtp_worthwhile(heavy) is False


# ---------------------------------------------------------------------------
# Betriebspunkt-Persistenz
# ---------------------------------------------------------------------------

def test_render_llamaswap_entry_quoting(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(vllm_probe, "load_vllm_runtime", lambda: RUNTIME)
    monkeypatch.setattr(vllm_flow, "load_vllm_runtime", lambda: RUNTIME)
    spec = VllmSpec(checkpoint=tmp_path, served_name="m", gpu_ids=[0],
                    k=4, capture_sizes=[1, 2, 4, 5, 8])
    entry = vllm_flow.render_llamaswap_entry(spec)
    assert "--port ${PORT}" in entry["cmd"]
    # JSON-Argumente einfach gequotet (llama-swap-shellwords)
    assert "'{\"method\":" in entry["cmd"]
    assert entry["cmdStop"].endswith("vllm-swap-stop ${PID}")
    assert any(e.startswith("CUDA_VISIBLE_DEVICES=0") for e in entry["env"])


def test_operating_point_apply_and_hardware_guard(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "healthCheckTimeout": 900,
        "models": {"other": {"cmd": "llama-server --model /x.gguf"}},
        "groups": {"main": {"exclusive": True, "swap": True,
                            "members": ["other"]}},
    }))
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(operating_points, "LLAMASWAP_CONFIG_PATH", cfg)
    monkeypatch.setattr(operating_points, "OPERATING_POINTS_DIR", profiles)
    monkeypatch.setattr(operating_points, "gpu_fingerprint", lambda: "T4:16384")

    entry = {"cmd": f"vllm serve --model {ckpt} --port ${{PORT}}", "ttl": 60}
    (profiles / "test-vllm.yaml").write_text(yaml.safe_dump({
        "llamaswap": entry, "hardware": "T4:16384",
    }))

    msgs = operating_points.apply_operating_point("test-vllm")
    assert any("applied" in m for m in msgs)
    written = yaml.safe_load(cfg.read_text())
    assert written["models"]["test-vllm"] == entry
    assert "test-vllm" in written["groups"]["main"]["members"]
    assert written["models"]["other"]["cmd"].startswith("llama-server")

    # Idempotent: zweiter Apply schreibt nicht
    before = cfg.read_text()
    operating_points.apply_operating_point("test-vllm")
    assert cfg.read_text() == before

    # Hardware-Wechsel: Ablehnung statt stillem Anwenden
    monkeypatch.setattr(operating_points, "gpu_fingerprint", lambda: "H100:81920")
    with pytest.raises(ValueError, match="Hardware changed"):
        operating_points.apply_operating_point("test-vllm")

    # Checkpoint weg: Ablehnung
    monkeypatch.setattr(operating_points, "gpu_fingerprint", lambda: "T4:16384")
    ckpt.rmdir()
    with pytest.raises(ValueError, match="gone"):
        operating_points.apply_operating_point("test-vllm")


# ---------------------------------------------------------------------------
# Langkontext-Decode: Messung darf nicht am Prefix-Cache haengen
# ---------------------------------------------------------------------------

class _FakeLongCtxServer:
    """Server-Attrappe mit fester Prefill- und Decode-Rate.

    ``cached`` steuert, ob der zweite Call den Prefix-Cache trifft. Die
    gemessene Decode-Rate muss in beiden Faellen dieselbe sein.
    """

    def __init__(self, prefill_tps: float, decode_tps: float, cached: bool) -> None:
        self.prefill_tps = prefill_tps
        self.decode_tps = decode_tps
        self.cached = cached
        self.calls = 0

    def chat(self, prompt: str, max_tokens: int, **kwargs: object) -> tuple:
        self.calls += 1
        prompt_tokens = len(prompt) // 5  # Attrappen-Tokenizer, Wert egal
        cached_tokens = prompt_tokens if (self.cached and self.calls > 1) else 0
        elapsed = (prompt_tokens - cached_tokens) / self.prefill_tps
        elapsed += max_tokens / self.decode_tps
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": max_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        }
        return "", usage, elapsed

    def metrics(self) -> dict:
        return {}


@pytest.mark.parametrize("cached", [True, False])
def test_long_context_decode_independent_of_prefix_cache(cached: bool) -> None:
    server = _FakeLongCtxServer(prefill_tps=520.0, decode_tps=40.0, cached=cached)
    point = vllm_probe.probe_long_context(server, mml=262144)  # type: ignore[arg-type]
    assert point is not None
    assert point["decode_tps"] == pytest.approx(40.0, rel=0.02)
    assert point["prefill_tps"] == pytest.approx(520.0, rel=0.02)


# ---------------------------------------------------------------------------
# TP-Gueltigkeit: TP muss Attention- UND KV-Heads teilen
# ---------------------------------------------------------------------------

def _meta_heads(total_gib: float, heads: int, kv_heads: int) -> VllmModelMeta:
    return VllmModelMeta(
        checkpoint=Path("."), architecture="x", num_layers=48,
        native_context=65536, total_bytes=int(total_gib * 1024**3),
        layer_bytes={}, component_bytes={}, giant_layers=[],
        mtp=MtpInfo(False),
        num_attention_heads=heads, num_key_value_heads=kv_heads,
    )


def test_valid_tp_sizes_bounded_by_kv_heads() -> None:
    # 27B: 24 Attention-Heads, 4 KV-Heads -> TP3 ist ungueltig
    meta = _meta_heads(20.0, 24, 4)
    assert meta.valid_tp_sizes(4) == [1, 2, 4]
    assert 3 not in meta.valid_tp_sizes(8)
    # Ohne Head-Angaben wird nicht eingeschraenkt (raten waere schlechter)
    assert _meta(20.0).valid_tp_sizes(3) == [1, 2, 3]


def test_topology_ladder_skips_invalid_tp(monkeypatch) -> None:
    """Drei V100 duerfen kein TP3 erzeugen — vLLM lehnt es beim
    Worker-Start ab ("16 is not divisible by 3", Lauf 2026-09-04)."""
    gpus = MINI_GPUS + [_gpu("GPU-e", "V100", 7.0, 32768, 31000, 1)]
    smi = {**SMI, "GPU-e": 3}
    monkeypatch.setattr(vllm_flow, "_smi_index_by_uuid", lambda: smi)
    meta = _meta_heads(20.0, 24, 4)
    rungs = list(vllm_flow.topology_ladder(meta, gpus, RUNTIME))
    assert all(r.tp != 3 for r in rungs), [r.label for r in rungs]
    v100_tp = [r for r in rungs if r.pp == 1 and r.tp > 1 and 1 in r.gpu_ids]
    assert v100_tp and v100_tp[0].tp == 2
    # Deterministische Wahl: freiester zuerst, bei Gleichstand kleinerer Index
    assert v100_tp[0].gpu_ids == [1, 4]


# ---------------------------------------------------------------------------
# Siegerregel: Gesamtzeit statt Decode-Schwelle
# ---------------------------------------------------------------------------

def test_beats_weighs_prefill_against_decode() -> None:
    """Messwerte 2026-09-04: Gitter 50,7 tok/s bei 749 Prefill gegen
    TP2 56,4 bei 449. Bei langen Prompts gewinnt das Gitter."""
    grid, tp2 = (50.7, 749.0), (56.4, 449.0)
    # 4 Prompt-Token je erzeugtem Token: Gitter vorn
    assert vllm_flow._beats(*grid, *tp2, 4.0)
    assert not vllm_flow._beats(*tp2, *grid, 4.0)
    # Kurze Prompts, lange Antworten: TP2 vorn
    assert vllm_flow._beats(*tp2, *grid, 0.5)
    # Gleicher Prefill (k-Vergleich INNERHALB einer Topologie): Decode zaehlt
    assert vllm_flow._beats(56.4, 449.0, 50.7, 449.0, 4.0)
    # Prefill-Rauschen friert kein besseres k ein (834 gegen 833)
    assert vllm_flow._beats(56.4, 833.0, 50.7, 834.0, 4.0)


# ---------------------------------------------------------------------------
# k-Sweep: geerbter Kontext + Nachschlag fuer den Sieger
# ---------------------------------------------------------------------------

class _FakeServer:
    def shutdown(self) -> None:
        pass


def _sweep_harness(monkeypatch, caps: dict[int, int], decode: dict[int, float]):
    """Simuliert Boots: caps[k] ist der groesste Kontext, den k traegt.

    Gibt die Liste der versuchten (k, mml) zurueck — daran laesst sich
    ablesen, wie viele Boots der Sweep gekostet hat.
    """
    from aifred.lib.calibration.vllm_probe import VllmBootError

    attempts: list[tuple[int, int]] = []

    def fake_boot(spec, port, log, timeout, cancel_check):
        attempts.append((spec.k, spec.mml))
        cap = caps[spec.k]
        if spec.mml > cap:
            raise VllmBootError("too large", parsed_max_len=cap)
        return _FakeServer()

    monkeypatch.setattr(vllm_flow, "boot_vllm", fake_boot)
    monkeypatch.setattr(vllm_flow, "find_free_port", lambda: 9999)
    monkeypatch.setattr(vllm_flow, "probe_coherence", lambda s: (3, 3, []))
    monkeypatch.setattr(vllm_flow, "probe_throughput", lambda s, **kw: [10.0])
    monkeypatch.setattr(vllm_flow, "_k_candidates", lambda m, r: [3, 2, 1])

    def fake_long(server, mml):
        # Rate haengt am k des zuletzt gebooteten Specs
        k = attempts[-1][0]
        return {"tokens": 1000, "prefill_tps": 500.0,
                "decode_tps": decode[k], "accept_rate": 0.9}

    monkeypatch.setattr(vllm_flow, "probe_long_context", fake_long)
    return attempts


def test_sweep_inherits_context_and_regrows_winner(monkeypatch, tmp_path):
    """Jedes k bootet einmal; der Sieger holt seinen Kontext zurueck.

    caps: k=3 traegt 20.000, k=2 dann 30.000, k=1 sogar 40.000 — genau das
    Muster des Laufs 2026-09-04 (19.136 / 22.848 / 25.296).
    """
    caps = {3: 20000, 2: 30000, 1: 40000}
    decode = {3: 30.0, 2: 50.0, 1: 40.0}          # k=2 gewinnt
    attempts = _sweep_harness(monkeypatch, caps, decode)

    spec = VllmSpec(checkpoint=tmp_path, served_name="m", gpu_ids=[0],
                    mml=65536, block_size=16)
    rung = vllm_flow._RungResult(spec=spec, label="TP1", tps=10.0,
                                 coherence=(3, 3), full_context=True,
                                 long_tokens=1000, long_prefill_tps=500.0,
                                 long_decode_tps=20.0)
    best_spec, _, best_k, _, _ = vllm_flow._sweep_k(
        rung, _meta(20.0), MINI_GPUS, SMI, RUNTIME, tmp_path,
        lambda msg: None, None, allow_ctx_reduction=True)

    assert best_k == 2
    # k=3 sucht den Deckel (2 Boots), k=2 und k=1 erben ihn (je 1),
    # danach EIN Nachschlag fuer den Sieger — der wieder suchen muss.
    assert [a[0] for a in attempts] == [3, 3, 2, 1, 2, 2]
    assert attempts[2][1] == 20000 and attempts[3][1] == 20000
    # Der Betriebspunkt traegt den Kontext des Siegers, nicht den geerbten
    assert best_spec.mml == 30000


def test_sweep_without_cap_does_not_regrow(monkeypatch, tmp_path):
    """Traegt jedes k den vollen Kontext, gibt es keinen Nachschlag."""
    caps = {3: 65536, 2: 65536, 1: 65536}
    attempts = _sweep_harness(monkeypatch, caps,
                              {3: 30.0, 2: 50.0, 1: 40.0})
    spec = VllmSpec(checkpoint=tmp_path, served_name="m", gpu_ids=[0],
                    mml=65536, block_size=16)
    rung = vllm_flow._RungResult(spec=spec, label="TP1", tps=10.0,
                                 coherence=(3, 3), full_context=True,
                                 long_tokens=1000, long_prefill_tps=500.0,
                                 long_decode_tps=20.0)
    best_spec, _, best_k, _, _ = vllm_flow._sweep_k(
        rung, _meta(20.0), MINI_GPUS, SMI, RUNTIME, tmp_path,
        lambda msg: None, None, allow_ctx_reduction=True)
    assert best_k == 2
    assert [a[0] for a in attempts] == [3, 2, 1]
    assert best_spec.mml == 65536
