"""KV-Auswertung des vLLM-Boot-Logs (Override-Zeilen, Startbudget, Poolgroesse).

Die Logzeilen stammen aus echten Boots im llama-swap-Journal vom 20.10.2026
(DeepSeek-V4 PP5 mit Override 3000) und 16.10.2026 (Flash-Next TP2×PP2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.lib.calibration import vllm_probe
from aifred.lib.calibration.vllm_probe import VllmSpec, parse_kv_report

DSV4_BOOT = """\
(Worker_PP1 pid=1968929) INFO 10-20 06:49:17 [gpu_worker.py:728] GPU startup budget bytes: requested=32368931636 weights=29018915840 activation_peak=629426176 warmup_live=869087744 idle_reserved=220807680 non_torch=50331648 graph=629426176 kv=1171744052
(Worker_PP2 pid=1968932) INFO 10-20 06:49:17 [gpu_worker.py:728] GPU startup budget bytes: requested=32368931636 weights=29018915840 activation_peak=629426176 warmup_live=869087744 idle_reserved=220807680 non_torch=50331648 graph=629426176 kv=1171744052
(Worker_PP3 pid=1968930) INFO 10-20 06:49:17 [gpu_worker.py:728] GPU startup budget bytes: requested=32368931636 weights=29018915840 activation_peak=629426176 warmup_live=869087744 idle_reserved=220807680 non_torch=50331648 graph=629426176 kv=1171744052
(Worker_PP0 pid=1968928) INFO 10-20 06:49:18 [gpu_worker.py:728] GPU startup budget bytes: requested=48216824218 weights=37696717824 activation_peak=629426176 warmup_live=816658432 idle_reserved=541885440 non_torch=14680064 graph=0 kv=9059341722
(Worker_PP4 pid=1968931) INFO 10-20 06:49:18 [gpu_worker.py:728] GPU startup budget bytes: requested=48216824218 weights=46147175424 activation_peak=654592000 warmup_live=842488832 idle_reserved=435331072 non_torch=16777216 graph=0 kv=555790746
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:3343] Overriding num_gpu_blocks=47373 with num_gpu_blocks_override=3000
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:3343] Overriding num_gpu_blocks=6127 with num_gpu_blocks_override=3000
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:3343] Overriding num_gpu_blocks=6127 with num_gpu_blocks_override=3000
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:3343] Overriding num_gpu_blocks=6127 with num_gpu_blocks_override=3000
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:3343] Overriding num_gpu_blocks=2325 with num_gpu_blocks_override=3000
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:2994] GPU KV cache size: 399,133 tokens
(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:2995] Maximum concurrency for 307,200 tokens per request: 1.30x
"""

GRID_WORKERS = """\
(Worker_PP1_TP1 pid=4152363) INFO 10-16 02:13:56 [gpu_worker.py:728] GPU startup budget bytes: requested=32368931636 weights=24011099136 activation_peak=401966080 warmup_live=154833920 idle_reserved=18423808 non_torch=67108864 graph=0 kv=7733923636
(Worker_PP1_TP0 pid=4152364) INFO 10-16 02:13:56 [gpu_worker.py:728] GPU startup budget bytes: requested=32368931636 weights=24011099136 activation_peak=401966080 warmup_live=154833920 idle_reserved=18423808 non_torch=67108864 graph=0 kv=7733923636
(Worker_PP0_TP0 pid=4152361) INFO 10-16 02:14:06 [gpu_worker.py:728] GPU startup budget bytes: requested=48216824218 weights=41285025792 activation_peak=432308736 warmup_live=35803648 idle_reserved=39202304 non_torch=157351936 graph=0 kv=6306334106
(Worker_PP0_TP1 pid=4152362) INFO 10-16 02:14:06 [gpu_worker.py:728] GPU startup budget bytes: requested=48216824218 weights=41285025792 activation_peak=432308736 warmup_live=35803648 idle_reserved=39202304 non_torch=157351936 graph=0 kv=6306334106
"""


def test_report_of_a_passed_boot_names_blocks_budget_and_pool() -> None:
    kv = parse_kv_report(DSV4_BOOT, tp=1)
    assert kv.auto_blocks == (47373, 6127, 6127, 6127, 2325)
    # Rang-Reihenfolge, nicht Log-Reihenfolge (PP0 schreibt als vierter)
    assert kv.kv_bytes == (9059341722, 1171744052, 1171744052, 1171744052, 555790746)
    assert kv.blocks_auto == 2325                 # die V100-Stufe begrenzt
    assert kv.bytes_per_block()[0] == 9059341722 // 47373
    assert kv.bytes_per_block()[4] == 555790746 // 2325
    assert (kv.pool_tokens, kv.max_model_len) == (399133, 307200)
    assert not kv.too_small
    # 1,30x bei 3000 Bloecken: ein voller Kontext braucht 2310 (aufgerundet)
    assert kv.blocks_for_one_context(3000) == 2310


def test_tp_pp_workers_are_ordered_by_rank() -> None:
    kv = parse_kv_report(GRID_WORKERS, tp=2)
    assert kv.kv_bytes == (6306334106, 6306334106, 7733923636, 7733923636)


def test_too_small_override_estimates_one_context_from_the_failure() -> None:
    log = DSV4_BOOT.split("(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:2994]")[0]
    log += ("ValueError: To serve at least one request with the model's max seq len "
            "(307200), (0.53 GiB KV cache is needed, which is larger than the available "
            "KV cache memory (0.45 GiB). Based on the available memory, the estimated "
            "maximum model length is 261000. Try increasing ...")
    kv = parse_kv_report(log, tp=1)
    assert kv.too_small and kv.pool_tokens == 0
    assert kv.blocks_for_one_context(2000) == 2356     # 2000 × 0,53 / 0,45, aufgerundet


def test_tiny_override_falls_back_to_bytes_per_block_of_the_first_worker() -> None:
    log = DSV4_BOOT.replace("num_gpu_blocks_override=3000", "num_gpu_blocks_override=1")
    log = log.split("(EngineCore pid=1968626) INFO 10-20 06:49:18 [kv_cache_utils.py:2994]")[0]
    log += ("(0.44 GiB KV cache is needed, which is larger than the available KV cache "
            "memory (0.0 GiB). ")
    kv = parse_kv_report(log, tp=1)
    per_block = 9059341722 // 47373
    assert kv.blocks_for_one_context(1) == -(-int(0.44 * 1024 ** 3) // per_block)


def test_override_reaches_the_command_line(tmp_path: Path) -> None:
    runtime = {"python": "/x/python", "base_args": [], "base_env": {}}
    spec = VllmSpec(checkpoint=tmp_path, served_name="m", gpu_ids=[0], num_gpu_blocks=2325)
    cmd = spec.build_cmd(runtime, port=8000)
    assert cmd[cmd.index("--num-gpu-blocks-override") + 1] == "2325"
    assert "--num-gpu-blocks-override" not in VllmSpec(
        checkpoint=tmp_path, served_name="m", gpu_ids=[0]).build_cmd(runtime, port=8000)


@pytest.mark.parametrize(("too_small", "blocks", "cap"), [
    (True, 2000, None),      # unter dem Budget: die Blockzahl ist zu klein, nicht der Kontext
    (True, 2325, 261000),    # beim Budget selbst: vLLMs Schaetzung ist die Kontextgrenze
    (False, 2000, 261000),   # andere Absage: Grenze wie bisher
])
def test_boot_error_reads_the_kv_report_and_drops_the_bogus_context_cap(
    monkeypatch, tmp_path: Path, too_small: bool, blocks: int, cap: int | None,
) -> None:
    (tmp_path / "config.json").write_text("{}")
    failure = ("(0.53 GiB KV cache is needed, which is larger than the available KV cache "
               "memory (0.45 GiB). Based on the available memory, the estimated maximum "
               "model length is 261000." if too_small
               else "ValueError: the estimated maximum model length is 261000.")
    log_text = DSV4_BOOT.split("(EngineCore pid=1968626) INFO 10-20 06:49:18 "
                               "[kv_cache_utils.py:2994]")[0] + failure

    class DeadProcess:
        pid = 0
        returncode = 1

        def poll(self) -> int:
            return 1

        def wait(self, timeout: float = 0) -> int:
            return 1

    def fake_popen(cmd, stdout, **kwargs):
        stdout.write(log_text)
        stdout.flush()
        return DeadProcess()

    monkeypatch.setattr(vllm_probe, "load_vllm_runtime",
                        lambda: {"python": "/x/python", "base_args": [], "base_env": {}})
    monkeypatch.setattr(vllm_probe.subprocess, "Popen", fake_popen)
    spec = VllmSpec(checkpoint=tmp_path, served_name="m", gpu_ids=[0, 1, 2, 3, 4],
                    pp=5, num_gpu_blocks=blocks)
    with pytest.raises(vllm_probe.VllmBootError) as caught:
        vllm_probe.boot_vllm(spec, 8000, tmp_path / "boot.log", timeout_s=5)
    error = caught.value
    assert error.kv.blocks_auto == 2325
    assert error.kv.too_small is too_small
    # Bei zu kleinem Override ist die "estimated maximum model length" keine
    # Kontextgrenze — sonst wuerde die Suche den Kontext statt der Bloecke senken
    assert error.parsed_max_len == cap


def test_fill_probe_counts_tokens_at_the_server_and_fills_near_the_window() -> None:
    sent: list[tuple[int, int]] = []   # (Wiederholungen, max_tokens)

    class Server:
        def chat(self, prompt, max_tokens=64, ignore_eos=False, timeout_s=300.0, **kw):
            repeats = prompt.count(vllm_probe.LONG_CONTEXT_FILLER)
            sent.append((repeats, max_tokens))
            return "", {"prompt_tokens": repeats * 19 + 12}, 1.0

    tokens = vllm_probe.probe_fill_context(Server(), mml=262144)  # type: ignore[arg-type]
    assert sent[0] == (vllm_probe.FILL_SAMPLE_REPEATS, 1)
    assert 0.94 * 262144 <= tokens <= 262144


# ---------------------------------------------------------------------------
# _boot: die Blockzahl regelt sich am Boot-Log ein
# ---------------------------------------------------------------------------

from aifred.lib.calibration import vllm_flow  # noqa: E402
from aifred.lib.calibration.vllm_probe import KvReport, VllmBootError  # noqa: E402

MML = 65536


class _Server:
    def __init__(self, kv: KvReport, log: str = "") -> None:
        self.kv, self.log, self.stopped = kv, log, False

    def kv_report(self, tp: int) -> KvReport:
        return self.kv

    def log_text(self) -> str:
        return self.log

    def shutdown(self) -> None:
        self.stopped = True


def _passed(budget: int, one_context: int, blocks: int) -> KvReport:
    """Ein bestandener Boot mit ``blocks``: Budget und Bloecke fuer einen Kontext."""
    return KvReport(auto_blocks=(budget,), kv_bytes=(budget * 4096,),
                    pool_tokens=blocks * MML // one_context, max_model_len=MML)


def _too_small(budget: int) -> VllmBootError:
    return VllmBootError("KV check", kv=KvReport(
        auto_blocks=(budget,), kv_bytes=(budget * 4096,),
        needed_gib=2.0, available_gib=1.0))


def _scripted_boots(monkeypatch, outcomes):
    """boot_vllm spielt ``outcomes`` der Reihe nach ab (Funktion: Bloecke -> Server/Fehler)."""
    booted: list[int] = []

    def fake_boot(spec, port, log, timeout, cancel_check):
        booted.append(spec.num_gpu_blocks)
        outcome = outcomes[len(booted) - 1](spec.num_gpu_blocks)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(vllm_flow, "boot_vllm", fake_boot)
    return booted


def _spec(blocks: int) -> VllmSpec:
    return VllmSpec(checkpoint=Path("."), served_name="m", gpu_ids=[0], mml=MML,
                    num_gpu_blocks=blocks)


def test_unknown_model_learns_the_budget_from_the_rejected_seed(monkeypatch, tmp_path) -> None:
    booted = _scripted_boots(monkeypatch, [
        lambda n: _too_small(budget=2304),
        lambda n: _Server(_passed(budget=2304, one_context=2048, blocks=n)),
    ])
    server, spec, one_context = vllm_flow._boot(
        _spec(vllm_flow.UNKNOWN_BLOCKS_SEED), 0, tmp_path / "b.log", None, lambda m: None,
        exact=True)
    assert booted == [1, 2304]
    assert (spec.num_gpu_blocks, one_context) == (2304, 2048)


def test_a_smaller_cache_than_the_budget_is_rebooted_a_larger_one_only_when_exact(
    monkeypatch, tmp_path,
) -> None:
    # Samen = ein voller Kontext (400), Budget 1000: zu optimistische Messung -> neu
    booted = _scripted_boots(monkeypatch, [
        lambda n: _Server(_passed(budget=1000, one_context=400, blocks=n)),
        lambda n: _Server(_passed(budget=1000, one_context=400, blocks=n)),
    ])
    _, spec, _ = vllm_flow._boot(_spec(400), 0, tmp_path / "b.log", None, lambda m: None,
                                 exact=False)
    assert booted == [400, 1000] and spec.num_gpu_blocks == 1000
    # Mehr Bloecke als das Budget: ohne exact gilt die Messung, persistiert wird das Budget
    booted = _scripted_boots(monkeypatch, [
        lambda n: _Server(_passed(budget=1000, one_context=400, blocks=n)),
    ])
    _, spec, _ = vllm_flow._boot(_spec(1200), 0, tmp_path / "b.log", None, lambda m: None,
                                 exact=False)
    assert booted == [1200] and spec.num_gpu_blocks == 1000


def test_budget_without_a_full_context_forces_the_cache_beyond_it(monkeypatch, tmp_path) -> None:
    # Das Budget traegt 1000, ein Kontext braucht 2000 (2,0 / 1,0 GiB): erzwingen
    booted = _scripted_boots(monkeypatch, [
        lambda n: _too_small(budget=1000),
        lambda n: _Server(_passed(budget=1000, one_context=2000, blocks=n)),
    ])
    _, spec, one_context = vllm_flow._boot(_spec(1000), 0, tmp_path / "b.log", None,
                                           lambda m: None, exact=True)
    assert booted == [1000, 2000]
    assert (spec.num_gpu_blocks, one_context) == (2000, 2000)


def test_forcing_that_overruns_the_card_hands_back_vllms_context_cap(monkeypatch, tmp_path) -> None:
    budget_failure = _too_small(budget=1000)
    budget_failure.parsed_max_len = 32768          # vLLMs Schaetzung fuer das Budget
    overrun = VllmBootError("CUDA out of memory", oom=True, kv=KvReport(
        auto_blocks=(1000,), kv_bytes=(1000 * 4096,)))
    booted = _scripted_boots(monkeypatch, [lambda n: budget_failure, lambda n: overrun])
    with pytest.raises(VllmBootError) as caught:
        vllm_flow._boot(_spec(1000), 0, tmp_path / "b.log", None, lambda m: None, exact=True)
    assert booted == [1000, 2000]
    assert caught.value.parsed_max_len == 32768 and not caught.value.oom
    assert caught.value.kv.blocks_auto == 1000     # der Aufrufer bootet beim Budget neu


def test_fill_overrun_shrinks_the_cache_to_one_context_before_the_window(
    monkeypatch, tmp_path,
) -> None:
    oom_log = "torch.OutOfMemoryError: CUDA out of memory"
    servers = [
        _Server(_passed(budget=1000, one_context=400, blocks=1000), log=oom_log),
        _Server(_passed(budget=1000, one_context=400, blocks=400)),
    ]
    booted = _scripted_boots(monkeypatch, [lambda n: servers[0], lambda n: servers[1]])
    fills = iter([RuntimeError("HTTP 500"), 62000])

    def fake_fill(server, mml):
        outcome = next(fills)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(vllm_flow, "probe_fill_context", fake_fill)
    monkeypatch.setattr(vllm_flow, "free_mb_by_index", lambda: {0: 900, 1: 31000})
    monkeypatch.setattr(vllm_flow, "find_free_port", lambda: 0)
    spec, measured = vllm_flow.measure_under_load(_spec(1000), tmp_path, lambda m: None, None)
    assert booted == [1000, 400] and spec.num_gpu_blocks == 400
    assert measured.blocks == 400 and measured.blocks_one_context == 400
    assert measured.free_under_load_mb == {0: 900, 1: 31000}
    assert measured.bytes_per_block == {0: 4096}
    assert vllm_flow.KvMeasurement.from_meta(measured.as_meta()) == measured
