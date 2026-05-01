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
from typing import AsyncIterator, Optional

from ..config import LLAMACPP_CALIBRATION_PORT
from ..credential_broker import broker
from .gpu import enumerate_gpus
from .llamaswap_io import parse_tensor_split, set_tensor_split
from .types import GPU
from .verifier import kill_orphan_on_port, verify

logger = logging.getLogger(__name__)


DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
MAX_PROBES = 15
DEFAULT_HEALTH_TIMEOUT = 600.0  # large models with mlock take a while


# ─────────────────────────────────────────────────────────────────────────
# Tool API exposed to the LLM
# ─────────────────────────────────────────────────────────────────────────


CALIBRATION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "probe_config",
            "description": (
                "Start llama-server with the given context size and tensor "
                "split, wait for it to load, run a short inference, then "
                "kill it and report per-GPU free MB. Returns "
                '{"status": "ok"|"oom"|"load_failed", "free_mb": [c0, c1, c2, c3], '
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
    """Render prompts/{lang}/utility/calibration_agent.txt with the runtime
    placeholders. The template lives in prompts/ so it can be tuned without
    touching code (CLAUDE.md rule: no hardcoded prompts in source).
    """
    from ..prompt_loader import load_prompt

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

    return load_prompt(
        "utility/calibration_agent",
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

    if not result.fits and not result.measured_free_mb:
        return _ProbeOutcome(status="oom", free_mb=[], detail=result.detail)

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
    seed_ctx: Optional[int] = None,
    seed_split: Optional[list[float]] = None,
    qwen_model: str = "qwen-plus",
    port: int = LLAMACPP_CALIBRATION_PORT,
    env: Optional[dict[str, str]] = None,
    extra_constraints: str = "",
    model_size_mb: Optional[float] = None,
    native_ctx: Optional[int] = None,
    total_layers: Optional[int] = None,
) -> AsyncIterator[str]:
    """AI-driven calibration loop. Yields progress strings.

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
        yield "__AI_ERROR__:DashScope API-Key fehlt"
        return

    await kill_orphan_on_port(port)
    gpus = enumerate_gpus()
    if not gpus:
        yield "__AI_ERROR__:Keine GPUs erkannt"
        return

    # Seed extraction if not passed
    if seed_split is None:
        parsed = parse_tensor_split(full_cmd)
        seed_split = parsed if parsed else None

    yield f"🤖 KI-Calibration mit {qwen_model} (max {MAX_PROBES} Probes, Polster {safety_margin_mb} MB)"

    system_prompt = _build_system_prompt(
        model_id=model_id,
        model_size_gb=(model_size_mb or 0) / 1024,
        native_ctx=native_ctx or 0,
        total_layers=total_layers or 0,
        gpus=gpus,
        safety_margin_mb=safety_margin_mb,
        seed_ctx=seed_ctx,
        seed_split=seed_split,
        extra_constraints=extra_constraints,
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
        try:
            response = await client.chat.completions.create(
                model=qwen_model,
                messages=messages,
                tools=CALIBRATION_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                extra_body={"enable_thinking": True},
            )
        except _openai.OpenAIError as exc:
            yield f"__AI_ERROR__:DashScope API-Fehler: {exc}"
            return
        except Exception as exc:
            yield f"__AI_ERROR__:Unerwarteter Fehler: {exc}"
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
            line = preview[0] if preview else "(leer)"
            yield f"⚠️ KI ohne Tool-Call: {line[:120]}"
            yield "__AI_ERROR__:Loop beendet ohne finalize"
            return

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                yield f"⚠️ KI lieferte ungültiges JSON in {name}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": "Invalid JSON arguments"}),
                })
                continue

            if name == "probe_config":
                if probe_count >= MAX_PROBES:
                    yield "⚠️ Maximum probe count erreicht"
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

                yield f"   → {outcome.status}: {outcome.detail}"

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
                    yield "⚠️ KI finalisiert nicht mit zuletzt geprobter Config — re-probing"
                    if ctx > 0 and split is not None and probe_count < MAX_PROBES:
                        probe_count += 1
                        outcome = await _do_probe(
                            full_cmd, ctx, split, port, gpus, safety_margin_mb, env,
                        )
                        last_outcome = outcome
                        last_ctx = ctx
                        last_split = split
                        yield f"   → {outcome.status}: {outcome.detail}"
                    else:
                        yield "__AI_ERROR__:finalize ohne verifizierte Probe"
                        return

                err = _validate_finalize(ctx, split, last_outcome, safety_margin_mb)
                if err:
                    yield f"⚠️ Finalize-Validation: {err}"
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
                yield f"⚠️ Unbekannter Tool-Call: {name}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": f"Unknown tool: {name}"}),
                })

    yield "__AI_ERROR__:Loop-Limit erreicht ohne finalize"
