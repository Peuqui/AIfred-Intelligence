"""
LLM Pipeline — Unified chunk-processing core for all LLM calls.

Provides a single AsyncGenerator that wraps llm_client.chat_stream() with:
- 500-error retry (1 attempt, 2s delay)
- TTFT measurement
- URL tracking (web_fetch calls)
- Sandbox output extraction
- Tool-call JSON stripping
- Thinking block processing
- Inference metadata building

Consumers (Chat UI, Message Hub) process the yielded events
and add their own concerns (streaming to UI, TTS, debug routing).
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator, Callable, Optional

from .config import DEBUG_LOG_RAW_OUTPUT
from .context_manager import estimate_tokens, strip_thinking_blocks
from .bubble import (
    KIND_SANDBOX_HTML,
    KIND_SANDBOX_IMAGE,
    KIND_SOURCES,
    KIND_VISION_IMAGES,
    KIND_VLM_OUTPUT,
    BubbleArtifact,
    image_markdown,
)
from .formatting import build_inference_metadata
from .logging_utils import log_message, log_raw_messages
from .perf_metrics import InferenceWork
from .timer import Timer

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from ..backends.base import LLMOptions


@dataclass
class PipelineResult:
    """Final result from run_llm_stream() — everything a consumer needs."""

    text: str = ""                                      # Full response (incl. <think> blocks)
    text_clean: str = ""                                # Response without thinking
    metadata_dict: dict[str, Any] = field(default_factory=dict)
    metadata_display: str = ""                          # Metadata string for UI
    debug_msg: str = ""                                 # Metadata debug line
    metrics: dict[str, Any] = field(default_factory=dict)
    ttft: float = 0.0
    inference_time: float = 0.0
    thinking_time: float = 0.0                          # all think blocks of the turn, sub-agents included
    tokens_per_sec: float = 0.0
    # Everything the turn's tools produced for the bubble, each with its
    # offset in ``text`` (see lib/bubble.py, rendered by render_bubble).
    artifacts: list[BubbleArtifact] = field(default_factory=list)
    silent_reply: bool = False                          # any tool requested TTS-skip
    truncated: bool = False                             # hit token/context limit — answer incomplete


# Anchor of a bubble artifact inside full_response while the turn streams.
# Post-processing prepends and strips text, so a character offset taken at
# event time would drift; the anchor moves with the text and becomes the final
# offset right before the result is built (and is removed from the text).
_ARTIFACT_ANCHOR = "\x00bubble-artifact-{}\x00"
_ARTIFACT_ANCHOR_RE = re.compile(r"\x00bubble-artifact-(\d+)\x00")


def resolve_artifact_anchors(text: str) -> tuple[str, dict[int, int]]:
    """Remove the artifact anchors from ``text``; return the clean text and
    the offset in it for every anchor index."""
    offsets: dict[int, int] = {}
    parts: list[str] = []
    last = 0
    for match in _ARTIFACT_ANCHOR_RE.finditer(text):
        parts.append(text[last:match.start()])
        offsets[int(match.group(1))] = sum(len(p) for p in parts)
        last = match.end()
    parts.append(text[last:])
    return "".join(parts), offsets


def strip_tool_json(text: str) -> str:
    """Remove store_memory JSON from response text (fallback tool-call artifact)."""
    return re.sub(
        r'\{\s*"content"\s*:\s*"[^"]+"\s*,\s*"memory_type"\s*:\s*"[^"]+"\s*,\s*"summary"\s*:\s*"[^"]+"[^}]*\}',
        "", text,
    ).strip()


def _dedup_injected_images(text: str, urls: list[str]) -> str:
    """Die Pipeline stellt jedem Kamerabild des Turns eine Referenz
    ``![alias](url)`` voran — die einzige Stelle, an der die URL im Verlauf
    bleibt (vision_analyze kann sie in späteren Turns erneut ansehen). Hat das
    LLM dieselbe image_url ein zweites Mal gerendert, bleibt pro URL nur das
    ERSTE ``![...](url)`` stehen. Angezeigt werden die Bilder über ihre
    Artefakte (render_bubble), nicht über diese Referenzen."""
    for url in urls:
        pattern = re.compile(r"!\[[^\]]*\]\(" + re.escape(url) + r"\)")
        matches = list(pattern.finditer(text))
        if len(matches) > 1:
            # Alle außer dem ersten entfernen — rückwärts, damit die vorigen
            # Match-Indizes gültig bleiben.
            for m in reversed(matches[1:]):
                text = text[: m.start()] + text[m.end():]
    return text


def _normalize_image_urls(text: str) -> str:
    """Erzwingt den absoluten Serving-Pfad für eingebettete Bilder. Das LLM
    lässt beim Abtippen einer Bild-URL gelegentlich den führenden Slash weg
    (``_upload/…`` statt ``/_upload/…``). Eine relative URL löst der Browser
    gegen den aktuellen Seitenpfad auf und bricht nach einem Reload (→ 404,
    z.B. ``/aifred/_upload/…``). Wir setzen den Slash für den bekannten
    Serving-Prefix ``_upload/`` in Markdown-Bildern deterministisch zurück,
    damit die Anzeige nicht von fehlerfreiem LLM-Kopieren abhängt.

    Außerdem werden Platzhalter-Bilder entfernt, die das LLM gelegentlich
    wörtlich aus der Anleitung übernimmt (z.B. ``![Kamera HH:MM](image_url)``):
    eine echte Serving-URL enthält immer einen Slash; eine ohne ist bogus und
    rendert als kaputtes Bild."""
    text = re.sub(r"(!\[[^\]]*\]\()_upload/", r"\1/_upload/", text)
    # Markdown-Bilder mit slash-loser (= unechter) URL streichen.
    text = re.sub(r"!\[[^\]]*\]\([^)/\n]*\)", "", text)
    return text




async def chat_stream_with_retry(
    llm_client: 'LLMClient',
    model: str,
    messages: list,
    options: 'LLMOptions',
    agent_label: str,
    toolkit: Any = None,
    on_debug: Callable[[str], None] | None = None,
    retry_delay: float = 2.0,
    max_retries: int = 1,
) -> AsyncGenerator[dict, None]:
    """Wrapper around llm_client.chat_stream() with retry logic for 500 errors.

    On 500 error: Logs the error, waits retry_delay seconds, retries once.
    If still fails, re-raises the error.
    """
    attempt = 0
    # Once a chunk has been forwarded to the caller, retrying would duplicate
    # content downstream (chat history, TTS, browser). Bail out instead.
    yielded_any = False

    while attempt <= max_retries:
        try:
            async for chunk in llm_client.chat_stream(model, messages, options, toolkit=toolkit):
                yield chunk
                yielded_any = True
            return
        except Exception as e:
            error_str = str(e)
            is_500_error = "500" in error_str and ("Internal Server Error" in error_str or "Server error" in error_str)

            if is_500_error and attempt < max_retries and not yielded_any:
                log_message(f"⚠️ {agent_label}: 500 Error - retrying in {retry_delay}s...")
                if on_debug:
                    on_debug(f"⚠️ {agent_label}: 500 Error (attempt {attempt + 1}/{max_retries + 1}) - {error_str}")

                await asyncio.sleep(retry_delay)
                attempt += 1
            else:
                raise


async def run_llm_stream(
    llm_client: 'LLMClient',
    model: str,
    messages: list,
    options: 'LLMOptions',
    agent_label: str,
    toolkit: Any = None,
    retry: bool = True,
    on_debug: Callable[[str], None] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Unified LLM chunk-processing pipeline.

    Wraps llm_client.chat_stream() with retry, tracking, and metadata.
    Yields the same chunk types as chat_stream() (passthrough), plus:
    - {"type": "ttft", "value": float} — after first content token
    - {"type": "pipeline_result", "result": PipelineResult} — after stream ends

    Args:
        llm_client: LLM client instance
        model: Model ID
        messages: Message list (system + history + user)
        options: LLM options (temperature, num_ctx, etc.)
        agent_label: Display label for logs (e.g. "AIfred", "Sokrates")
        toolkit: Optional toolkit with tools
        retry: Enable 500-error retry (default True)
        on_debug: Optional debug callback (e.g. state.add_debug)
    """
    # Raw debug logging
    log_raw_messages(f"{agent_label} (stream)", messages, estimate_tokens, toolkit=toolkit)

    # Denkstufe sichtbar machen — sie ist KEIN Modell-Schalter, sondern eine
    # Jinja-Variable der Chat-Vorlage. Fehlt sie, setzt die Vorlage ihren
    # eigenen Standard ein (Qwen3.8: xhigh, also die teuerste Stufe). Ein
    # stillschweigend leerer Wert sieht deshalb aus wie "maximal nachdenken".
    # Dieselbe Zeile steht im Backend als Logdatei-Eintrag; hier geht sie
    # zusaetzlich in die Debug-Konsole, weil Peuqui sie dort sehen will.
    if on_debug and options.enable_thinking is not False:
        level = options.reasoning_effort or "NOT SENT (template default applies)"
        on_debug(f"🧠 {agent_label} reasoning_effort: {level}")

    timer = Timer()
    full_response = ""
    token_count = 0
    first_token = False
    ttft = 0.0
    metrics: dict[str, Any] = {}
    silent_reply = False  # set True if any tool_result has silent_reply
    # Bubble artifacts in turn order (lib/bubble.py). Each gets an anchor in
    # full_response where its tool ran; post-processing turns anchors into
    # offsets.
    artifacts: list[BubbleArtifact] = []
    artifact_keys: set[str] = set()
    shown_image_urls: set[str] = set()
    # The turn's own web_fetch calls share one sources block; the entry of
    # the call in flight gets its success flag from the next tool result.
    own_sources: Optional[BubbleArtifact] = None
    pending_fetch: Optional[dict[str, Any]] = None

    def add_artifact(artifact: BubbleArtifact) -> None:
        nonlocal full_response
        key = artifact.key()
        if key is not None:
            if key in artifact_keys:
                return
            artifact_keys.add(key)
        if artifact.kind == KIND_VISION_IMAGES:
            # Each camera image once per turn (snapshot, then analyze of the
            # same frame, would show it twice).
            urls = [u for u in artifact.data["urls"] if u not in shown_image_urls]
            if not urls:
                return
            shown_image_urls.update(urls)
            artifact = BubbleArtifact(KIND_VISION_IMAGES, {**artifact.data, "urls": urls})
        full_response += _ARTIFACT_ANCHOR.format(len(artifacts))
        artifacts.append(artifact)

    def source_alias(source_id: Any) -> str:
        try:
            from .vision_utils import resolve_source_alias
            return str(resolve_source_alias(str(source_id), fallback="Snapshot"))
        except Exception:  # noqa: BLE001
            return "Snapshot"
    # image_url → (label, VLM-Beschreibung) aus vision_query_events. Wird im
    # Post-Processing als <image_descriptions>-Collapsible über die tatsächlich
    # gezeigten Event-Bilder gehängt.
    event_descriptions: dict[str, tuple[str, str]] = {}

    # Select stream source: with or without retry
    stream: AsyncIterator[dict[str, Any]]
    if retry:
        stream = chat_stream_with_retry(
            llm_client, model, messages, options, agent_label,
            toolkit=toolkit, on_debug=on_debug,
        )
    else:
        stream = llm_client.chat_stream(model, messages, options, toolkit=toolkit)

    async for chunk in stream:
        chunk_type = chunk["type"]

        if chunk_type == "content":
            if not first_token:
                ttft = timer.elapsed()
                first_token = True
                log_message(f"⚡ {agent_label} TTFT: {ttft:.2f}s")
                yield {"type": "ttft", "value": ttft}

            full_response += chunk["text"]
            token_count += 1
            yield chunk  # passthrough

        elif chunk_type == "tool_call_start":
            yield chunk

        elif chunk_type == "tool_call":
            tool_name = chunk.get("name", "")
            full_args = chunk.get("arguments", "")
            shown_args = full_args if DEBUG_LOG_RAW_OUTPUT else full_args[:200]
            log_message(f"🔧 Tool call: {tool_name}({shown_args})")

            # Track web_fetch URLs for the turn's sources block
            if tool_name == "web_fetch":
                try:
                    tool_args = json.loads(full_args) if full_args else {}
                except (ValueError, json.JSONDecodeError):
                    tool_args = {}
                pending_fetch = {"url": tool_args.get("url", ""), "success": None}
                if own_sources is None:
                    own_sources = BubbleArtifact(KIND_SOURCES, {"urls": []})
                    add_artifact(own_sources)
                own_sources.data["urls"].append(pending_fetch)

            yield chunk

        elif chunk_type == "tool_progress":
            # Streaming tools (web_search, search_documents, …) emit progress
            # lines while they run. Forward them so consumers can update the
            # UI immediately instead of seeing a debug-block at tool end.
            yield chunk

        elif chunk_type == "tool_artifacts":
            # Bubble artifacts delivered by a tool (a sub-agent's transcript
            # and everything its own tools produced), placed where the tool
            # ran in this turn.
            for raw in chunk.get("artifacts", []):
                add_artifact(BubbleArtifact.from_dict(raw))
            yield chunk

        elif chunk_type == "tool_result":
            result_text = chunk.get("result", "")
            if DEBUG_LOG_RAW_OUTPUT:
                log_message(f"🔧 Tool result: {result_text}")

            # Auto-extract VLM raw output → <vlm_output> tag in full_response.
            # The vision_analyze tool puts the VLM description under "vlm_raw"
            # in its JSON response specifically so we can prepend it here as
            # a collapsible — same mechanism as <think>, controlled by the
            # system, not by the LLM's text formatting choices.
            # The tool also returns "vlm_stats" with TTFT/tok-per-s/etc., so
            # we build a metrics footer to mirror the chat-bubble layout
            # (description text, then an italic stats line in parentheses).
            if result_text and "vlm_raw" in result_text:
                try:
                    parsed = json.loads(result_text)
                    if isinstance(parsed, dict):
                        vlm_text = parsed.get("vlm_raw", "")
                        vlm_stats = parsed.get("vlm_stats", {}) or {}
                        vlm_model = parsed.get("model", "")
                        image_url = parsed.get("image_url", "")
                        vlm_source_id = parsed.get("source_id", "")
                        if isinstance(vlm_text, str) and vlm_text.strip():
                            # Reuse build_inference_metadata so the VLM
                            # bubble footer + debug console line look
                            # identical to the chat-LLM ones (locale-aware
                            # number formatting included).
                            _, vlm_meta_display, vlm_debug_msg = build_inference_metadata(
                                ttft=float(vlm_stats.get("ttft_s") or 0) or None,
                                inference_time=float(vlm_stats.get("inference_s") or 0),
                                tokens_generated=int(vlm_stats.get("eval_tokens") or 0),
                                tokens_per_sec=float(vlm_stats.get("eval_tok_per_s") or 0),
                                source=f"VL ({vlm_model})" if vlm_model else "VL",
                                backend_metrics={
                                    "prompt_per_second": float(
                                        vlm_stats.get("pp_tok_per_s") or 0
                                    ),
                                },
                                tokens_prompt=int(vlm_stats.get("prompt_tokens") or 0),
                                backend_type="ollama",
                                agent_label="👁️ VLM",
                            )
                            body = vlm_text.strip()
                            if vlm_meta_display:
                                body += f"\n\n{vlm_meta_display}"
                            # The analysed image (if not shown yet this turn),
                            # then the VLM's description of it. Alt text is the
                            # user-given camera alias (e.g. "Türkamera").
                            if isinstance(image_url, str) and image_url:
                                add_artifact(BubbleArtifact(
                                    KIND_VISION_IMAGES,
                                    {"urls": [image_url], "alt": source_alias(vlm_source_id)},
                                ))
                            add_artifact(BubbleArtifact(KIND_VLM_OUTPUT, {"body": body}))
                            if vlm_debug_msg:
                                yield {"type": "debug", "message": vlm_debug_msg}
                except (ValueError, json.JSONDecodeError):
                    pass

            # vision_snapshot (no VLM) returns image_url without vlm_raw, and
            # for a burst all frames in image_urls — all of them are shown.
            if (
                result_text
                and '"image_url"' in result_text
                and '"vlm_raw"' not in result_text
            ):
                try:
                    parsed = json.loads(result_text)
                    if (
                        isinstance(parsed, dict)
                        and parsed.get("image_url")
                        and parsed.get("source_id")
                    ):
                        frames = [str(u) for u in (parsed.get("image_urls") or [parsed["image_url"]])]
                        add_artifact(BubbleArtifact(
                            KIND_VISION_IMAGES,
                            {"urls": frames, "alt": source_alias(parsed.get("source_id"))},
                        ))
                except (ValueError, json.JSONDecodeError):
                    pass

            # vision_query_events: VLM-Beschreibung je Event einsammeln, damit
            # das Post-Processing sie als Collapsible über die gezeigten Bilder
            # hängt (image_url → (Label, Beschreibung)).
            if result_text and '"events"' in result_text and '"image_url"' in result_text:
                try:
                    parsed = json.loads(result_text)
                    for ev in (parsed.get("events") or []):
                        if not isinstance(ev, dict):
                            continue
                        url = str(ev.get("image_url") or "").strip()
                        desc = str(
                            (ev.get("classification") or {}).get("description") or ""
                        ).strip()
                        if not url or not desc:
                            continue
                        name = str(ev.get("source_name") or ev.get("source_id") or "")
                        ts = str(ev.get("timestamp") or "")
                        tlabel = ts[11:16] if len(ts) >= 16 else ts
                        label = " · ".join(p for p in (name, tlabel) if p)
                        event_descriptions[url] = (label, desc)
                except (ValueError, json.JSONDecodeError):
                    pass

            # Update last URL success status. Tool errors are always
            # ``json.dumps({"error": ...})`` (web_fetch + function_calling
            # wrappers) — check that structured format instead of
            # substring-matching the content: a success page whose URL
            # contains "error" must not count as a failure. Tool execution
            # is sequential (backends/base.py loops tool_calls), so [-1]
            # is always the web_fetch this result belongs to.
            if pending_fetch is not None:
                _fetch_err = False
                if result_text.lstrip().startswith("{"):
                    try:
                        _fetch_err = "error" in json.loads(result_text)
                    except (ValueError, json.JSONDecodeError):
                        _fetch_err = False
                pending_fetch["success"] = not _fetch_err
                pending_fetch = None

            # Sandbox output URLs (marker SSOT: lib/sandbox.py). A page a
            # sub-agent built arrives as an artifact first; its marker line in
            # the report is then the same key and not shown twice.
            from .sandbox import SANDBOX_HTML_URL_MARKER, SANDBOX_IMAGE_URL_MARKER
            for line in result_text.split("\n"):
                if line.startswith(SANDBOX_HTML_URL_MARKER):
                    add_artifact(BubbleArtifact(
                        KIND_SANDBOX_HTML, {"url": line[len(SANDBOX_HTML_URL_MARKER):].strip()},
                    ))
                elif line.startswith(SANDBOX_IMAGE_URL_MARKER):
                    add_artifact(BubbleArtifact(
                        KIND_SANDBOX_IMAGE, {"url": line[len(SANDBOX_IMAGE_URL_MARKER):].strip()},
                    ))

            # silent_reply: Audio-Tools (audio_play, audio_play_folder,
            # audio_resume) markieren erfolgreichen Audio-Start damit der
            # Channel die TTS-Bestaetigung skippt — Music laeuft sofort,
            # kein "DJ labert in den Song". JSON-Result wird hier geparst.
            if not silent_reply and result_text and "silent_reply" in result_text:
                try:
                    parsed = json.loads(result_text)
                    if isinstance(parsed, dict) and parsed.get("silent_reply") is True:
                        silent_reply = True
                except (ValueError, json.JSONDecodeError):
                    pass

            yield chunk

        elif chunk_type == "thinking":
            thinking_content = chunk.get("text", "")
            if thinking_content:
                full_response += f"<think>{thinking_content}</think>"
            yield chunk

        elif chunk_type == "debug":
            # Backend debug items (truncation warning, context guard,
            # discarded partial tool calls, text-extraction notices) —
            # forward so consumers can mirror them into the browser debug
            # console. Previously these fell through the dispatch silently
            # and only ever reached the server logfile.
            yield chunk

        elif chunk_type == "done":
            metrics = chunk.get("metrics", {})
            token_count = metrics.get("tokens_generated", token_count)

    # --- Post-processing ---

    # Strip fallback tool-call JSON from response text
    full_response = strip_tool_json(full_response)
    # Bild-URLs absolut machen (LLM lässt den führenden Slash mal weg) — sonst
    # bricht das Bild nach einem Tab-Reload (relative URL → 404).
    full_response = _normalize_image_urls(full_response)

    # Kamerabilder des Turns als Referenz an den Textanfang: Der Text geht in
    # den Verlauf, und nur dort bleibt die URL für spätere Turns erhalten
    # (Tool-Ergebnisse werden nicht gespeichert). Je Bild-Artefakt die letzte
    # Aufnahme (bei einer Serie das repräsentative Bild). Angezeigt werden die
    # Bilder über ihre Artefakte an der Stelle des Turns (render_bubble).
    camera_images = [a for a in artifacts if a.kind == KIND_VISION_IMAGES]
    if camera_images:
        refs = [image_markdown(a.data["alt"], a.data["urls"][-1]) for a in camera_images]
        full_response = "\n\n".join(refs) + "\n\n" + full_response
        full_response = _dedup_injected_images(
            full_response, [a.data["urls"][-1] for a in camera_images],
        )

    # Original-VLM-Beschreibungen der TATSÄCHLICH gezeigten Event-Bilder als
    # Collapsible oben in die Bubble hängen, je mit Bildname davor. Als
    # <image_descriptions>-Tag — wird wie think/vlm_output generisch zum
    # Collapsible gerendert (SSoT get_xml_tag_config), aus dem Klartext gestript
    # und vom TTS nicht vorgelesen.
    if event_descriptions:
        shown_urls = [
            u for _alt, u in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", full_response)
        ]
        # Akzent-Orange aus dem Theme (SSoT), nicht hardcoden.
        try:
            from ..theme import COLORS
            _orange = COLORS.get("primary", "#e67700")
        except Exception:  # noqa: BLE001
            _orange = "#e67700"
        # Trennlinie zwischen den Einträgen im Lieblingsorange.
        sep = (
            f'<hr style="border:none;border-top:1px solid {_orange};'
            'opacity:0.6;margin:0.7em 0">'
        )
        seen_urls: set[str] = set()
        entries: list[str] = []
        for u in shown_urls:
            if u in event_descriptions and u not in seen_urls:
                seen_urls.add(u)
                label, desc = event_descriptions[u]
                head = (
                    f'<b style="color:{_orange}">{label}</b><br>\n' if label else ""
                )
                entries.append(f"{head}{desc}")
        if entries:
            full_response = (
                "<image_descriptions>\n"
                + ("\n" + sep + "\n").join(entries)
                + "\n</image_descriptions>"
                + full_response
            )

    # Artifact anchors → offsets in the final text
    full_response, anchor_offsets = resolve_artifact_anchors(full_response)
    for index, artifact in enumerate(artifacts):
        artifact.offset = anchor_offsets[index]

    # One place for the complete raw output of a turn (every agent turn runs
    # through here; the bubble formatter no longer logs it a second time).
    if DEBUG_LOG_RAW_OUTPUT:
        log_message("=" * 80)
        log_message(f"🔍 RAW AI RESPONSE (COMPLETE) — {agent_label}:")
        log_message(full_response)
        log_message("=" * 80)

    # Thinking blocks
    text_clean = strip_thinking_blocks(full_response) if full_response else ""
    inference_time = timer.elapsed()
    # Time in think blocks, measured by the backend over every block of the
    # turn and the sub-agents it ran (perf_metrics.ThinkingClock).
    thinking_time = InferenceWork.from_dict(metrics["work"]).thinking_s if metrics else 0.0
    tokens_per_sec = metrics.get("tokens_per_second", 0)

    truncated = bool(metrics.get("truncated"))

    # Metadata
    metadata_dict, metadata_display, debug_msg = build_inference_metadata(
        ttft=ttft,
        inference_time=inference_time,
        tokens_generated=token_count,
        tokens_per_sec=tokens_per_sec,
        source=f"{agent_label} ({model})",
        backend_metrics=metrics,
        tokens_prompt=metrics.get("tokens_prompt", 0),
        backend_type=llm_client.backend_type,
        agent_label=agent_label,
        response_chars=len(full_response),
        truncated=truncated,
        thinking_time=thinking_time,
    )

    yield {
        "type": "pipeline_result",
        "result": PipelineResult(
            text=full_response,
            text_clean=text_clean,
            metadata_dict=metadata_dict,
            metadata_display=metadata_display,
            debug_msg=debug_msg,
            metrics=metrics,
            ttft=ttft,
            inference_time=inference_time,
            thinking_time=thinking_time,
            tokens_per_sec=tokens_per_sec,
            artifacts=artifacts,
            silent_reply=silent_reply,
            truncated=truncated,
        ),
    }
