"""Audio Player API (file streaming + position sync)."""

import asyncio
from pathlib import Path
from typing import Optional, Dict

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .app import api_app

# In-memory map: session_id -> last requested state_key (for re-resolve from JS)
_audio_active: Dict[str, str] = {}


def _audio_resolver():  # type: ignore[no-untyped-def]
    """Build a fresh SourceResolver: filesystem-discovery + http_streams.

    Mirrors the plugin's _make_resolver() — local sources come from the
    data/media/audio/ filesystem (folders + symlinks), HTTP streams from
    plugin settings.json. Without this, the file endpoint would only see
    http_stream entries and 404 on every NAS-mounted source.
    """
    import json as _json
    from pathlib import Path as _Path
    from ..audio_sources import SourceResolver, build_source_map
    from ..config import MEDIA_AUDIO_DIR

    settings_path = (
        _Path(__file__).parent.parent.parent
        / "plugins" / "tools" / "audio_player" / "settings.json"
    )
    streams: Dict[str, Dict[str, str]] = {}
    if settings_path.exists():
        try:
            with open(settings_path, encoding="utf-8") as f:
                data = _json.load(f)
            if isinstance(data, dict):
                streams = {
                    label: src
                    for label, src in data.get("sources", {}).items()
                    if src.get("type") == "http_stream"
                }
        except (OSError, _json.JSONDecodeError):
            pass
    sources = build_source_map(MEDIA_AUDIO_DIR, streams)
    return SourceResolver(sources)


@api_app.get("/audio/file", tags=["Audio"])
async def audio_file(request: Request, key: str):
    """Stream an audio file by state_key. Supports HTTP Range for seeking.

    The key is resolved against the audio_player plugin's source map, so
    the LLM never sees raw paths. Path-traversal is rejected by the resolver.
    """
    from fastapi.responses import StreamingResponse, RedirectResponse
    from ..audio_sources import ALLOWED_EXTENSIONS

    try:
        src = _audio_resolver().resolve(key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # HTTP streams: redirect the browser to the upstream URL — the browser
    # opens the connection itself, no proxying needed.
    if src.is_stream:
        return RedirectResponse(src.uri, status_code=302)

    file_path = Path(src.uri)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="unsupported audio extension")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")

    # Map extension → MIME type for HTML5 <audio>
    mime_map = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".flac": "audio/flac", ".m4a": "audio/mp4", ".opus": "audio/ogg",
        ".aac": "audio/aac", ".mp4": "audio/mp4", ".webm": "audio/webm",
    }
    media_type = mime_map.get(file_path.suffix.lower(), "application/octet-stream")

    chunk_size = 64 * 1024

    if range_header:
        # Parse "bytes=start-end" (RFC 7233, incl. suffix form "bytes=-N")
        try:
            unit, _, ranges = range_header.partition("=")
            if unit.strip().lower() != "bytes":
                raise ValueError("only bytes ranges supported")
            start_str, _, end_str = ranges.partition("-")
            if start_str:
                start = int(start_str)
                # Oversized end clamps to EOF per RFC — no 416 for that.
                end = min(int(end_str), file_size - 1) if end_str else file_size - 1
            elif end_str:
                # Suffix range "bytes=-N" = the LAST N bytes. Clients probe
                # file tails with this (m4a/mp4 moov lookup) — treating it
                # as start=0 served the wrong bytes with a valid-looking 206.
                suffix_len = int(end_str)
                if suffix_len <= 0:
                    raise ValueError("invalid suffix length")
                start = max(0, file_size - suffix_len)
                end = file_size - 1
            else:
                raise ValueError("empty range")
            if start >= file_size or start > end:
                raise HTTPException(status_code=416, detail="range not satisfiable")
        except (ValueError, AttributeError):
            raise HTTPException(status_code=400, detail="invalid range header")

        length = end - start + 1

        async def iter_range():  # type: ignore[no-untyped-def]
            # File I/O via to_thread — a stalling medium (NFS, USB) must not
            # block the whole event loop.
            def _open_seeked():  # type: ignore[no-untyped-def]
                f = open(file_path, "rb")
                f.seek(start)
                return f

            f = await asyncio.to_thread(_open_seeked)
            try:
                remaining = length
                while remaining > 0:
                    data = await asyncio.to_thread(f.read, min(chunk_size, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                await asyncio.to_thread(f.close)

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-cache",
        }
        return StreamingResponse(iter_range(), status_code=206, media_type=media_type, headers=headers)

    # Full file response (with Accept-Ranges so the browser can seek later)
    async def iter_full():  # type: ignore[no-untyped-def]
        f = await asyncio.to_thread(open, file_path, "rb")
        try:
            while True:
                data = await asyncio.to_thread(f.read, chunk_size)
                if not data:
                    break
                yield data
        finally:
            await asyncio.to_thread(f.close)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(iter_full(), media_type=media_type, headers=headers)


class AudioPositionRequest(BaseModel):
    """Browser → server: persist current playback position for resume."""
    state_key: str
    pos_sec: float
    duration_sec: Optional[float] = None
    completed: bool = False


@api_app.get("/audio/test", tags=["Audio"], response_class=HTMLResponse)
async def audio_test_page(key: str = "music/05-Ausgefressen.mp3"):
    """Standalone test page — verifies endpoint + browser playback without LLM/State."""
    import html as _html
    from urllib.parse import quote
    safe_key = _html.escape(key)
    audio_src = f"/api/audio/file?key={quote(key)}"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AIfred Audio Test</title>
<style>body{{font-family:system-ui;max-width:720px;margin:40px auto;padding:0 16px;background:#1a1a1a;color:#ddd}}
h1{{font-size:18px;color:#4287f5}} code{{background:#333;padding:2px 6px;border-radius:3px}}
audio{{width:100%;margin:16px 0}} .ok{{color:#4ade80}} .err{{color:#f87171}}</style>
</head><body>
<h1>Audio-Endpoint Test</h1>
<p>State-Key: <code>{safe_key}</code></p>
<p>Source URL: <code>{audio_src}</code></p>
<audio id="testplayer" controls preload="metadata" src="{audio_src}"></audio>
<p>Status: <span id="status">loading…</span></p>
<pre id="log" style="background:#222;padding:10px;border-radius:4px;font-size:12px;max-height:300px;overflow:auto"></pre>
<script>
const a = document.getElementById('testplayer');
const s = document.getElementById('status');
const L = document.getElementById('log');
function log(msg, cls) {{
  const t = new Date().toLocaleTimeString();
  L.textContent += `[${{t}}] ${{msg}}\\n`;
  if (cls) {{ s.textContent = msg; s.className = cls; }}
}}
['loadstart','loadedmetadata','canplay','play','playing','pause','ended','error','stalled','waiting'].forEach(ev => {{
  a.addEventListener(ev, e => log(`event: ${{ev}} | currentTime=${{a.currentTime.toFixed(2)}} | duration=${{isFinite(a.duration)?a.duration.toFixed(1):'?'}} | networkState=${{a.networkState}} | readyState=${{a.readyState}}`));
}});
a.addEventListener('error', () => {{
  const e = a.error;
  log(`ERROR code=${{e?e.code:'?'}} message=${{e?e.message:'?'}}`, 'err');
}});
a.addEventListener('canplay', () => log('✅ canplay — pressing play', 'ok'));
a.addEventListener('play', () => log('▶ playing', 'ok'));
fetch("{audio_src}", {{method:'HEAD'}}).then(r => log(`HEAD response: HTTP ${{r.status}} | content-type=${{r.headers.get('content-type')}} | content-length=${{r.headers.get('content-length')}}`));
</script>
</body></html>"""


@api_app.post("/audio/position", tags=["Audio"])
async def audio_position(req: AudioPositionRequest):
    """Update audio_state.json from the browser's currentTime."""
    from ..audio_state import audio_state
    if req.completed:
        audio_state.mark_completed(req.state_key)
        return {"status": "ok", "completed": True}
    # Resolve URI for the state_key (best-effort; failures are non-fatal —
    # the URI is informational, not authoritative)
    try:
        src = _audio_resolver().resolve(req.state_key)
        uri = src.uri
    except ValueError:
        uri = ""
    audio_state.update(
        key=req.state_key,
        uri=uri,
        pos_sec=float(req.pos_sec),
        duration_sec=float(req.duration_sec) if req.duration_sec else None,
    )
    return {"status": "ok"}
