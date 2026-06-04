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
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Optional

from .context_manager import estimate_tokens, strip_thinking_blocks
from .formatting import build_inference_metadata, format_thinking_process
from .logging_utils import log_message, log_raw_messages
from .timer import Timer

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from ..backends.base import LLMOptions


@dataclass
class PipelineResult:
    """Final result from run_llm_stream() — everything a consumer needs."""

    text: str = ""                                      # Full response (incl. <think> blocks)
    text_clean: str = ""                                # Response without thinking
    thinking_html: str = ""                             # Formatted thinking HTML
    metadata_dict: dict[str, Any] = field(default_factory=dict)
    metadata_display: str = ""                          # Metadata string for UI
    debug_msg: str = ""                                 # Metadata debug line
    metrics: dict[str, Any] = field(default_factory=dict)
    ttft: float = 0.0
    inference_time: float = 0.0
    tokens_per_sec: float = 0.0
    fetched_urls: list[dict[str, Any]] = field(default_factory=list)
    sandbox_html_urls: list[str] = field(default_factory=list)
    sandbox_image_urls: list[str] = field(default_factory=list)
    silent_reply: bool = False                          # any tool requested TTS-skip


def strip_tool_json(text: str) -> str:
    """Remove store_memory JSON from response text (fallback tool-call artifact)."""
    return re.sub(
        r'\{\s*"content"\s*:\s*"[^"]+"\s*,\s*"memory_type"\s*:\s*"[^"]+"\s*,\s*"summary"\s*:\s*"[^"]+"[^}]*\}',
        "", text,
    ).strip()


def _dedup_injected_images(text: str, urls: list[str]) -> str:
    """Single Source of Truth fürs gerenderte VLM-Bild: Die Pipeline stellt
    das Bild deterministisch voran (``![alias](url)``). Sieht das LLM dieselbe
    image_url im Tool-Result und rendert sie ein zweites Mal, erschiene das
    Bild doppelt. Hier bleibt pro URL nur das ERSTE ``![...](url)`` stehen,
    alle weiteren (das LLM-Echo) werden entfernt."""
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
    last_error = None
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
                last_error = e
            else:
                raise

    if last_error:
        raise last_error


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

    timer = Timer()
    full_response = ""
    token_count = 0
    first_token = False
    ttft = 0.0
    metrics: dict[str, Any] = {}
    fetched_urls: list[dict[str, Any]] = []
    sandbox_html_urls: list[str] = []
    sandbox_image_urls: list[str] = []
    silent_reply = False  # set True if any tool_result has silent_reply
    # SSoT fürs gerenderte VLM-Bild: URLs, die die Pipeline deterministisch
    # eingefügt hat. Ein späteres LLM-Echo derselben URL wird im Post-
    # Processing entfernt (sonst erscheint dasselbe Bild zweimal).
    injected_image_urls: list[str] = []
    # Genau EIN Vision-Bild pro Turn: analyze hat Vorrang vor snapshot (es
    # zeigt, was das VLM real gesehen hat). Beide Tools liefern nur noch
    # image_url — die Pipeline pinnt das gewählte Bild im Post-Processing.
    analyze_image: Optional[tuple[str, str]] = None   # (url, alt)
    snapshot_image: Optional[tuple[str, str]] = None  # (url, alt)
    # image_url → (label, VLM-Beschreibung) aus vision_query_events. Wird im
    # Post-Processing als <image_descriptions>-Collapsible über die tatsächlich
    # gezeigten Event-Bilder gehängt.
    event_descriptions: dict[str, tuple[str, str]] = {}

    # Select stream source: with or without retry
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
            log_message(f"🔧 Tool call: {tool_name}({full_args})")

            # Track web_fetch URLs for sources collapsible
            if tool_name == "web_fetch":
                try:
                    tool_args = json.loads(full_args) if full_args else {}
                except (ValueError, json.JSONDecodeError):
                    tool_args = {}
                url = tool_args.get("url", "")
                fetched_urls.append({"url": url, "success": None})

            yield chunk

        elif chunk_type == "tool_progress":
            # Streaming tools (web_search, search_documents, …) emit progress
            # lines while they run. Forward them so consumers can update the
            # UI immediately instead of seeing a debug-block at tool end.
            yield chunk

        elif chunk_type == "tool_result":
            result_text = chunk.get("result", "")
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
                            # Record analyze's image (highest priority). The
                            # actual image is pinned ONCE in post-processing so
                            # exactly one vision image appears per turn, even
                            # when snapshot + analyze both ran. Alt-text uses
                            # the user-given camera alias (e.g. "Türkamera").
                            if isinstance(image_url, str) and image_url:
                                try:
                                    from .vision_utils import resolve_source_alias
                                    alt = resolve_source_alias(
                                        str(vlm_source_id), fallback="Snapshot"
                                    )
                                except Exception:  # noqa: BLE001
                                    alt = "Snapshot"
                                analyze_image = (image_url, alt)
                            full_response = (
                                f"<vlm_output>{body}</vlm_output>"
                                + full_response
                            )
                            if vlm_debug_msg:
                                yield {"type": "debug", "message": vlm_debug_msg}
                except (ValueError, json.JSONDecodeError):
                    pass

            # vision_snapshot (no VLM) returns image_url without vlm_raw —
            # record it as the fallback vision image (analyze wins if present).
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
                        try:
                            from .vision_utils import resolve_source_alias
                            snap_alt = resolve_source_alias(
                                str(parsed.get("source_id")), fallback="Snapshot"
                            )
                        except Exception:  # noqa: BLE001
                            snap_alt = "Snapshot"
                        snapshot_image = (str(parsed["image_url"]), snap_alt)
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

            # Update last URL success status
            if fetched_urls and fetched_urls[-1]["success"] is None:
                fetched_urls[-1]["success"] = "error" not in result_text.lower()[:50]

            # Extract sandbox output URLs
            for line in result_text.split("\n"):
                if line.startswith("SANDBOX_HTML_URL: "):
                    sandbox_html_urls.append(line.split("SANDBOX_HTML_URL: ", 1)[1].strip())
                elif line.startswith("SANDBOX_IMAGE_URL: "):
                    sandbox_image_urls.append(line.split("SANDBOX_IMAGE_URL: ", 1)[1].strip())

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

        elif chunk_type == "done":
            metrics = chunk.get("metrics", {})
            token_count = metrics.get("tokens_generated", token_count)

    # --- Post-processing ---

    # Strip fallback tool-call JSON from response text
    full_response = strip_tool_json(full_response)
    # Bild-URLs absolut machen (LLM lässt den führenden Slash mal weg) — sonst
    # bricht das Bild nach einem Tab-Reload (relative URL → 404).
    full_response = _normalize_image_urls(full_response)

    # Genau EIN Vision-Bild pro Turn ganz oben pinnen (analyze vor snapshot).
    # So erscheint bei kombiniertem „Foto + Analyse" nicht dasselbe Motiv
    # doppelt. Die URL kommt zusätzlich in injected_image_urls, damit ein
    # etwaiges LLM-Echo derselben URL unten dedupliziert wird.
    chosen_image = analyze_image or snapshot_image
    if chosen_image:
        _img_url, _img_alt = chosen_image
        full_response = f"![{_img_alt}]({_img_url})\n\n" + full_response
        injected_image_urls.append(_img_url)

    # SSoT fürs VLM-Bild durchsetzen: Die Pipeline hat das Bild oben
    # deterministisch vorangestellt. Hat das LLM dieselbe image_url (die es im
    # Tool-Result sah) ein zweites Mal als Markdown gerendert, entfernen wir
    # die Dublette — pro URL bleibt nur das erste ![...](url) stehen.
    if injected_image_urls:
        full_response = _dedup_injected_images(full_response, injected_image_urls)

    # Original-VLM-Beschreibungen der TATSÄCHLICH gezeigten Event-Bilder als
    # Collapsible oben in die Bubble hängen, je mit Bildname davor. Als
    # <image_descriptions>-Tag — wird wie think/vlm_output generisch zum
    # Collapsible gerendert (SSoT get_xml_tag_config), aus dem Klartext gestript
    # und vom TTS nicht vorgelesen.
    if event_descriptions:
        shown_urls = [
            u for _alt, u in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", full_response)
        ]
        seen_urls: set[str] = set()
        entries: list[str] = []
        for u in shown_urls:
            if u in event_descriptions and u not in seen_urls:
                seen_urls.add(u)
                label, desc = event_descriptions[u]
                entries.append(f"{label}\n{desc}" if label else desc)
        if entries:
            full_response = (
                "<image_descriptions>\n"
                + "\n\n".join(entries)
                + "\n</image_descriptions>"
                + full_response
            )

    # Thinking blocks
    text_clean = strip_thinking_blocks(full_response) if full_response else ""
    inference_time = timer.elapsed()
    tokens_per_sec = metrics.get("tokens_per_second", 0)

    thinking_html = format_thinking_process(
        full_response,
        model_name=model,
        inference_time=inference_time,
        tokens_per_sec=tokens_per_sec,
    )

    # Metadata
    metadata_dict, metadata_display, debug_msg = build_inference_metadata(
        ttft=ttft,
        inference_time=inference_time,
        tokens_generated=token_count,
        tokens_per_sec=tokens_per_sec,
        source=f"{agent_label} ({model})",
        backend_metrics=metrics,
        tokens_prompt=metrics.get("tokens_prompt", 0),
        agent_label=agent_label,
        response_chars=len(full_response),
    )

    yield {
        "type": "pipeline_result",
        "result": PipelineResult(
            text=full_response,
            text_clean=text_clean,
            thinking_html=thinking_html,
            metadata_dict=metadata_dict,
            metadata_display=metadata_display,
            debug_msg=debug_msg,
            metrics=metrics,
            ttft=ttft,
            inference_time=inference_time,
            tokens_per_sec=tokens_per_sec,
            fetched_urls=fetched_urls,
            sandbox_html_urls=sandbox_html_urls,
            sandbox_image_urls=sandbox_image_urls,
            silent_reply=silent_reply,
        ),
    }
