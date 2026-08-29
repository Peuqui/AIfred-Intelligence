"""
vLLM-Probe-Primitiv fuer die Kalibration.

Der Messbaustein, auf dem die vLLM-Betriebspunkt-Suche aufsetzt:
einen Server mit einem konkreten Parametersatz booten, den Ausgang
strukturiert bewerten (healthy / gestorben mit geparster Ursache),
Kohaerenz und Durchsatz messen, den Prozessbaum vollstaendig beenden.

Maschine vs. Modell: Die Laufzeitumgebung (venv, Plattform-Env,
Pflicht-Flags) kommt aus ``data/vllm_runtime.yaml`` — die Probe kennt
nur Modell-Parameter. Ohne Runtime-Datei gibt es keinen stillen
Fallback: vLLM-Kalibration setzt eine deklarierte Umgebung voraus.
"""

import json
import logging
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import DATA_DIR

VLLM_RUNTIME_PATH = DATA_DIR / "vllm_runtime.yaml"

logger = logging.getLogger(__name__)


# Nacktes CUDA-OOM (Signaturen im Boot-Log; Erkennung IMMER am vollen
# Log — siehe Kommentar am Grenzwert-Parsing).
OOM_SIGNATURES = (
    "CUDA out of memory",
    "OutOfMemoryError",
    "No available memory for the cache blocks",
)


def parse_vllm_max_context_from_error(error_output: str) -> int | None:
    """
    Parse maximum possible context from vLLM error message.

    vLLM error messages contain lines like:
    "Please reduce the max_model_len or increase tensor_parallel_size.
     You can calculate the maximum possible value for --max-model-len by..."
     OR
    "ValueError: ... only X.XX GiB available. You may increase it by ..."

    But the most reliable pattern is looking for explicit token limits in errors.

    Args:
        error_output: vLLM stderr/stdout output containing error message

    Returns:
        Maximum context in tokens if found, None otherwise

    Example error patterns:
        - "... estimated maximum model length is 3296"
        - "ValueError: ... Please use a smaller max_model_len (<=20784)"
        - "... max sequence length must be at most 24156"
        - "The model's max seq len (131072) is larger than the maximum number of tokens that can be stored in KV cache (20784)"
    """
    # Pattern 1: "estimated maximum model length is X" - MOST RELIABLE
    # This is vLLM's direct recommendation from VRAM calculation
    match = re.search(r'estimated\s+maximum\s+model\s+length\s+is\s+(\d+)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 1 - estimated): {tokens:,} tokens")
        return tokens

    # Pattern 2: "max_model_len (<=X)" or "max_model_len (<= X)"
    match = re.search(r'max_model_len\s*\(?\s*<=?\s*(\d+)\)?', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 2): {tokens:,} tokens")
        return tokens

    # Pattern 3: "max sequence length must be at most X"
    match = re.search(r'max\s+sequence\s+length\s+must\s+be\s+at\s+most\s+(\d+)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 3): {tokens:,} tokens")
        return tokens

    # Pattern 4: "derived max_model_len (max_position_embeddings=X" - calibration blocking
    match = re.search(r'derived\s+max_model_len\s+\(max_position_embeddings=(\d+)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 4 - native limit): {tokens:,} tokens")
        return tokens

    # Pattern 5: "KV cache (X)" - last resort
    match = re.search(r'KV\s+cache\s+\((\d+)\)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 5): {tokens:,} tokens")
        return tokens

    logger.warning("Could not parse max context from vLLM error output")
    return None



def load_vllm_runtime() -> dict:
    """Laufzeitumgebung laden; fehlende/kaputte Datei ist ein harter Fehler."""
    if not VLLM_RUNTIME_PATH.exists():
        raise FileNotFoundError(
            f"vLLM runtime config missing: {VLLM_RUNTIME_PATH}. "
            f"Declare python/base_env/base_args for this machine's vLLM install."
        )
    runtime: dict = yaml.safe_load(VLLM_RUNTIME_PATH.read_text())
    if not isinstance(runtime, dict):
        raise ValueError(f"vLLM runtime config is not a mapping: {VLLM_RUNTIME_PATH}")
    python = runtime.get("python", "")
    if not python or not Path(python).exists():
        raise FileNotFoundError(
            f"vLLM runtime python not found: '{python}' (from {VLLM_RUNTIME_PATH})"
        )
    runtime.setdefault("base_env", {})
    runtime.setdefault("base_args", [])
    return runtime


@dataclass
class VllmSpec:
    """Ein konkreter Boot-Parametersatz (die Suchvariablen der Kalibration)."""

    checkpoint: Path
    served_name: str
    gpu_ids: list[int]              # numerisch, PCI-Reihenfolge = Stufenordnung
    tp: int = 1
    pp: int = 1
    gmu: float = 0.90
    mml: int = 4096
    block_size: int = 16
    k: int = 0                      # Spekulationstiefe (0 = aus)
    capture_sizes: list[int] | None = None
    pp_partition: str | None = None  # z.B. "24,24"
    max_num_seqs: int = 4
    max_batched_tokens: int = 2048
    language_model_only: bool = False
    # Attention-Backend des Drafters (abhaengig von der Compute-Klasse der
    # letzten PP-Stufe); None = vLLM-Default
    spec_attn_backend: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)

    def build_cmd(self, runtime: dict, port: int) -> list[str]:
        cmd = [
            str(runtime["python"]), "-m", "vllm.entrypoints.openai.api_server",
            "--model", str(self.checkpoint),
            "--served-model-name", self.served_name,
            *runtime["base_args"],
            "--tensor-parallel-size", str(self.tp),
            "--pipeline-parallel-size", str(self.pp),
            "--gpu-memory-utilization", str(self.gmu),
            "--block-size", str(self.block_size),
            "--max-model-len", str(self.mml),
            "--max-num-seqs", str(self.max_num_seqs),
            "--max-num-batched-tokens", str(self.max_batched_tokens),
            "--host", "127.0.0.1", "--port", str(port),
        ]
        if self.language_model_only:
            cmd.append("--language-model-only")
        if self.pp > 1:
            # Kampagnen-Befund: PP-Betrieb (insb. mit Spekulation) laeuft
            # auf diesem Stack mit async scheduling (MERGE-Handover S.2/4)
            cmd.append("--async-scheduling")
        if self.k > 0:
            spec_cfg: dict = {"method": "mtp", "num_speculative_tokens": self.k,
                              "draft_sample_method": "greedy"}
            spec_cfg.update(runtime.get("spec_config_extra") or {})
            if self.spec_attn_backend:
                spec_cfg["attention_backend"] = self.spec_attn_backend
            cmd += ["--speculative-config", json.dumps(spec_cfg, separators=(",", ":"))]
        if self.capture_sizes:
            comp = {"cudagraph_capture_sizes": self.capture_sizes}
            cmd += ["--compilation-config", json.dumps(comp, separators=(",", ":"))]
        cmd += self.extra_args
        return cmd

    def build_env(self, runtime: dict) -> dict[str, str]:
        env = {**runtime["base_env"]}
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in self.gpu_ids)
        if self.pp_partition:
            env["VLLM_PP_LAYER_PARTITION"] = self.pp_partition
        if self.k > 0:
            env["VLLM_1CAT_ENABLE_SM70_MTP_DEFAULTS"] = "1"
        if self.k > 0 and self.pp > 1:
            # qwen3_5-Familie: geteiltes Embedding liegt auf Stufe 0, der
            # Drafter auf der letzten Stufe — ohne den Schalter crasht
            # PPMissingLayer (andere Familien ignorieren die Variable)
            env.setdefault("VLLM_QWEN35_MTP_SHARE_IO_WEIGHTS", "0")
        env.update(self.extra_env)
        # Minimal-Basis, damit Subprozesse (nvcc, ninja) funktionieren
        env.setdefault("HOME", os.environ.get("HOME", str(Path.home())))
        return env


class VllmBootError(Exception):
    """Boot gescheitert — mit geparster Ursache fuer die Suche."""

    def __init__(self, reason: str, log_tail: str = "",
                 parsed_max_len: int | None = None, oom: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.log_tail = log_tail
        # OOM ohne von vLLM genannte Grenze — am VOLLEN Log erkannt (die
        # OOM-Zeile liegt vor den Folge-Tracebacks, ausserhalb des Tails).
        self.oom = oom
        # Von vLLM selbst genannte Kontext-Obergrenze (falls in der
        # Fehlermeldung enthalten) — direktes Futter fuer die MML-Suche.
        self.parsed_max_len = parsed_max_len


def _kill_tree(pid: int, grace_s: int = 25) -> None:
    """Prozessbaum beenden: Nachfahren VOR dem Signal einsammeln (vLLM-Worker
    verlassen die Prozessgruppe), TERM auf alle, nach grace_s KILL."""

    def descendants(p: int) -> list[int]:
        try:
            out = subprocess.run(["ps", "-o", "pid=", "--ppid", str(p)],
                                 capture_output=True, text=True, timeout=10).stdout
        except (subprocess.SubprocessError, OSError):
            return []
        pids = []
        for child in out.split():
            pids += descendants(int(child))
            pids.append(int(child))
        return pids

    all_pids = descendants(pid) + [pid]
    for p in all_pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not any(_alive(p) for p in all_pids):
            return
        time.sleep(1)
    for p in all_pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class VllmServer:
    """Ein laufender Probe-Server (immer via boot_vllm erzeugen)."""

    def __init__(self, proc: subprocess.Popen, port: int, served_name: str, log_path: Path):
        self.proc = proc
        self.port = port
        self.served_name = served_name
        self.log_path = log_path
        self.base_url = f"http://127.0.0.1:{port}"

    def shutdown(self) -> None:
        _kill_tree(self.proc.pid)
        self.proc.wait(timeout=30)

    def chat(self, prompt: str, max_tokens: int = 64, temperature: float = 0.0,
             ignore_eos: bool = False, timeout_s: float = 300.0) -> tuple[str, dict, float]:
        """Eine Chat-Completion; Rueckgabe (Text, usage-Dict, Dauer_s)."""
        body: dict = {
            "model": self.served_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if ignore_eos:
            body["ignore_eos"] = True
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            d = json.load(r)
        dt = time.monotonic() - t0
        return d["choices"][0]["message"]["content"], d["usage"], dt

    def metrics(self) -> dict[str, float]:
        """Prometheus-Counter als {name: Summe ueber Label-Saetze}."""
        import re
        with urllib.request.urlopen(f"{self.base_url}/metrics", timeout=10) as r:
            raw = r.read().decode()
        out: dict[str, float] = {}
        for line in raw.splitlines():
            if not line or line.startswith("#"):
                continue
            m = re.match(r"([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([0-9.eE+-]+)", line)
            if m:
                out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
        return out

    def log_tail(self, n_chars: int = 4000) -> str:
        if not self.log_path.exists():
            return ""
        text = self.log_path.read_text(errors="replace")
        return text[-n_chars:]


def boot_vllm(
    spec: VllmSpec,
    port: int,
    log_path: Path,
    timeout_s: int = 900,
    cancel_check=None,
) -> VllmServer:
    """Server booten und auf healthy warten.

    Raises VllmBootError wenn der Prozess stirbt (mit Log-Tail und, falls
    vLLM eine Kontext-Obergrenze nennt, dem geparsten Wert) oder das
    Timeout reisst.
    """
    runtime = load_vllm_runtime()
    if not (spec.checkpoint / "config.json").exists():
        raise VllmBootError(f"checkpoint has no config.json: {spec.checkpoint}")

    cmd = spec.build_cmd(runtime, port)
    env = spec.build_env(runtime)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        cmd, stdout=log_file, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=env, start_new_session=True,
    )
    server = VllmServer(proc, port, spec.served_name, log_path)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cancel_check is not None and cancel_check():
            server.shutdown()
            raise VllmBootError("cancelled by user")
        if proc.poll() is not None:
            # Grenzwert-Parsing ueber das GANZE Log: die entscheidende
            # ValueError-Zeile steht VOR den langen Folge-Tracebacks und
            # faellt aus einem reinen Tail-Fenster heraus (27B-Lauf
            # 2026-08-28: "estimated maximum model length" ungesehen).
            full_log = server.log_path.read_text(errors="replace") \
                if server.log_path.exists() else ""
            raise VllmBootError(
                f"server died during boot (exit {proc.returncode})",
                log_tail=full_log[-4000:],
                parsed_max_len=parse_vllm_max_context_from_error(full_log),
                oom=any(sig in full_log for sig in OOM_SIGNATURES),
            )
        try:
            with urllib.request.urlopen(f"{server.base_url}/health", timeout=3) as r:
                if r.status == 200:
                    return server
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(2)

    server.shutdown()
    raise VllmBootError(f"boot timeout after {timeout_s}s", log_tail=server.log_tail())


# --- Mess-Proben -----------------------------------------------------------

# Kohaerenz-Checks: Prompts BEWUSST laenger als eine QSA-Kompressionsgruppe
# (sehr kurze Prompts kippen bei QSA-Modellen unabhaengig von MTP — Handover
# 2026-08-28); erwartete Substrings sind temperatur-0-stabil.
COHERENCE_CHECKS: list[tuple[str, str]] = [
    ("Die Hauptstadt von Frankreich ist Paris. Die Hauptstadt von "
     "Deutschland ist welche Stadt? Antworte in einem Satz.", "Berlin"),
    ("Rechne Schritt fuer Schritt und nenne am Ende das Ergebnis: "
     "Was ist 37 mal 43?", "1591"),
    ("Schreibe eine Python-Funktion, die einen String umdreht. "
     "Nur der Code, keine Erklaerung.", "[::-1]"),
]

THROUGHPUT_PROMPT = (
    "Erklaere ausfuehrlich und fachlich praezise die Unterschiede zwischen "
    "Tensor-Parallelismus und Pipeline-Parallelismus bei der Inferenz "
    "grosser Sprachmodelle, einschliesslich der Kommunikationsmuster "
    "und Latenzeffekte."
)


def probe_coherence(server: VllmServer) -> tuple[int, int, list[str]]:
    """(bestanden, gesamt, Antwort-Snippets).

    max_tokens grosszuegig: Schritt-fuer-Schritt-Rechnungen brauchen Platz,
    sonst faellt ein kohaerentes Modell durch (27B-Probe 2026-08-28)."""
    passed = 0
    snippets = []
    for prompt, expect in COHERENCE_CHECKS:
        answer, _, _ = server.chat(prompt, max_tokens=500)
        snippets.append(answer.replace("\n", " ")[:80])
        if expect in answer:
            passed += 1
    return passed, len(COHERENCE_CHECKS), snippets


def probe_throughput(server: VllmServer, tokens: int = 200, runs: int = 2,
                     warmup: bool = True) -> list[float]:
    """tok/s je Lauf (Wall-Clock inkl. Prefill; Vergleichbarkeit zaehlt,
    nicht Absolutwert). Erster Lauf optional als Warmup verworfen."""
    if warmup:
        server.chat(THROUGHPUT_PROMPT, max_tokens=tokens, ignore_eos=True)
    results = []
    for _ in range(runs):
        _, usage, dt = server.chat(THROUGHPUT_PROMPT, max_tokens=tokens, ignore_eos=True)
        results.append(usage["completion_tokens"] / dt)
    return results


# Langkontext-Messpunkt: ~45 % des Kontextfensters fuellen (gedeckelt),
# damit Architektur-Unterschiede sichtbar werden, die der Kurzkontext
# verdeckt (RTX-Befund 2026-08-29: Attention-Kosten dominieren erst dort).
LONG_CONTEXT_TARGET_TOKENS = 30000
LONG_CONTEXT_MIN_TOKENS = 8192
LONG_CONTEXT_FILLER = (
    "Die Industrialisierung veraenderte Wirtschaft, Verkehr und Alltag "
    "in Europa grundlegend und dauerhaft. "
)


# Spekulations-Zaehler (Prometheus): Akzeptanzrate beim Lang-Decode ist
# die Diagnose-Groesse fuer den beobachteten Spekulations-Einbruch bei
# vollem Kontext (Kollaps der Akzeptanz vs. Kostenexplosion im Verify).
SPEC_DRAFT_METRIC = "vllm:spec_decode_num_draft_tokens_total"
SPEC_ACCEPT_METRIC = "vllm:spec_decode_num_accepted_tokens_total"


def probe_long_context(server: VllmServer, mml: int) -> dict | None:
    """Langkontext-Punkt als Dict: tokens, prefill_tps, decode_tps,
    accept_rate (Spekulations-Akzeptanz waehrend des Lang-Decodes;
    -1.0 wenn keine Spekulation laeuft oder Zaehler fehlen).

    Erster Call (max_tokens=1) misst den Prefill; der zweite Call mit
    identischem Prompt trifft den Prefix-Cache und misst den reinen
    Decode bei gefuelltem Kontext. None, wenn das Fenster fuer einen
    aussagekraeftigen Langpunkt zu klein ist.
    """
    target = min(LONG_CONTEXT_TARGET_TOKENS, int(mml * 0.45))
    if target < LONG_CONTEXT_MIN_TOKENS:
        return None
    # ~5,5 Zeichen je Token fuer deutschen Fuelltext
    repeats = max(1, int(target * 5.5) // len(LONG_CONTEXT_FILLER))
    prompt = (LONG_CONTEXT_FILLER * repeats
              + "\nFasse den Kern des Textes in einem Satz zusammen:")
    _, usage, dt1 = server.chat(prompt, max_tokens=1, ignore_eos=True,
                                timeout_s=1200.0)
    prompt_tokens = int(usage.get("prompt_tokens", 0)) or target
    prefill_tps = prompt_tokens / dt1 if dt1 > 0 else 0.0
    try:
        m_before = server.metrics()
    except Exception:  # noqa: BLE001 — Diagnose optional, Messung geht vor
        m_before = {}
    _, usage2, dt2 = server.chat(prompt, max_tokens=200, ignore_eos=True,
                                 timeout_s=1200.0)
    decode_tps = usage2["completion_tokens"] / dt2 if dt2 > 0 else 0.0
    accept_rate = -1.0
    if m_before:
        try:
            m_after = server.metrics()
            drafted = m_after.get(SPEC_DRAFT_METRIC, 0.0) - m_before.get(SPEC_DRAFT_METRIC, 0.0)
            accepted = m_after.get(SPEC_ACCEPT_METRIC, 0.0) - m_before.get(SPEC_ACCEPT_METRIC, 0.0)
            if drafted > 0:
                accept_rate = accepted / drafted
        except Exception:  # noqa: BLE001
            pass
    return {"tokens": prompt_tokens, "prefill_tps": prefill_tps,
            "decode_tps": decode_tps, "accept_rate": accept_rate}


def find_free_port(start: int = 8050, end: int = 8090) -> int:
    import socket
    for port in range(start, end):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free port in {start}-{end}")
