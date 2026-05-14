"""Test DashScope Qwen3-TTS Voice Cloning + Streaming.

Tests:
1. Voice Enrollment (clone AIfred/Sokrates/Salomo from WAV files)
2. Sentence-based TTS with cloned voices
3. Realtime WebSocket streaming with cloned voices

Run: python tests/test_dashscope_voice_clone.py
Requires: DASHSCOPE_API_KEY in .env or environment
"""

import os
import sys
import time
import base64
import json
import wave

# Load .env from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
from dotenv import load_dotenv  # noqa: E402 — after sys.path setup
load_dotenv()

import requests  # noqa: E402 — must be after load_dotenv()
import dashscope  # noqa: E402 — must be after load_dotenv() to pick up API key

# International endpoint
DASHSCOPE_API_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
ENROLLMENT_URL = f"{DASHSCOPE_API_URL}/services/audio/tts/customization"
WEBSOCKET_URL = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"

dashscope.base_http_api_url = DASHSCOPE_API_URL

API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    print("DASHSCOPE_API_KEY not set!")
    sys.exit(1)

VOICES_DIR = os.path.join(PROJECT_ROOT, "docker", "xtts", "voices")
OUTPUT_DIR = "/tmp/dashscope_tts_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Voice cloning target model (must match TTS model used later)
VC_TARGET_MODEL = "qwen3-tts-vc-2026-01-22"
VC_REALTIME_MODEL = "qwen3-tts-vc-realtime-2026-01-15"


# ============================================================
# 1. Voice Enrollment
# ============================================================

def list_enrolled_voices() -> list[dict]:
    """List all enrolled custom voices."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "list",
            "page_index": 0,
            "page_size": 100,
        },
    }
    resp = requests.post(ENROLLMENT_URL, json=payload, headers=headers, timeout=30)
    data = resp.json()
    if resp.status_code == 200 and "output" in data:
        return data["output"].get("voices", [])
    print(f"  List error: {data}")
    return []


def enroll_voice(name: str, wav_path: str, target_model: str) -> str | None:
    """Enroll a voice from a WAV file. Returns voice_id or None."""
    print(f"\n  Enrolling '{name}' from {os.path.basename(wav_path)}...")

    # Check file
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        dur = wf.getnframes() / sr
        print(f"  Audio: {dur:.1f}s, {sr}Hz, {wf.getnchannels()}ch")

    # Read and encode
    with open(wav_path, "rb") as f:
        audio_bytes = f.read()
    b64 = base64.b64encode(audio_bytes).decode()
    data_uri = f"data:audio/wav;base64,{b64}"

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": target_model,
            "preferred_name": name.lower(),
            "audio": {"data": data_uri},
        },
    }

    start = time.time()
    resp = requests.post(ENROLLMENT_URL, json=payload, headers=headers, timeout=60)
    elapsed = time.time() - start

    data = resp.json()
    if resp.status_code == 200 and "output" in data:
        voice_id = data["output"].get("voice", "")
        print(f"  OK: voice_id={voice_id} ({elapsed:.1f}s)")
        return voice_id
    else:
        print(f"  Error ({resp.status_code}): {json.dumps(data, indent=2)}")
        return None


# ============================================================
# 2. Sentence-based TTS with cloned voice
# ============================================================

def test_vc_streaming(text: str, voice_id: str, name: str) -> bool:
    """Test streaming TTS with a cloned voice (sentence-based)."""
    print(f"\n{'='*60}")
    print(f"Voice Clone Streaming: {name} ({voice_id[:40]}...)")
    print(f"Text: {text[:80]}")
    print(f"{'='*60}")

    start = time.time()
    first_chunk_time = None
    chunks: list[bytes] = []

    response = dashscope.MultiModalConversation.call(
        model=VC_TARGET_MODEL,
        api_key=API_KEY,
        text=text,
        voice=voice_id,
        language_type="German",
        stream=True,
    )

    for chunk in response:
        if chunk.output and chunk.output.audio and chunk.output.audio.data:
            if first_chunk_time is None:
                first_chunk_time = time.time() - start
            pcm_bytes = base64.b64decode(chunk.output.audio.data)
            chunks.append(pcm_bytes)

    elapsed = time.time() - start
    total_bytes = sum(len(c) for c in chunks)
    duration = total_bytes / (24000 * 2)

    print(f"  Chunks: {len(chunks)}, Audio: {duration:.1f}s, Time: {elapsed:.1f}s")
    if first_chunk_time:
        print(f"  First-Audio-Delay: {first_chunk_time:.2f}s")

    # Save as WAV
    output_path = os.path.join(OUTPUT_DIR, f"vc_{name.lower()}.wav")
    pcm_data = b"".join(chunks)
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_data)

    print(f"  Saved: {output_path}")
    return len(chunks) > 0


# ============================================================
# 3. Realtime WebSocket streaming
# ============================================================

def test_realtime_streaming(text: str, voice_id: str, name: str) -> bool:
    """Test true realtime WebSocket streaming with cloned voice."""
    print(f"\n{'='*60}")
    print(f"Realtime WebSocket: {name}")
    print(f"Text: {text[:80]}")
    print(f"{'='*60}")

    try:
        from dashscope.audio.qwen_tts_realtime import (
            QwenTtsRealtime,
            QwenTtsRealtimeCallback,
            AudioFormat,
        )
    except ImportError:
        print("  QwenTtsRealtime not available in this SDK version")
        return False

    chunks: list[bytes] = []
    start_time = time.time()
    first_chunk_time = None
    done_event = None

    import threading
    done_event = threading.Event()

    class TestCallback(QwenTtsRealtimeCallback):
        def on_event(self, response: dict) -> None:
            nonlocal first_chunk_time
            event_type = response.get("type", "")
            if event_type == "response.audio.delta":
                if first_chunk_time is None:
                    first_chunk_time = time.time() - start_time
                    print(f"  First chunk: {first_chunk_time:.2f}s")
                audio_b64 = response.get("delta", "")
                if audio_b64:
                    chunks.append(base64.b64decode(audio_b64))
            elif event_type == "response.done":
                print("  Response done")
                done_event.set()
            elif event_type == "session.created":
                print("  Session created")

    callback = TestCallback()

    tts = QwenTtsRealtime(
        model=VC_REALTIME_MODEL,
        callback=callback,
        url=WEBSOCKET_URL,
    )

    tts.connect()
    tts.update_session(
        voice=voice_id,
        response_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        mode="server_commit",
    )

    # Send text and finish
    tts.append_text(text)
    tts.finish()

    # Wait for completion
    done_event.wait(timeout=30)

    elapsed = time.time() - start_time
    total_bytes = sum(len(c) for c in chunks)
    duration = total_bytes / (24000 * 2) if total_bytes > 0 else 0

    print(f"  Chunks: {len(chunks)}, Audio: {duration:.1f}s, Time: {elapsed:.1f}s")
    if first_chunk_time:
        print(f"  First-Audio-Delay: {first_chunk_time:.2f}s")

    # Save as WAV
    if chunks:
        output_path = os.path.join(OUTPUT_DIR, f"rt_{name.lower()}.wav")
        pcm_data = b"".join(chunks)
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm_data)
        print(f"  Saved: {output_path}")

    return len(chunks) > 0


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print(f"API Key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"Voices dir: {VOICES_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    # --- Step 1: Check existing enrolled voices ---
    print(f"\n{'='*60}")
    print("Step 1: Checking enrolled voices...")
    print(f"{'='*60}")

    existing = list_enrolled_voices()
    existing_names = {v.get("preferred_name", ""): v.get("voice", "") for v in existing}
    print(f"  Found {len(existing)} enrolled voices")
    for v in existing:
        print(f"    - {v.get('preferred_name')}: {v.get('voice', '')[:50]}...")

    # --- Step 2: Enroll missing voices ---
    print(f"\n{'='*60}")
    print("Step 2: Voice Enrollment")
    print(f"{'='*60}")

    voice_ids: dict[str, str] = {}  # name -> voice_id
    agents = ["AIfred", "Sokrates", "Salomo"]

    for agent in agents:
        wav_path = os.path.join(VOICES_DIR, f"{agent}.wav")
        if not os.path.exists(wav_path):
            print(f"  {agent}: WAV not found at {wav_path}")
            continue

        # Check if already enrolled
        if agent.lower() in existing_names:
            voice_ids[agent] = existing_names[agent.lower()]
            print(f"  {agent}: Already enrolled → {voice_ids[agent][:50]}...")
            continue

        # Enroll for batch model
        vid = enroll_voice(agent, wav_path, VC_TARGET_MODEL)
        if vid:
            voice_ids[agent] = vid

    print(f"\n  Voice IDs: {json.dumps({k: v[:40]+'...' for k, v in voice_ids.items()}, indent=4)}")

    if not voice_ids:
        print("\nNo voices enrolled! Exiting.")
        sys.exit(1)

    # --- Step 3: Test sentence-based streaming with cloned voices ---
    print(f"\n{'='*60}")
    print("Step 3: Sentence-based Streaming with Cloned Voices")
    print(f"{'='*60}")

    test_texts = {
        "AIfred": "Hallo, ich bin AIfred, dein intelligenter Assistent. Wie kann ich dir heute helfen?",
        "Sokrates": "Hast du wirklich alle Aspekte dieser Frage berücksichtigt? Lass mich das kritisch hinterfragen.",
        "Salomo": "Nach Abwägung aller Argumente komme ich zu folgendem Urteil.",
    }

    results = []
    for agent, voice_id in voice_ids.items():
        text = test_texts.get(agent, "Dies ist ein Test.")
        ok = test_vc_streaming(text, voice_id, agent)
        results.append((f"VC-Stream {agent}", ok))

    # --- Step 4: Test realtime WebSocket streaming ---
    print(f"\n{'='*60}")
    print("Step 4: Realtime WebSocket Streaming")
    print(f"{'='*60}")

    # Enroll for realtime model (separate enrollment needed)
    print("  Enrolling for realtime model...")
    rt_voice_ids: dict[str, str] = {}

    for agent in agents:
        wav_path = os.path.join(VOICES_DIR, f"{agent}.wav")
        if not os.path.exists(wav_path):
            continue

        # Check if already enrolled for realtime
        rt_name = f"{agent.lower()}_rt"
        if rt_name in existing_names:
            rt_voice_ids[agent] = existing_names[rt_name]
            print(f"  {agent} (RT): Already enrolled → {rt_voice_ids[agent][:50]}...")
            continue

        vid = enroll_voice(f"{agent.lower()}_rt", wav_path, VC_REALTIME_MODEL)
        if vid:
            rt_voice_ids[agent] = vid

    # Test realtime with first available voice
    if rt_voice_ids:
        agent = list(rt_voice_ids.keys())[0]
        text = test_texts.get(agent, "Dies ist ein Realtime-Test.")
        ok = test_realtime_streaming(text, rt_voice_ids[agent], agent)
        results.append((f"RT-WebSocket {agent}", ok))
    else:
        print("  No realtime voices enrolled, skipping")

    # --- Summary ---
    print(f"\n{'='*60}")
    print("RESULTS:")
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name}")
    print(f"{'='*60}")
    print(f"Audio files in: {OUTPUT_DIR}")

    # Save voice IDs for later use
    ids_file = os.path.join(OUTPUT_DIR, "voice_ids.json")
    with open(ids_file, "w") as f:
        json.dump({"batch": voice_ids, "realtime": rt_voice_ids}, f, indent=2)
    print(f"Voice IDs saved: {ids_file}")
