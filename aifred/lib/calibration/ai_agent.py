"""AI-driven calibration via DashScope/Qwen function-calling.

Where the algorithmic path in flow.py follows a fixed phase scheme
(KV-quant ladder, GPU-count search, then balance-check), this module
hands the search loop over to a Qwen model that can react to OOM
errors and unexpected VRAM patterns. The LLM sees a tool API:

  probe_config(ctx, tensor_split) → status + per-GPU free MB
  finalize(ctx, tensor_split, reasoning) → end the loop with a result

It iterates until ``finalize`` is called or ``MAX_PROBES`` is exceeded;
on errors or timeout the caller falls back to the legacy algorithm.

Cost: ~10 ¢ with qwen-plus, ~60 ¢ with qwen-max for one calibration
(15-probe cap, ~150 k input + ~25 k output cumulative).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from ..config import LLAMACPP_CALIBRATION_PORT
from ..credential_broker import broker
from .gpu import enumerate_gpus
from .llamaswap_io import parse_tensor_split, set_tensor_split
from .types import GPU
from .verifier import kill_orphan_on_port, verify

logger = logging.getLogger(__name__)


DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
# Safety net — manual calibration of similar configs hits the optimum
# in 3-6 probes, AI with OOM-recovery realistically lands in 8-12.
# 25 leaves ample room for unexpected OOM streaks without burning
# unbounded tokens if the agent fails to converge.
MAX_PROBES = 25
DEFAULT_HEALTH_TIMEOUT = 600.0  # large models with mlock take a while


# ─────────────────────────────────────────────────────────────────────────
# Tool API exposed to the LLM
# ─────────────────────────────────────────────────────────────────────────


CALIBRATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "estimate_config",
            "description": (
                "FAST (1-2 seconds, no model load): run llama-fit-params "
                "to project per-GPU VRAM usage analytically — same memory "
                "math the server uses, but without actually loading "
                "weights. Use this to quickly explore many configurations "
                "and narrow down candidates before spending a slow probe. "
                "Returns "
                '{"used_mb": [c0,c1,c2,c3], "free_mb": [c0,c1,c2,c3], '
                '"fits_safety_margin": bool}. Math projection only — '
                "doesn't account for CUDA kernel reservations and inference "
                "activations; verify the final candidate with probe_config."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ctx": {"type": "integer"},
                    "tensor_split": {"type": "string"},
                },
                "required": ["ctx", "tensor_split"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "probe_config",
            "description": (
                "SLOW (3-5 minutes for large models): start llama-server "
                "with the given context and tensor split, wait for it to "
                "load, run a short inference, then kill it and report "
                "per-GPU free MB. This is the ground-truth verification — "
                "use it sparingly on the best candidate(s) you found via "
                "estimate_config. Returns "
                '{"status": "ok"|"oom"|"load_failed", "free_mb": [c0,c1,c2,c3], '
                '"detail": "..."}. On OOM, free_mb may be empty.'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ctx": {
                        "type": "integer",
                        "description": "Context size in tokens (e.g. 100000)",
                    },
                    "tensor_split": {
                        "type": "string",
                        "description": (
                            "Comma-separated layer-distribution ratios per GPU, "
                            "e.g. '20,21,10,11' for 4 GPUs. The values are "
                            "relative — they don't have to sum to anything special."
                        ),
                    },
                },
                "required": ["ctx", "tensor_split"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize",
            "description": (
                "Finalize the calibration with the chosen ctx and tensor_split. "
                "Only call this when you've verified via probe_config that the "
                "configuration meets the safety-margin constraint on every GPU. "
                "After finalize, the loop terminates and the values are written "
                "to llama-swap config.yaml."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ctx": {"type": "integer"},
                    "tensor_split": {"type": "string"},
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Short explanation (1-2 sentences) why this is "
                            "the optimum given the constraints."
                        ),
                    },
                },
                "required": ["ctx", "tensor_split", "reasoning"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _ProbeOutcome:
    status: str            # "ok" | "oom" | "load_failed"
    free_mb: list[int]     # per-GPU; empty when status != "ok"
    detail: str


def _parse_split(text: str, n_gpus: int) -> Optional[list[float]]:
    """Parse '20,21,10,11' → [20, 21, 10, 11]. Returns None on bad format."""
    try:
        parts = [float(x.strip()) for x in text.split(",") if x.strip()]
    except ValueError:
        return None
    if len(parts) != n_gpus or any(p < 0 for p in parts):
        return None
    return parts


def _format_split(split: list[float] | tuple[float, ...]) -> str:
    return ",".join(str(int(round(v))) for v in split)


def _hardware_block(gpus: list[GPU]) -> str:
    lines = []
    for g in gpus:
        lines.append(
            f"  CUDA{g.cuda_id}: {g.name} — {g.total_mb} MB total, {g.free_mb} MB free"
        )
    return "\n".join(lines)


def _build_system_prompt(
    model_id: str,
    model_size_gb: float,
    native_ctx: int,
    total_layers: int,
    gpus: list[GPU],
    safety_margin_mb: int,
    seed_ctx: Optional[int],
    seed_split: Optional[list[float]],
    extra_constraints: str = "",
) -> str:
    """Load the calibration agent's system prompt and fill in runtime
    placeholders. The path is resolved from ``agents.json`` (calibration
    agent's ``prompts.system`` entry), so the user can edit the prompt
    file via the Agent Editor without code changes (CLAUDE.md rule: no
    hardcoded prompts in source).
    """
    from ..agent_config import load_agents_raw
    from ..prompt_loader import load_prompt

    cal_cfg = load_agents_raw().get("calibration") or {}
    prompt_path = (cal_cfg.get("prompts") or {}).get("system") or "calibration/system.txt"
    # load_prompt strips the .txt suffix internally
    prompt_name = prompt_path.removesuffix(".txt")

    seed_block = ""
    if seed_ctx and seed_split:
        seed_block = (
            f"\n\nA conservative algorithm has already determined a working "
            f"baseline: ctx={seed_ctx}, tensor_split={_format_split(seed_split)}. "
            f"Your job is to push beyond this — that algorithm leaves much VRAM "
            f"unused. Start by probing higher contexts and rebalancing the split "
            f"to even out free VRAM across GPUs."
        )

    extra = f"\n\n{extra_constraints}" if extra_constraints else ""

    # Force lang="en" — the prompt is for an LLM tool-use loop, not the
    # human UI. Tool-calling reasoning is more reliable on English
    # regardless of UI locale, and we only ship the EN file.
    return load_prompt(
        prompt_name,
        lang="en",
        safety_margin_mb=safety_margin_mb,
        model_id=model_id,
        model_size_gb=f"{model_size_gb:.1f}",
        total_layers=total_layers,
        native_ctx=native_ctx,
        hardware_block=_hardware_block(gpus),
        max_probes=MAX_PROBES,
        seed_block=seed_block,
        extra_constraints=extra,
    )


async def _pre_search_max_ctx(
    full_cmd: str,
    gguf_path: Path,
    gpus: list[GPU],
    safety_margin_mb: int,
    native_ctx: int,
    initial_split: list[float],
) -> tuple[int, list[float], str]:
    """Binary-search the largest math-projected ctx that fits, starting
    from the model's native context and shrinking down via fit-params.

    Each step is a 1-2 s fit-params call (no model load), so we can
    afford ~10 steps to converge on the math-maximum. The AI uses this
    as its seed, verifies it with one real probe, and finalizes.

    Returns ``(best_ctx, split_used, log_summary)``.
    """
    from . import projection as proj

    if native_ctx <= 0:
        return (0, initial_split, "native_ctx unknown — skipping pre-search")

    cmd = set_tensor_split(full_cmd, initial_split)
    n_gpus = len(gpus)

    # First check the native ctx directly — many small models fit it whole.
    try:
        point = await proj.project(cmd, gguf_path, native_ctx, ngl=99, n_gpus=n_gpus)
        free = list(point.per_gpu_free_mb)
        if free and min(free) >= safety_margin_mb:
            return (native_ctx, initial_split,
                    f"native ctx={native_ctx} already fits (min free={min(free)} MB)")
    except Exception as exc:
        logger.warning(f"pre-search native probe failed: {exc}")

    # Binary search: lo always-fits-by-construction (use a tiny start),
    # hi never-fits-as-checked.
    lo, hi = 4096, native_ctx
    best_ctx = 0
    iterations = 0
    while hi - lo > 4096 and iterations < 12:
        mid = ((lo + hi) // 2) // 1024 * 1024  # round down to 1k boundary
        if mid <= lo:
            break
        try:
            point = await proj.project(cmd, gguf_path, mid, ngl=99, n_gpus=n_gpus)
            free = list(point.per_gpu_free_mb)
        except Exception:
            free = []

        if free and min(free) >= safety_margin_mb:
            best_ctx = mid
            lo = mid
        else:
            hi = mid
        iterations += 1

    if best_ctx == 0:
        return (0, initial_split, f"pre-search found no fitting ctx in [{lo},{hi}]")

    return (best_ctx, initial_split,
            f"binary-searched in {iterations} iterations from native={native_ctx}")


async def _do_estimate(
    full_cmd: str,
    gguf_path: Path,
    ctx: int,
    tensor_split: list[float],
    gpus: list[GPU],
    safety_margin_mb: int,
) -> _ProbeOutcome:
    """Fast math projection via llama-fit-params (no model load).

    Returns a _ProbeOutcome where ``status`` is ``"estimate_ok"`` /
    ``"estimate_tight"`` / ``"estimate_oom"`` so the AI can distinguish
    a math projection from a real probe.
    """
    from . import projection as proj

    cmd = set_tensor_split(full_cmd, tensor_split)
    try:
        point = await proj.project(cmd, gguf_path, ctx, ngl=99, n_gpus=len(gpus))
    except Exception as exc:
        logger.exception("project() raised during estimate")
        return _ProbeOutcome(status="estimate_failed", free_mb=[], detail=str(exc))

    free_list = list(point.per_gpu_free_mb)
    used_list = list(point.per_gpu_used_mb)
    if not free_list or all(f == 0 for f in free_list):
        return _ProbeOutcome(
            status="estimate_failed",
            free_mb=[],
            detail="fit-params returned no per-GPU data",
        )
    min_free = min(free_list)
    if min_free < 0:
        status = "estimate_oom"
    elif min_free < safety_margin_mb:
        status = "estimate_tight"
    else:
        status = "estimate_ok"
    detail = ", ".join(
        f"{g.name}: used={used_list[i]}MB free={free_list[i]}MB"
        for i, g in enumerate(gpus) if i < len(free_list)
    )
    return _ProbeOutcome(status=status, free_mb=free_list, detail=detail)


async def _do_probe(
    full_cmd: str,
    ctx: int,
    tensor_split: list[float],
    port: int,
    gpus: list[GPU],
    safety_margin_mb: int,
    env: Optional[dict[str, str]],
) -> _ProbeOutcome:
    """Run a single physical probe and translate the result to a tool payload."""
    cmd = set_tensor_split(full_cmd, tensor_split)
    try:
        result = await verify(
            full_cmd=cmd,
            context=ctx,
            port=port,
            gpus=gpus,
            safety_margin_mb=safety_margin_mb,
            env=env,
            health_timeout=DEFAULT_HEALTH_TIMEOUT,
        )
    except Exception as exc:
        logger.exception("verify() raised during AI calibration probe")
        return _ProbeOutcome(status="load_failed", free_mb=[], detail=str(exc))

    if not result.measured_free_mb:
        # Hard OOM — server failed to start or load the model entirely.
        return _ProbeOutcome(status="oom", free_mb=[], detail=result.detail)

    # Soft case: server loaded and inference ran, but the tightest GPU is
    # below the configured safety margin. The AI gets the real numbers
    # and can decide whether to back off ctx slightly or rebalance.
    return _ProbeOutcome(
        status="ok" if result.fits else "tight",
        free_mb=list(result.measured_free_mb),
        detail=result.detail,
    )


def _payload_from_outcome(outcome: _ProbeOutcome) -> str:
    """Serialize for the tool-result message body."""
    return json.dumps({
        "status": outcome.status,
        "free_mb": outcome.free_mb,
        "detail": outcome.detail,
    }, ensure_ascii=False)


def _validate_finalize(
    ctx: int,
    split: Optional[list[float]],
    last_outcome: Optional[_ProbeOutcome],
    safety_margin_mb: int,
) -> Optional[str]:
    """Return error message if finalize values are unfit, else None."""
    if ctx <= 0:
        return f"Invalid ctx: {ctx}"
    if split is None:
        return "Invalid tensor_split format"
    if last_outcome is None or last_outcome.status not in ("ok", "tight"):
        return "No successful probe matches the finalized config"
    if not last_outcome.free_mb:
        return "Last probe has no VRAM measurement"
    min_free = min(last_outcome.free_mb)
    if min_free < safety_margin_mb:
        return (
            f"Finalized config violates safety margin: "
            f"min_free={min_free} MB < {safety_margin_mb} MB"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


async def calibrate_with_ai(
    model_id: str,
    full_cmd: str,
    safety_margin_mb: int,
    gguf_path: Optional[Path] = None,
    seed_ctx: Optional[int] = None,
    seed_split: Optional[list[float]] = None,
    qwen_model: Optional[str] = None,
    port: int = LLAMACPP_CALIBRATION_PORT,
    env: Optional[dict[str, str]] = None,
    extra_constraints: str = "",
    model_size_mb: Optional[float] = None,
    native_ctx: Optional[int] = None,
    total_layers: Optional[int] = None,
    allow_hybrid: bool = False,
) -> AsyncIterator[str]:
    """AI-driven calibration loop. Yields progress strings.

    The Qwen model is read from the ``calibration`` system agent in
    ``agents.json`` when ``qwen_model`` is not given — that's where the
    user can edit it via the Agent Editor.

    On success the last yielded line is
    ``__AI_RESULT__:{ctx}:{ts_csv}:{reasoning}``.
    On failure ``__AI_ERROR__:{reason}`` — the caller is expected to fall
    back to the legacy algorithm.
    """
    try:
        from openai import AsyncOpenAI
        import openai as _openai
    except ImportError:
        yield "__AI_ERROR__:openai package not installed"
        return

    api_key = broker.get("cloud_qwen", "api_key") or os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        yield "__AI_ERROR__:DashScope API key missing"
        return

    # Read model + reasoning toggle from the calibration system agent in
    # agents.json (editable via the Agent Editor). Reasoning costs extra
    # time per turn (30-120 s on Qwen-Plus) — for the focused decisions
    # this loop makes ("more ctx" / "rebalance split"), it's often
    # overkill, so the default is OFF.
    enable_thinking = False
    try:
        from ..agent_config import load_agents_raw
        cal_cfg = load_agents_raw().get("calibration") or {}
        if qwen_model is None:
            qwen_model = cal_cfg.get("model") or "qwen-plus"
        enable_thinking = bool((cal_cfg.get("toggles") or {}).get("reasoning", False))
    except Exception:
        if qwen_model is None:
            qwen_model = "qwen-plus"

    await kill_orphan_on_port(port)
    gpus = enumerate_gpus()
    if not gpus:
        yield "__AI_ERROR__:No GPUs detected"
        return

    # Seed extraction if not passed
    if seed_split is None:
        parsed = parse_tensor_split(full_cmd)
        seed_split = parsed if parsed else None

    yield f"🤖 AI calibration with {qwen_model} (max {MAX_PROBES} probes, safety margin {safety_margin_mb} MB)"

    # ─────────────────────────────────────────────────────────────────
    # Pre-search via fit-params: binary-search from native ctx down to
    # find the largest math-projected ctx that fits with the seed split.
    # Hands the AI a strong starting point — typically saves 3-5 probes.
    # ─────────────────────────────────────────────────────────────────
    pre_search_block = ""
    if gguf_path and native_ctx and seed_split:
        yield "🧮 Pre-searching via fit-params (math projection, no model load)..."
        searched_ctx, searched_split, search_log = await _pre_search_max_ctx(
            full_cmd=full_cmd,
            gguf_path=gguf_path,
            gpus=gpus,
            safety_margin_mb=safety_margin_mb,
            native_ctx=native_ctx,
            initial_split=seed_split,
        )
        if searched_ctx > 0:
            yield f"   -> math projection suggests ctx={searched_ctx} ({search_log})"
            seed_ctx = searched_ctx
            seed_split = searched_split
            pre_search_block = (
                f"\n\nMATH PROJECTION (via llama-fit-params, no model load): "
                f"largest ctx that fits with split {_format_split(searched_split)} "
                f"is approximately {searched_ctx}. Verify with one probe_config call, "
                f"then iterate: rebalance layers across GPUs for even free-VRAM "
                f"distribution, then push ctx higher if there's headroom. "
                f"Use estimate_config liberally to explore without spending probes."
            )
        elif not allow_hybrid:
            # Pre-search found nothing AND hybrid is disabled: there's no
            # GPU-only solution. Fail fast instead of letting the AI burn
            # through 25 OOM probes trying.
            yield f"   -> {search_log}"
            yield (
                "❌ Model does not fit on the available GPUs. Hybrid mode "
                "is disabled — calibration cannot proceed."
            )
            yield "💡 Enable the Hybrid toggle to allow CPU offload."
            yield "__AI_ERROR__:no GPU-only fit and hybrid disabled"
            return
        else:
            yield f"   -> {search_log}"

    hybrid_constraint = (
        ""
        if allow_hybrid
        else (
            "\n\nIMPORTANT: Hybrid mode (CPU offload of layers, ngl<99) is "
            "DISABLED for this calibration. You must find a GPU-only "
            "configuration. If after several probes no GPU-only fit exists, "
            "call finalize with the best partial result and explain in the "
            "reasoning that the model exceeds available GPU VRAM — the user "
            "will then enable the Hybrid toggle if needed."
        )
    )

    system_prompt = _build_system_prompt(
        model_id=model_id,
        model_size_gb=(model_size_mb or 0) / 1024,
        native_ctx=native_ctx or 0,
        total_layers=total_layers or 0,
        gpus=gpus,
        safety_margin_mb=safety_margin_mb,
        seed_ctx=seed_ctx,
        seed_split=seed_split,
        extra_constraints=extra_constraints + pre_search_block + hybrid_constraint,
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Start the calibration."},
    ]

    client = AsyncOpenAI(api_key=api_key, base_url=DASHSCOPE_BASE_URL)
    last_outcome: Optional[_ProbeOutcome] = None
    last_ctx: Optional[int] = None
    last_split: Optional[list[float]] = None
    probe_count = 0

    for turn in range(MAX_PROBES + 5):  # extra slack for non-tool messages
        # The AI always reasons between turns, even without enable_thinking
        # (chain-of-thought just makes it more thorough + slower).
        if turn == 0:
            yield "🧠 AI is reasoning (initial turn — may take up to 2 min)..."
        else:
            yield "🧠 AI is reasoning..."
        try:
            response = await client.chat.completions.create(
                model=qwen_model,
                messages=messages,
                tools=CALIBRATION_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                extra_body={"enable_thinking": enable_thinking},
            )
        except _openai.OpenAIError as exc:
            yield f"__AI_ERROR__:DashScope API error: {exc}"
            return
        except Exception as exc:
            yield f"__AI_ERROR__:Unexpected error: {exc}"
            return

        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []

        # Mirror the assistant message back into the conversation
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ] if tool_calls else None,
        })

        if not tool_calls:
            preview = (msg.content or "").strip().splitlines()[:1]
            line = preview[0] if preview else "(empty)"
            yield f"⚠️ AI returned no tool call: {line[:120]}"
            yield "__AI_ERROR__:Loop ended without finalize"
            return

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                yield f"⚠️ AI returned invalid JSON for {name}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": "Invalid JSON arguments"}),
                })
                continue

            if name == "estimate_config":
                ctx = int(args.get("ctx", 0))
                split_str = str(args.get("tensor_split", ""))
                split = _parse_split(split_str, len(gpus))
                if ctx <= 0 or split is None:
                    msg_back = f"Bad arguments: ctx={ctx}, tensor_split={split_str!r}"
                    yield f"⚠️ {msg_back}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": msg_back}),
                    })
                    continue

                if not gguf_path:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "error": "estimate_config unavailable: gguf_path missing",
                        }),
                    })
                    continue

                yield f"🧮 Estimate: ctx={ctx}, split={_format_split(split)}"
                est = await _do_estimate(
                    full_cmd, gguf_path, ctx, split, gpus, safety_margin_mb,
                )
                yield f"   -> {est.status}: {est.detail}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _payload_from_outcome(est),
                })
                continue

            if name == "probe_config":
                if probe_count >= MAX_PROBES:
                    yield "⚠️ Maximum probe count reached"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "error": f"Max {MAX_PROBES} probes exhausted — call finalize now",
                        }),
                    })
                    continue

                ctx = int(args.get("ctx", 0))
                split_str = str(args.get("tensor_split", ""))
                split = _parse_split(split_str, len(gpus))
                if ctx <= 0 or split is None:
                    msg_back = f"Bad arguments: ctx={ctx}, tensor_split={split_str!r}"
                    yield f"⚠️ {msg_back}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": msg_back}),
                    })
                    continue

                probe_count += 1
                yield f"🔬 Probe #{probe_count}: ctx={ctx}, split={_format_split(split)}"

                outcome = await _do_probe(
                    full_cmd, ctx, split, port, gpus, safety_margin_mb, env,
                )
                last_outcome = outcome
                last_ctx = ctx
                last_split = split

                yield f"   -> {outcome.status}: {outcome.detail}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _payload_from_outcome(outcome),
                })

            elif name == "finalize":
                ctx = int(args.get("ctx", 0))
                split_str = str(args.get("tensor_split", ""))
                reasoning = str(args.get("reasoning", ""))
                split = _parse_split(split_str, len(gpus))

                # Verify finalize matches a successful probe
                same_as_last = (
                    last_ctx == ctx
                    and last_split is not None
                    and split is not None
                    and len(last_split) == len(split)
                    and all(abs(a - b) < 0.001 for a, b in zip(last_split, split))
                )
                if not same_as_last:
                    yield "⚠️ AI finalized with a config not matching the last probe — re-probing"
                    if ctx > 0 and split is not None and probe_count < MAX_PROBES:
                        probe_count += 1
                        outcome = await _do_probe(
                            full_cmd, ctx, split, port, gpus, safety_margin_mb, env,
                        )
                        last_outcome = outcome
                        last_ctx = ctx
                        last_split = split
                        yield f"   -> {outcome.status}: {outcome.detail}"
                    else:
                        yield "__AI_ERROR__:finalize without a verified probe"
                        return

                err = _validate_finalize(ctx, split, last_outcome, safety_margin_mb)
                if err:
                    yield f"⚠️ Finalize validation: {err}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": err}),
                    })
                    continue

                assert split is not None  # validated above
                yield f"✅ Final: ctx={ctx}, split={_format_split(split)}"
                if reasoning:
                    yield f"   💭 {reasoning}"
                yield f"__AI_RESULT__:{ctx}:{_format_split(split)}:{reasoning}"
                return

            else:
                yield f"⚠️ Unknown tool call: {name}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": f"Unknown tool: {name}"}),
                })

    yield "__AI_ERROR__:Loop limit reached without finalize"
