"""Narrator plugin — turn a workspace text file into one audio file via TTS.

Counterpart to the translator's ``translate_file``: the document content
never passes through the LLM context. The text is chunked at paragraph
boundaries (``lib/text_chunking.py``, same SSOT as translate_file),
each chunk is synthesized through the central ``generate_tts`` SSOT and
the pieces are ffmpeg-concatenated; das Ergebnis landet als MP3
(Default; explizites ``.wav`` überspringt den Encode) im Documents-Baum. Typical pipeline: audio upload → Whisper transcript (workspace
file) → translate_file → narrate_file.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA
from ....lib.plugin_base import PluginContext, load_tool_description


def _resolve_engine_and_voice(engine: str = "", voice: str = "") -> tuple[str, str]:
    """Resolve the effective narrator engine and its default voice.

    Shared by narrate_file and list_narrator_voices so both always agree.
    Engine-Entscheidung und Voice-Katalog laufen über die lib-SSOTs
    (``resolve_narrator_engine``/``voice_names`` — geteilt mit der
    Narrator-UI im ``_tts_config_mixin``). Voices are engine-bound — the
    saved voice for the resolved engine wins, else the engine's own first
    voice (never hand e.g. the "AIfred" clone name to Piper).
    """
    from ....lib.config import NARRATE_DEFAULT_VOICE, TTS_DEFAULT_ENGINE
    from ....lib.settings import load_settings
    from ....lib.tts_engines import get_engine, resolve_narrator_engine, voice_names

    _settings = load_settings() or {}
    if not engine:
        engine = resolve_narrator_engine(
            _settings.get("narrator_engine", "auto"),
            bool(_settings.get("enable_tts")),
            _settings.get("tts_engine") or TTS_DEFAULT_ENGINE,
            _settings.get("narrator_fallback_engine", "piper"),
        )
    if not voice:
        voice = (_settings.get("narrator_voices") or {}).get(engine, "")
    if not voice:
        eng_obj = get_engine(engine)
        if eng_obj is not None:
            names = voice_names(eng_obj)
            voice = names[0] if names else ""
    return engine, voice or NARRATE_DEFAULT_VOICE


def _gpu_engine_conflict(engine: str) -> str | None:
    """Clear error text when a GPU-bound engine cannot run right now.

    A GPU TTS engine is only safe while the spoken output runs the SAME
    engine — the LLM then sits in the matching -tts- calibration profile
    and the container's VRAM is reserved. Anything else (spoken output
    off, or on a different engine) would start a second uncoordinated
    GPU consumer next to the LLM: OOM or a silent CPU fallback. Per
    project rule there is no silent fallback — refuse with a clear
    message instead. Returns ``None`` when the engine is fine.
    """
    from ....lib.settings import load_settings
    from ....lib.tts_engines import get_engine

    eng_obj = get_engine(engine)
    if eng_obj is None or not eng_obj.needs_gpu:
        return None
    _settings = load_settings() or {}
    if _settings.get("enable_tts") and _settings.get("tts_engine") == engine:
        return None
    return (
        f"Engine '{engine}' needs GPU VRAM, but the spoken output is not "
        f"running it — a second GPU TTS container next to the LLM is not "
        f"coordinated. Use engine 'auto' or a GPU-free engine, or enable "
        f"the spoken output with '{engine}' first."
    )


@dataclass
class NarratorPlugin:
    name: str = "narrator"
    display_name: str = "Narrator"
    # Gear icon in the Agent-Editor plugin tab dispatches this state event
    # (same mechanism as audio_player → open_audio_settings).
    settings_event_name: str = "open_narrator_settings"
    description: str = (
        "Vertont Textdateien aus dem Dokumentenbaum zu einer einzelnen "
        "Audio-Datei — absatzweise über die lokale TTS-Engine, Ergebnis als "
        "MP3 neben der Quelldatei (handytauglich, '.wav' optional)."
    )

    def is_available(self) -> bool:
        # Local TTS engines are part of the deployment; availability of the
        # concrete engine container is ensured per call (ensure_engine_ready).
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:

        async def _narrate_file(
            filename: str,
            output_filename: str = "",
            voice: str = "",
            language: str = "de",
            engine: str = "",
            speaker_voices: dict[str, str] | None = None,
        ) -> str:
            """Synthesize a whole document into one audio file."""
            from ....lib import file_manager as fm
            from ....lib.audio_processing import (
                TTS_AUDIO_DIR,
                concatenate_wav_files,
                generate_tts,
            )
            from ....lib.config import NARRATE_CHUNK_LIMIT_CHARS
            from ....lib.debug_bus import debug
            from ....lib.text_chunking import (
                split_paragraph_chunks,
                split_speaker_segments,
            )
            from ....lib.tts_engine_manager import ensure_engine_ready

            # Resolve engine/voice from the narrator settings (UI row in the
            # audio section) unless the caller passed them explicitly.
            engine, voice = _resolve_engine_and_voice(engine, voice)
            # Unbekannte Engine sofort klar melden — ensure_engine_ready
            # behandelt sie als "ready" und erst generate_tts scheiterte
            # dann mit irreführendem "TTS failed at chunk 1/N".
            from ....lib.tts_engines import get_engine
            if get_engine(engine) is None:
                return json.dumps({"error": f"Unknown TTS engine: {engine}"})
            conflict = _gpu_engine_conflict(engine)
            if conflict:
                return json.dumps({"error": conflict})

            read = fm.read_file(filename)
            if not read.success:
                return json.dumps({"error": read.detail})
            text = read.metadata["content"].strip()
            if not text:
                return json.dumps({"error": f"File is empty: {filename}"})

            # Multi-voice mode: map "[LABEL]:" line markers to voices.
            # Validate strictly up front — an unknown voice or an unmapped
            # label in the text must abort with a clear error instead of
            # silently narrating with the wrong voice (project rule).
            if speaker_voices:
                if not isinstance(speaker_voices, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in speaker_voices.items()
                ):
                    return json.dumps({"error": (
                        "speaker_voices must be a JSON object mapping "
                        "speaker labels to voice names"
                    )})
                from ....lib.tts_engines import get_engine, voice_names
                eng_obj = get_engine(engine)
                if eng_obj is None:
                    return json.dumps({"error": f"Unknown TTS engine: {engine}"})
                known_voices = set(voice_names(eng_obj))
                unknown_voices = sorted(
                    v for v in speaker_voices.values() if v not in known_voices
                )
                if unknown_voices:
                    return json.dumps({"error": (
                        f"Unknown voice(s) for engine {engine}: "
                        f"{', '.join(unknown_voices)}. "
                        f"Available: {', '.join(sorted(known_voices))}"
                    )})
                segments = split_speaker_segments(text)
                unmapped = sorted({
                    label for label, _ in segments
                    if label is not None and label not in speaker_voices
                })
                if unmapped:
                    return json.dumps({"error": (
                        f"Speaker label(s) in {filename} without a voice in "
                        f"speaker_voices: {', '.join(unmapped)}"
                    )})
            else:
                segments = [(None, text)]

            if not output_filename:
                p = PurePosixPath(filename)
                output_filename = str(p.with_suffix(".mp3"))
            out_path, err = fm.safe_resolve(output_filename)
            if err:
                return json.dumps({"error": err})
            assert out_path is not None

            # Container start can take minutes on cold start — keep the
            # blocking wait off the event loop.
            loop = asyncio.get_running_loop()
            ok, status, _device = await loop.run_in_executor(
                None, lambda: ensure_engine_ready(engine)
            )
            if not ok:
                return json.dumps({"error": f"TTS engine {engine}: {status}"})

            # A speaker change is always a hard chunk boundary; long
            # segments are still split at paragraph boundaries internally.
            voiced_chunks: list[tuple[str, str]] = []
            for label, segment_text in segments:
                segment_voice = voice if label is None else (speaker_voices or {})[label]
                for chunk in split_paragraph_chunks(segment_text, NARRATE_CHUNK_LIMIT_CHARS):
                    voiced_chunks.append((segment_voice, chunk))
            total = len(voiced_chunks)
            if speaker_voices:
                debug(
                    f"🔊 narrate_file: {filename} → {len(segments)} speaker "
                    f"segments, {total} chunks ({engine}, "
                    f"{len(speaker_voices)} voices)"
                )
            else:
                debug(f"🔊 narrate_file: {filename} → {total} chunks ({engine}, {voice})")

            if not voiced_chunks:
                # Datei besteht nur aus Sprecher-Markern ohne Text — sonst
                # endete das später als irreführendes "ffmpeg concat failed".
                return json.dumps({"error": "No narratable text in file (only speaker markers?)"})

            wav_urls: list[str] = []
            for i, (chunk_voice, chunk) in enumerate(voiced_chunks, 1):
                url = await generate_tts(
                    chunk, chunk_voice, 1.0, engine,
                    pitch=1.0, agent="narrator", language=language,
                )
                if not url:
                    return json.dumps({
                        "error": f"TTS failed at chunk {i}/{total}",
                        "chunks_done": i - 1,
                    })
                wav_urls.append(url)
                if i % 5 == 0 or i == total:
                    debug(f"🔊 narrate_file: {i}/{total} chunks synthesized")

            combined_url: str | None
            if len(wav_urls) == 1:
                combined_url = wav_urls[0]
            else:
                combined_url = await loop.run_in_executor(
                    None, lambda: concatenate_wav_files(wav_urls, delete_originals=True)
                )
            if not combined_url:
                return json.dumps({"error": "ffmpeg concat failed"})

            # Move out of the 24h-cleanup TTS cache into the documents tree.
            # Default target is MP3 (speech VBR ≈ 130 kbps, ~10x smaller than
            # WAV — an 80 min narration is ~80 MB instead of ~800 MB, fit for
            # phone download). An explicit '.wav' output skips the encode.
            src = TTS_AUDIO_DIR / combined_url.split("/")[-1]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.suffix.lower() == ".mp3":
                import subprocess
                enc = await loop.run_in_executor(None, lambda: subprocess.run(
                    ["ffmpeg", "-y", "-i", str(src),
                     "-codec:a", "libmp3lame", "-q:a", "4", str(out_path)],
                    capture_output=True, timeout=1800,
                ))
                if enc.returncode != 0:
                    # Keep the combined WAV (result of potentially hours of
                    # synthesis) in the TTS cache for a retry; remove the
                    # partial MP3 so no broken file lingers in documents.
                    out_path.unlink(missing_ok=True)
                    return json.dumps({
                        "error": "ffmpeg mp3 encode failed — combined WAV kept "
                                 f"in TTS cache as {src.name}"
                    })
                src.unlink(missing_ok=True)
            else:
                shutil.move(str(src), str(out_path))

            size_mb = out_path.stat().st_size / (1024 * 1024)
            debug(f"✅ narrate_file: wrote {output_filename} ({size_mb:.1f} MB)")
            result: dict[str, Any] = {
                "written": output_filename,
                # Ready-made browser URL — share it as a MARKDOWN link
                # ([name](url)); raw <a> HTML renders dead in chat bubbles.
                "url": f"/_upload/documents/{output_filename}",
                "chunks": total,
                "chars": len(text),
                "engine": engine,
                "voice": voice,
                "size_mb": round(size_mb, 1),
            }
            if speaker_voices:
                result["speaker_voices"] = speaker_voices
                result["segments"] = len(segments)
            return json.dumps(result)

        async def _list_narrator_voices(engine: str = "") -> str:
            """Voice discovery for the effective narrator engine."""
            from ....lib.tts_engines import get_engine, voice_names

            engine, default_voice = _resolve_engine_and_voice(engine)
            conflict = _gpu_engine_conflict(engine)
            if conflict:
                return json.dumps({"error": conflict})
            eng_obj = get_engine(engine)
            if eng_obj is None:
                return json.dumps({"error": f"Unknown TTS engine: {engine}"})
            return json.dumps({
                "engine": engine,
                "default_voice": default_voice,
                "voices": voice_names(eng_obj),
            })

        return [
            Tool(
                name="list_narrator_voices",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "list_narrator_voices"),
                parameters={
                    "type": "object",
                    "properties": {
                        "engine": {
                            "type": "string",
                            "description": (
                                "Optional TTS engine key. Default: the engine "
                                "the narrator settings resolve to."
                            ),
                        },
                    },
                    "required": [],
                },
                executor=_list_narrator_voices,
            ),
            Tool(
                name="narrate_file",
                # Schreibt eine Datei im Dokumentenbaum → gleiche Stufe wie
                # write_file / translate_file.
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "narrate_file"),
                parameters={
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": (
                                "Source text file, relative to the documents root "
                                "WITHOUT a 'documents/' prefix "
                                "(e.g. 'meeting-DE.txt'). Use list_files "
                                "first if you are unsure of the path."
                            ),
                        },
                        "output_filename": {
                            "type": "string",
                            "description": (
                                "Optional output path. Default: same folder, "
                                "'<name>.mp3'. Use a '.wav' suffix to skip the "
                                "MP3 encode."
                            ),
                        },
                        "voice": {
                            "type": "string",
                            "description": (
                                "Reference voice name (default: AIfred). Must be "
                                "one of the cloned voices of the TTS engine."
                            ),
                        },
                        "language": {
                            "type": "string",
                            "description": "Language code of the text (e.g. 'de', 'en'). Default 'de'.",
                        },
                        "engine": {
                            "type": "string",
                            "description": (
                                "Optional TTS engine key. Default: resolved from "
                                "the narrator settings. GPU-bound engines are "
                                "only allowed while the spoken output runs the "
                                "same engine — otherwise omit or pick a GPU-free "
                                "engine."
                            ),
                        },
                        "speaker_voices": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                            "description": (
                                "Optional multi-voice mapping: speaker label → "
                                "voice name. Lines in the source file starting "
                                "with '[LABEL]:' switch to that speaker's voice "
                                "(the marker itself is not spoken); text before "
                                "the first marker uses the default voice. Voice "
                                "names are engine-specific — call "
                                "list_narrator_voices first and use names from "
                                "its result."
                            ),
                        },
                    },
                    "required": ["filename"],
                },
                executor=_narrate_file,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "narrate_file":
            fname = tool_args.get("filename", "?")
            if lang == "de":
                return f"🔊 Vertone {fname}..."
            return f"🔊 Narrating {fname}..."
        if tool_name == "list_narrator_voices":
            if lang == "de":
                return "🎤 Ermittle verfügbare Stimmen..."
            return "🎤 Listing available voices..."
        return ""


plugin = NarratorPlugin()
