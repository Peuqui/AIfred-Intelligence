"""Dictation and audio files on the server: a recording becomes text, independent of the request that
brought it (a locked phone or a closed tab ends nothing).

``accept`` checks the upload and starts a job on the server's loop. The job estimates the time,
asks the user when it will take long (``dictation_question``, answered with ``answer``), transcribes
through the STT service and hands the text on as the browser's dictation did:

- a long transcript → a file in the documents and a bubble with its link,
- an audio file → a bubble with the transcript,
- a dictation → straight to the AI as a turn, or into the input field (``dictation_text``) when
  ``show_transcription`` is on or the text holds a mail address or a URL STT likely got wrong.

Every job ends with ``dictation_finished``; a message for the user comes as ``notice``.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import session_events
from .debug_bus import debug, session_scope
from .logging_utils import log_exception

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm")

# Structured data STT likely gets wrong: Whisper often writes "@" as "at" and "." as "punkt"/"dot".
_STRUCTURED_DATA_RE = re.compile(
    r"@"                                    # email: literal @
    r"|\bat\b.*\b(?:punkt|dot)\b"           # email: "at ... punkt/dot" (STT transcription)
    r"|www\s*\.\s*"                         # URL: www. (with possible spaces)
    r"|www\s+punkt\s+"                      # URL: "www punkt" (STT)
    r"|\.\s*(?:com|de|org|net|io|edu)\b"    # URL: .com, .de etc.
    r"|\bpunkt\s*(?:com|de|org|net|io|edu)\b"  # URL: "punkt de" (STT)
    r"|https?\s*:\s*/\s*/"                  # URL: http(s)://
)


def needs_review(text: str) -> bool:
    """Does the transcript hold a mail address or a URL the user should check before it is sent?"""
    return bool(_STRUCTURED_DATA_RE.search(text.lower()))


class DictationRejected(Exception):
    """The upload cannot be transcribed; ``message`` says why, in the user's language."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class DictationJob:
    id: str
    session_id: str
    source: str          # "mic" (dictation) or "file" (audio file)
    filename: str
    path: Path           # the upload as a temporary file, deleted when the job ends
    size_display: str
    lang: str            # the interface's language
    answer: asyncio.Future[bool] | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


_jobs: dict[str, DictationJob] = {}


def accept(job_id: str, session_id: str, filename: str, content: bytes, source: str, lang: str) -> DictationJob:
    """Check the upload and start its job. The client names the job (a UUID) before it uploads: its
    question or its text may reach the client's event stream before the upload's answer does.

    Raises:
        DictationRejected: unknown audio format, a file above AUDIO_UPLOAD_MAX_MB, or a job id that
            is no UUID or is taken.
    """
    from .config import AUDIO_UPLOAD_MAX_MB
    from .formatting import format_number
    from .i18n import t

    try:
        job_id = uuid.UUID(job_id).hex
    except ValueError as error:
        raise DictationRejected(f"not a job id: {job_id!r}") from error
    if job_id in _jobs:
        raise DictationRejected(f"job {job_id} runs already")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in AUDIO_EXTENSIONS:
        raise DictationRejected(t("stt_format_unsupported", lang=lang, ext=extension or "–", allowed=", ".join(AUDIO_EXTENSIONS)))
    size_mb = len(content) / (1024 * 1024)
    if size_mb > AUDIO_UPLOAD_MAX_MB:
        raise DictationRejected(t(
            "stt_too_large", lang=lang, size=format_number(size_mb, 1), limit=format_number(AUDIO_UPLOAD_MAX_MB, 0),
        ))
    with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as upload:
        upload.write(content)
    size_display = f"{format_number(len(content) / 1024, 0)} KB" if size_mb < 1 else f"{format_number(size_mb, 1)} MB"
    job = DictationJob(job_id, session_id, source, filename, Path(upload.name), size_display, lang)
    _jobs[job.id] = job
    job.task = asyncio.create_task(_run(job), name=f"dictation-{session_id[:8]}")
    return job


def answer(session_id: str, job_id: str, yes: bool) -> bool:
    """The user's answer to the job's question; False when the job asks nothing (or is not the session's)."""
    job = _jobs.get(job_id)
    if job is None or job.session_id != session_id or job.answer is None or job.answer.done():
        return False
    job.answer.set_result(yes)
    return True


def _notice(job: DictationJob, level: str, text: str) -> None:
    session_events.publish(job.session_id, "notice", {"level": level, "text": text})


async def _ask(job: DictationJob, kind: str, question: str) -> bool:
    """Ask the session's clients and wait for the answer (the job waits as long as it takes)."""
    job.answer = asyncio.get_running_loop().create_future()
    session_events.publish(job.session_id, "dictation_question", {"job_id": job.id, "kind": kind, "text": question})
    try:
        return await job.answer
    finally:
        job.answer = None


async def _run(job: DictationJob) -> None:
    from .stt_client import STTError

    with session_scope(job.session_id):
        try:
            text = await _transcription(job)
            if text is not None:
                await _deliver(job, text)
        except (STTError, TimeoutError) as error:
            debug(f"❌ Whisper: {error}")
            _notice(job, "error", f"🎤 Whisper: {error}")
        except Exception as error:  # noqa: BLE001 — boundary of the job: the user sees the error, the log keeps the traceback
            log_exception("Dictation failed", error)
            _notice(job, "error", _t(job, "stt_failed", reason=str(error)))
        finally:
            job.path.unlink(missing_ok=True)
            _jobs.pop(job.id, None)
            session_events.publish(job.session_id, "dictation_finished", {"job_id": job.id})


def _t(job: DictationJob, key: str, **values: Any) -> str:
    from .i18n import t

    return t(key, lang=job.lang, **values)


async def _transcription(job: DictationJob) -> str | None:
    """The text of the recording; None when the user stopped it or nothing could be done (said so)."""
    from .audio_processing import get_audio_duration
    from .config import (
        WHISPER_CONFIRM_THRESHOLD_S,
        WHISPER_RTF_GPU,
        WHISPER_TRANSCRIBE_TIMEOUT_S,
    )
    from .formatting import format_number
    from .stt_client import get_default_client

    stt = get_default_client()
    if not await asyncio.to_thread(stt.is_ready):
        debug("🎤 Starting Whisper service...")
        await asyncio.to_thread(stt.ensure_ready)

    duration_s = await asyncio.to_thread(get_audio_duration, str(job.path))
    estimate_s = duration_s * WHISPER_RTF_GPU
    if duration_s:
        debug(
            f"🎤 Audio duration {format_number(duration_s / 60, 1)} min — estimated transcription "
            f"~{format_number(max(estimate_s, 6) / 60, 1)} min (GPU engine)"
        )
    if estimate_s > WHISPER_TRANSCRIBE_TIMEOUT_S:
        debug(f"⚠️ Estimated ~{round(estimate_s / 60)} min exceeds the {WHISPER_TRANSCRIBE_TIMEOUT_S // 60} min transcribe timeout — aborted")
        _notice(job, "error", _t(job, "stt_estimate_too_long", minutes=round(estimate_s / 60), limit=WHISPER_TRANSCRIBE_TIMEOUT_S // 60))
        return None
    if estimate_s > WHISPER_CONFIRM_THRESHOLD_S:
        question = _t(job, "stt_confirm_long", filename=job.filename, minutes=round(estimate_s / 60), engine="GPU")
        if not await _ask(job, "long", question):
            debug("🎤 Transcription cancelled by user")
            _notice(job, "info", _t(job, "stt_cancelled"))
            return None

    if job.source == "file":
        from .turn_service import post_bubble

        post_bubble(job.session_id, "aifred", _t(
            job, "stt_bubble_started", filename=job.filename, size=job.size_display, engine="GPU",
        ), job.lang, in_history=False)
    debug(f"🎤 Transcribing audio: {job.filename} ({job.size_display}, GPU engine)...")
    return await _transcribe(job, may_unload=True)


async def _transcribe(job: DictationJob, *, may_unload: bool) -> str | None:
    """Transcribe on the device the settings prefer. An audio file keeps its own language (forcing the
    interface's makes Whisper translate on the fly) and gets speaker labels (a recording of several
    people); a dictation is in the interface's language, one speaker."""
    from .audio_processing import transcribe_audio_auto
    from .formatting import format_number
    from .runtime_settings import load_runtime_settings
    from .stt_client import STTGPUUnavailableError

    language = "auto" if job.source == "file" else job.lang
    try:
        text, seconds, device = await asyncio.to_thread(
            transcribe_audio_auto, str(job.path), language=language, diarize=job.source == "file",
        )
    except STTGPUUnavailableError:
        # A big file and no card with room (the STT service on "GPU only"): the CPU would take too
        # long, unloading the LLM makes room. It reloads with the next chat.
        backend = load_runtime_settings().backend_type
        if not (may_unload and backend in ("llamacpp", "vllm", "ollama")):
            debug("⚠️ No GPU with free VRAM — transcription aborted (file too big for CPU)")
            _notice(job, "error", _t(job, "stt_gpu_busy_aborted"))
            return None
        debug("⚠️ No GPU with free VRAM — asking to unload the LLM")
        if not await _ask(job, "unload", _t(job, "stt_gpu_busy_unload_confirm", filename=job.filename, size=job.size_display)):
            debug("🎤 Transcription cancelled by user (GPU busy, no LLM unload)")
            _notice(job, "info", _t(job, "stt_cancelled"))
            return None
        debug(f"🎤 Unloading LLM backend ({backend}) for GPU transcription...")
        await unload_chat_models(backend)
        return await _transcribe(job, may_unload=False)
    if device == "cpu":
        debug("⚠️ Transcribed on the CPU engine (slower)")
    if not text:
        debug("⚠️ Transcription returned empty text")
        _notice(job, "error", _t(job, "stt_empty"))
        return None
    debug(f"✅ Transcription complete ({format_number(seconds, 1)}s)")
    return str(text)


async def _deliver(job: DictationJob, text: str) -> None:
    from .config import TRANSCRIPT_TO_WORKSPACE_THRESHOLD_CHARS
    from .logging_utils import CONSOLE_SEPARATOR
    from .settings import persisted_settings
    from .turn_service import TurnRequest, post_bubble, submit_turn

    if len(text) > TRANSCRIPT_TO_WORKSPACE_THRESHOLD_CHARS:
        _save_long_transcript(job, text)
    elif job.source == "file":
        # In the history too: the agent can work with it.
        post_bubble(job.session_id, "aifred", _t(
            job, "stt_bubble_transcript", filename=job.filename, size=job.size_display, text=text,
        ), job.lang, in_history=True)
        debug("💬 Transcript added to chat bubble")
    elif persisted_settings().get("show_transcription") or needs_review(text):
        if needs_review(text):
            debug("✏️ Email/URL detected → edit mode")
        session_events.publish(job.session_id, "dictation_text", {"job_id": job.id, "text": text})
        debug("✏️ Text in input field → Ready for editing")
    else:
        submit_turn(TurnRequest(job.session_id, text))
        debug("🚀 Dictation sent to the AI")
    debug(CONSOLE_SEPARATOR)


def _save_long_transcript(job: DictationJob, text: str) -> None:
    """A long recording (a meeting) becomes a file in the documents, which the agent works on with
    its file tools, and a bubble with its link (in the history: "summarize it" works)."""
    from . import file_manager
    from .formatting import format_number
    from .turn_service import post_bubble

    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(job.filename)[0])[:60]
    name = f"transcript-{stem}-{datetime.now().strftime('%Y-%m-%d-%H%M')}.txt"
    result = file_manager.write_file(name, text)
    if not result.success:
        debug(f"❌ Workspace write failed: {result.detail}")
        _notice(job, "error", _t(job, "stt_transcript_save_failed", reason=result.detail))
        return
    chars = format_number(len(text) / 1000, 1)
    debug(f"📄 Transcript saved to workspace: {name} ({chars}k chars)")
    # A Markdown link, not raw HTML: the model imitates the pattern it sees here.
    link = f"[{name}](/_upload/documents/{name})"
    # A transcript with speaker labels gets the text that explains the anonymous [SPEAKER_xx] markers.
    key = "stt_bubble_saved_speakers" if "[SPEAKER_" in text else "stt_bubble_saved"
    post_bubble(job.session_id, "aifred", _t(job, key, transcript_link=link, filename=name, chars=chars), job.lang, in_history=True)
    _notice(job, "success", _t(job, "stt_transcript_saved", filename=name))


async def unload_chat_models(backend_type: str) -> None:
    """Unload the chat backend's models so the STT service's GPU worker fits; the LLM reloads with the
    next chat request. llama-swap: ``POST /unload`` stops every running instance (vLLM included)."""
    import httpx

    from ..backends.ollama import wait_for_vram_stable
    from .config import BACKEND_URLS, LLAMASWAP_BACKENDS
    from .formatting import format_number

    if backend_type in LLAMASWAP_BACKENDS:
        base = BACKEND_URLS["llamacpp"].removesuffix("/v1")
        async with httpx.AsyncClient(timeout=30) as client:
            (await client.post(f"{base}/unload")).raise_for_status()
        stabilized, wait_s, free_mb = await wait_for_vram_stable()
        debug(
            f"🎤 llama-swap unloaded — {format_number(free_mb)} MiB VRAM free after "
            f"{format_number(wait_s, 1)}s{'' if stabilized else ' (still settling)'}"
        )
    elif backend_type == "ollama":
        from ..backends import BackendFactory

        backend = BackendFactory.create("ollama")
        try:
            _success, unloaded = await backend.unload_all_models(wait_for_stability=True)
            debug(f"🎤 Ollama unloaded: {', '.join(unloaded) if unloaded else 'nothing loaded'}")
        finally:
            await backend.client.aclose()
