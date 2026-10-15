"""Narrator plugin — turn a workspace text file into one audio file via TTS.

Counterpart to the translator's ``translate_file``: the document content
never passes through the LLM context. The text is chunked at paragraph
boundaries (``lib/text_chunking.py``, same SSOT as translate_file),
each chunk is spoken by the TTS service (``tts_client``) with AIfred's list (or one
pinned engine) and the pieces are ffmpeg-concatenated; das Ergebnis landet als MP3
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
from ....lib.plugin_base import (
    PluginContext,
    load_tool_description,
    load_tool_parameters,
)
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA
from ....lib.tts_client import EngineRef, TTSSuiteError, new_run_id


async def _choose_engine_and_voice(engine: str = "", voice: str = "") -> tuple[EngineRef, str, list[str]]:
    """Engine, default voice and the engine's voice names for a narration.

    Shared by narrate_file and list_narrator_voices so both always agree.
    The engine is the topmost active entry of AIfred's TTS list (or the explicit key); the
    reading itself sends the whole list (``narration_refs``), so the service steps on when an
    engine fails.
    Voices are engine-bound and come from the service — the saved narrator
    voice for that engine wins, else the engine's own first voice (never
    hand e.g. the "AIfred" clone name to Piper).

    Raises:
        ValueError: no usable engine.
        TTSSuiteError: the service is not reachable or refuses.
    """
    from ....lib.config import NARRATE_DEFAULT_VOICE, NARRATE_SPEECH_UNIT
    from ....lib.settings import load_settings
    from ....lib.tts_client import get_tts_client
    from ....lib.tts_service import single_engine_ref

    client = get_tts_client()
    await asyncio.to_thread(client.ensure_ready)
    ref = await asyncio.to_thread(single_engine_ref, engine, NARRATE_SPEECH_UNIT)
    names = [info.name for info in await asyncio.to_thread(client.voices, ref.engine)]
    if not voice:
        voice = ((load_settings() or {}).get("narrator_voices") or {}).get(ref.engine, "")
    if not voice:
        voice = names[0] if names else ""
    return ref, voice or NARRATE_DEFAULT_VOICE, names


async def _narrator_voice(engine_key: str) -> str:
    """Saved narrator voice of an engine, else the engine's own first voice."""
    from ....lib.settings import load_settings
    from ....lib.tts_client import get_tts_client

    saved = ((load_settings() or {}).get("narrator_voices") or {}).get(engine_key, "")
    if saved:
        return str(saved)
    names = [info.name for info in await asyncio.to_thread(get_tts_client().voices, engine_key)]
    if not names:
        raise ValueError(f"engine {engine_key!r} offers no voice for the narrator")
    return names[0]


@dataclass
class NarratorPlugin:
    name: str = "narrator"
    # Gear icon in the Agent-Editor plugin tab dispatches this state event
    # (same mechanism as audio_player → open_audio_settings).
    settings_event_name: str = "open_narrator_settings"

    def is_available(self) -> bool:
        # The TTS service is part of the deployment; it starts the concrete
        # engine container per call.
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
            )
            from ....lib.config import (
                NARRATE_CHUNK_LIMIT_CHARS,
                NARRATE_PARAGRAPH_PAUSE_MS,
                NARRATE_SPEECH_UNIT,
            )
            from ....lib.debug_bus import debug
            from ....lib.speech_synthesis import store_reading_audio
            from ....lib.text_chunking import (
                split_paragraph_chunks,
                split_speaker_segments,
            )
            from ....lib.tts_client import get_tts_client
            from ....lib.tts_service import narration_refs

            # Engines from AIfred's list (or the caller's explicit key); voice from
            # the narrator settings unless passed. The passed voice and the speaker voices
            # belong to the first engine; another engine the service steps to speaks with
            # its own narrator voice.
            explicit_engine = bool(engine)
            try:
                engine_ref, voice, known_names = await _choose_engine_and_voice(engine, voice)
            except (TTSSuiteError, ValueError) as exc:
                return json.dumps({"error": str(exc)})
            engine = engine_ref.engine
            try:
                refs = await asyncio.to_thread(narration_refs, engine if explicit_engine else "", NARRATE_SPEECH_UNIT)
                other_voices = {ref.engine: await _narrator_voice(ref.engine) for ref in refs[1:]}
            except (TTSSuiteError, ValueError) as exc:
                return json.dumps({"error": str(exc)})

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
                known_voices = set(known_names)
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

            loop = asyncio.get_running_loop()

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

            client = get_tts_client()
            wav_urls: list[str] = []
            run = new_run_id()  # one reading for the whole file: a code block may span chunks
            for i, (chunk_voice, chunk) in enumerate(voiced_chunks, 1):
                try:
                    stream = await client.speak(
                        chunk, language=language, engines=refs, paragraph_pause_ms=NARRATE_PARAGRAPH_PAUSE_MS,
                        voices={**{key: {"voice": name} for key, name in other_voices.items()},
                                engine: {"voice": chunk_voice}},
                        run=run,
                    )
                    wav_urls.extend(await store_reading_audio(stream, "narrator"))
                except (TTSSuiteError, ValueError) as exc:
                    return json.dumps({
                        "error": f"TTS failed at chunk {i}/{total}: {exc}",
                        "chunks_done": i - 1,
                    })
                if i % 5 == 0 or i == total:
                    debug(f"🔊 narrate_file: {i}/{total} chunks synthesized")

            if not wav_urls:
                return json.dumps({"error": "The TTS service delivered no audio"})
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
            try:
                ref, default_voice, names = await _choose_engine_and_voice(engine)
            except (TTSSuiteError, ValueError) as exc:
                return json.dumps({"error": str(exc)})
            return json.dumps({
                "engine": ref.engine,
                "default_voice": default_voice,
                "voices": names,
            })

        return [
            Tool(
                name="list_narrator_voices",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "list_narrator_voices"),
                parameters=load_tool_parameters(__file__, "list_narrator_voices"),
                executor=_list_narrator_voices,
            ),
            Tool(
                name="narrate_file",
                # Schreibt eine Datei im Dokumentenbaum → gleiche Stufe wie
                # write_file / translate_file.
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "narrate_file"),
                parameters=load_tool_parameters(__file__, "narrate_file"),
                executor=_narrate_file,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: set[str] | None = None) -> str:
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
