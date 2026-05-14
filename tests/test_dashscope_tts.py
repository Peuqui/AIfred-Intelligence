"""Standalone test for DashScope Qwen3-TTS Cloud API.

Run: python tests/test_dashscope_tts.py
Requires: DASHSCOPE_API_KEY in .env or environment

NOT a pytest test — functions take explicit args, run via __main__ block.
"""

import os
import sys
import time
import base64

# Load .env from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import dashscope  # noqa: E402 — must be after load_dotenv() to pick up API key

# International endpoint (Singapore)
dashscope.base_http_api_url = "https://dashscope-intl.aliyuncs.com/api/v1"

API_KEY = os.getenv("DASHSCOPE_API_KEY")
if not API_KEY:
    print("DASHSCOPE_API_KEY not set!")
    sys.exit(1)

OUTPUT_DIR = "/tmp/dashscope_tts_test"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def test_non_streaming(text: str, voice: str, language: str, filename: str) -> bool:
    """Test non-streaming TTS (returns complete WAV)."""
    print(f"\n{'='*60}")
    print(f"Non-Streaming Test: voice={voice}, lang={language}")
    print(f"Text: {text[:80]}...")
    print(f"{'='*60}")

    start = time.time()

    response = dashscope.MultiModalConversation.call(
        model="qwen3-tts-flash",
        api_key=API_KEY,
        text=text,
        voice=voice,
        language_type=language,
        stream=False,
    )

    elapsed = time.time() - start

    print(f"Response status: {response.status_code}")
    print(f"Time: {elapsed:.2f}s")

    if response.status_code != 200:
        print(f"Error: {response.message}")
        return False

    # Extract audio data
    audio_data = response.output.audio.data
    if not audio_data:
        print("No audio data received!")
        return False

    # Decode base64 WAV
    wav_bytes = base64.b64decode(audio_data)
    output_path = os.path.join(OUTPUT_DIR, filename)
    with open(output_path, "wb") as f:
        f.write(wav_bytes)

    file_size = os.path.getsize(output_path)
    duration_est = file_size / (24000 * 2)  # 24kHz, 16-bit mono
    print(f"Audio saved: {output_path} ({file_size:,} bytes, ~{duration_est:.1f}s)")
    print(f"Latency: {elapsed:.2f}s for ~{duration_est:.1f}s audio")

    return True


def test_streaming(text: str, voice: str, language: str, filename: str) -> bool:
    """Test streaming TTS (returns PCM chunks)."""
    print(f"\n{'='*60}")
    print(f"Streaming Test: voice={voice}, lang={language}")
    print(f"Text: {text[:80]}...")
    print(f"{'='*60}")

    start = time.time()
    first_chunk_time = None
    chunks = []

    response = dashscope.MultiModalConversation.call(
        model="qwen3-tts-flash",
        api_key=API_KEY,
        text=text,
        voice=voice,
        language_type=language,
        stream=True,
    )

    for chunk in response:
        if chunk.output and chunk.output.audio and chunk.output.audio.data:
            if first_chunk_time is None:
                first_chunk_time = time.time() - start
                print(f"First chunk: {first_chunk_time:.2f}s")
            pcm_bytes = base64.b64decode(chunk.output.audio.data)
            chunks.append(pcm_bytes)

    elapsed = time.time() - start
    total_bytes = sum(len(c) for c in chunks)
    duration_est = total_bytes / (24000 * 2)  # 24kHz, 16-bit mono

    print(f"Chunks received: {len(chunks)}")
    print(f"Total: {total_bytes:,} bytes (~{duration_est:.1f}s audio)")
    print(f"Total time: {elapsed:.2f}s")
    if first_chunk_time:
        print(f"First-Audio-Delay: {first_chunk_time:.2f}s")

    # Save raw PCM (can be played with: ffplay -f s16le -ar 24000 -ac 1 file.pcm)
    output_path = os.path.join(OUTPUT_DIR, filename)
    with open(output_path, "wb") as f:
        for chunk in chunks:
            f.write(chunk)

    print(f"PCM saved: {output_path}")
    print(f"Play with: ffplay -f s16le -ar 24000 -ac 1 {output_path}")

    return len(chunks) > 0


if __name__ == "__main__":
    try:
        print(f"DashScope SDK v{dashscope.version.__version__}")
    except AttributeError:
        print("DashScope SDK (version unknown)")
    print(f"API Key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"Output: {OUTPUT_DIR}")

    results = []

    # Test 1: German non-streaming
    results.append(("DE non-stream", test_non_streaming(
        text="Hallo, ich bin AIfred, dein intelligenter Assistent. Heute ist ein wunderbarer Tag!",
        voice="Cherry",
        language="German",
        filename="test_de_nonstream.wav",
    )))

    # Test 2: German streaming
    results.append(("DE streaming", test_streaming(
        text="Die künstliche Intelligenz entwickelt sich rasant weiter. Sprachsynthese wird immer natürlicher.",
        voice="Cherry",
        language="German",
        filename="test_de_stream.pcm",
    )))

    # Test 3: English non-streaming
    results.append(("EN non-stream", test_non_streaming(
        text="Hello, I am AIfred, your intelligent assistant. How can I help you today?",
        voice="Ethan",
        language="English",
        filename="test_en_nonstream.wav",
    )))

    # Summary
    print(f"\n{'='*60}")
    print("RESULTS:")
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name}")
    print(f"{'='*60}")
    print(f"Audio files in: {OUTPUT_DIR}")
