"""Narrator plugin — turn a workspace text file into one audio file via TTS.

Counterpart to the translator's ``translate_file``: the document content
never passes through the LLM context. The text is chunked at paragraph
boundaries (``lib/text_chunking.py``, same SSOT as translate_file),
each chunk is synthesized through the central ``generate_tts`` SSOT and
the pieces are ffmpeg-concatenated into a single WAV in the documents
tree. Typical pipeline: audio upload → Whisper transcript (workspace
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
from ....lib.security import TIER_WRITE_DATA
from ....lib.plugin_base import PluginContext, load_tool_description


@dataclass
class NarratorPlugin:
    name: str = "narrator"
    display_name: str = "Narrator"
    # Gear icon in the Agent-Editor plugin tab dispatches this state event
    # (same mechanism as audio_player → open_audio_settings).
    settings_event_name: str = "open_narrator_settings"
    description: str = (
        "Vertont Textdateien aus dem Dokumentenbaum zu einer einzelnen "
        "Audio-Datei — satzweise über die lokale TTS-Engine, Ergebnis als "
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
        ) -> str:
            """Synthesize a whole document into one audio file."""
            from ....lib import file_manager as fm
            from ....lib.audio_processing import (
                TTS_AUDIO_DIR,
                concatenate_wav_files,
                generate_tts,
            )
            from ....lib.config import (
                NARRATE_CHUNK_LIMIT_CHARS,
                NARRATE_DEFAULT_VOICE,
                TTS_DEFAULT_ENGINE,
            )
            from ....lib.debug_bus import debug
            from ....lib.text_chunking import split_paragraph_chunks
            from ....lib.tts_engine_manager import ensure_engine_ready

            # Resolve engine/voice from the narrator settings (UI row in the
            # audio section) unless the caller passed them explicitly.
            # "auto" follows the spoken-output engine; while that is off,
            # the user-selected GPU-free fallback is used so the loaded LLM
            # keeps its VRAM (the qwen3local container otherwise silently
            # falls back to an agonizingly slow CPU synth).
            from ....lib.settings import load_settings
            _settings = load_settings() or {}
            if not engine:
                engine = _settings.get("narrator_engine", "auto")
                if engine == "auto":
                    if _settings.get("enable_tts"):
                        engine = _settings.get("tts_engine") or TTS_DEFAULT_ENGINE
                    else:
                        engine = _settings.get("narrator_fallback_engine", "piper")
            if not voice:
                # Voices are engine-bound — look up the saved voice for the
                # resolved engine, else fall back to the engine's own first
                # voice (never hand e.g. the "AIfred" clone name to Piper).
                voice = (_settings.get("narrator_voices") or {}).get(engine, "")
            if not voice:
                from ....lib.tts_engines import get_engine
                eng_obj = get_engine(engine)
                if eng_obj is not None:
                    try:
                        _voices = list(eng_obj.get_voices().keys())
                    except Exception:
                        _voices = list(eng_obj.voices_fallback.keys())
                    voice = _voices[0] if _voices else ""
            voice = voice or NARRATE_DEFAULT_VOICE

            read = fm.read_file(filename)
            if not read.success:
                return json.dumps({"error": read.detail})
            text = read.metadata["content"].strip()
            if not text:
                return json.dumps({"error": f"File is empty: {filename}"})

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

            chunks = split_paragraph_chunks(text, NARRATE_CHUNK_LIMIT_CHARS)
            debug(f"🔊 narrate_file: {filename} → {len(chunks)} chunks ({engine}, {voice})")

            wav_urls: list[str] = []
            for i, chunk in enumerate(chunks, 1):
                url = await generate_tts(
                    chunk, voice, 1.0, engine,
                    pitch=1.0, agent="narrator", language=language,
                )
                if not url:
                    return json.dumps({
                        "error": f"TTS failed at chunk {i}/{len(chunks)}",
                        "chunks_done": i - 1,
                    })
                wav_urls.append(url)
                if i % 5 == 0 or i == len(chunks):
                    debug(f"🔊 narrate_file: {i}/{len(chunks)} chunks synthesized")

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
                src.unlink(missing_ok=True)
                if enc.returncode != 0:
                    return json.dumps({"error": "ffmpeg mp3 encode failed"})
            else:
                shutil.move(str(src), str(out_path))

            size_mb = out_path.stat().st_size / (1024 * 1024)
            debug(f"✅ narrate_file: wrote {output_filename} ({size_mb:.1f} MB)")
            return json.dumps({
                "written": output_filename,
                "chunks": len(chunks),
                "chars": len(text),
                "engine": engine,
                "voice": voice,
                "size_mb": round(size_mb, 1),
            })

        return [
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
                                "(e.g. 'documents/meeting-DE.txt'). Use list_files "
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
                                "TTS engine key (default: the configured default "
                                "engine, normally 'qwen3local')."
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
        return ""


plugin = NarratorPlugin()
