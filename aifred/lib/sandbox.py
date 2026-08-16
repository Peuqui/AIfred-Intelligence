"""Sandboxed Python code execution via subprocess.

Runs user-provided Python code in an isolated subprocess with:
- Resource limits (RAM via RLIMIT_AS, CPU via RLIMIT_CPU)
- Timeout enforcement
- Matplotlib auto-capture
- HTML output detection (for interactive visualizations)

Output files are stored in data/sandbox_output/{session_id}/ for
session-scoped cleanup (like images in data/upload/images/{session_id}/).
"""

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import (
    DOCUMENTS_DIR,
    SANDBOX_MAX_FILE_SIZE_MB,
    SANDBOX_MAX_OUTPUT_BYTES,
    SANDBOX_MAX_PROCESSES,
    SANDBOX_MAX_RAM_MB,
    SANDBOX_TIMEOUT_SECONDS,
    SANDBOX_WORK_DIR,
)

from .logging_utils import log_message
from .session_storage import SESSION_ID_RE


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    images: list[str] = field(default_factory=list)   # URLs to plot images
    html_urls: list[str] = field(default_factory=list)  # URLs to interactive HTMLs
    timed_out: bool = False


_IMPORT_GUARD = '''
# Sandbox: no import restrictions (resource limits provide safety)
'''

_MATPLOTLIB_HOOK = '''
import sys as _sys
if "matplotlib" in _sys.modules or "matplotlib.pyplot" in _sys.modules:
    import matplotlib.pyplot as _plt
    for _fig_num in _plt.get_fignums():
        _fig = _plt.figure(_fig_num)
        _fig.savefig(f"__plot_{_fig_num}.png", dpi=150, bbox_inches="tight")
    _plt.close("all")
'''


def _build_wrapper_script(code: str, work_dir: Path) -> str:
    """Build the full Python script with guards and hooks.

    Note: CWD is set by the bwrap --chdir flag; no os.chdir here.
    """
    parts = [
        _IMPORT_GUARD,
        code,
        _MATPLOTLIB_HOOK,
    ]
    return "\n".join(parts)


def _safe_session_subdir(session_id: str) -> Optional[Path]:
    """Return the per-session SANDBOX_OUTPUT_DIR subpath if the id is safe.

    Returns None when ``session_id`` is not a 32-hex string or, after
    resolve(), would escape SANDBOX_OUTPUT_DIR. The id format matches
    ``session_storage._sanitize_session_id`` so cookie-supplied values
    that fail elsewhere also fail here.
    """
    from .config import SANDBOX_OUTPUT_DIR
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return None
    root = SANDBOX_OUTPUT_DIR.resolve()
    candidate = (SANDBOX_OUTPUT_DIR / session_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _session_output_dir(session_id: str) -> Path:
    """Get or create session-specific sandbox output directory."""
    session_dir = _safe_session_subdir(session_id)
    if session_dir is None:
        raise ValueError(f"Unsafe session_id for sandbox output: {session_id!r}")
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _sandbox_url(session_id: str, filename: str) -> str:
    """Build full URL for a sandbox output file (respects BACKEND_URL)."""
    from .config import BACKEND_URL
    relative = f"/_upload/sandbox_output/{session_id}/{filename}"
    return f"{BACKEND_URL}{relative}" if BACKEND_URL else relative


# Browser screenshots are a by-product of render_html — proof that a page
# loaded, worthless once the chat is gone. Plots and HTML are the model's
# actual deliverables and outlive their session. This prefix is the only
# thing that tells the two apart on disk, so it is the SSOT for both the
# writer (browser_render) and the deletion path below.
SCREENSHOT_PREFIX = "shot_"

# Line markers the sandbox tools emit in their result text so downstream
# consumers (llm_pipeline UI-embed, screenshot description below) can find
# the produced artifacts. SSOT — the plugin and every parser use these.
SANDBOX_HTML_URL_MARKER = "SANDBOX_HTML_URL: "
SANDBOX_IMAGE_URL_MARKER = "SANDBOX_IMAGE_URL: "
# Transport line from render_html to describe_sandbox_screenshots: carries
# the caller's verification question for the screenshot description. Parsed
# and stripped there — never reaches the main model.
SANDBOX_VISION_FOCUS_MARKER = "SANDBOX_VISION_FOCUS: "


def _configured_vision_model() -> Optional[str]:
    """Effective model id of the "vision" agent role, or None if unset.

    ``get_effective_model_from_settings("vision")`` inherits AIfred's model
    when the role is empty — right for text agents sharing AIfred's LLM,
    wrong here (a text model cannot describe images). So check the raw
    ``backend_models`` entry first, mirroring the emptiness check the
    browser path uses (``_image_mixin``).
    """
    from .config import get_effective_model_from_settings
    from .settings import load_settings
    settings = load_settings() or {}
    backend_type = settings.get("backend_type", "llamacpp")
    if not settings.get("backend_models", {}).get(backend_type, {}).get("vision", ""):
        return None
    return get_effective_model_from_settings("vision")


async def describe_sandbox_screenshots(
    result_text: str, session_id: str, main_model: str
) -> tuple[str, list[str]]:
    """Append VLM text descriptions for sandbox images to a tool result.

    A text-only model cannot see the screenshots/plots that render_html and
    execute_code produce — the tool result only carries their URLs for the
    UI embed. This feeds the model a text description instead. Describer
    choice (SSOT ``is_vision_model_sync``): a vision-capable main model
    describes its own screenshots (already loaded — no swap, no second
    model in VRAM); otherwise the configured "vision" agent role. Neither
    available → an explicit note in the tool result so the model tells the
    user that visual verification is not possible right now.

    Returns ``(amended_result_text, debug_messages)``. Debug messages are
    English (dev-facing) and meant to be yielded as ``{"type": "debug"}``
    events by the caller; model-facing text comes from prompt files in the
    active conversation language.
    """
    from datetime import datetime

    from .prompt_loader import load_prompt
    from .vision_utils import is_vision_model_sync, url_to_file_path

    # Extract the caller's verification question (transport line from
    # render_html) and strip it — it steers the describer prompt, the main
    # model gets the answer, not the marker.
    focus = ""
    if SANDBOX_VISION_FOCUS_MARKER in result_text:
        kept_lines: list[str] = []
        for line in result_text.split("\n"):
            if line.startswith(SANDBOX_VISION_FOCUS_MARKER):
                focus = line[len(SANDBOX_VISION_FOCUS_MARKER):].strip()
            else:
                kept_lines.append(line)
        result_text = "\n".join(kept_lines)

    urls = [
        line[len(SANDBOX_IMAGE_URL_MARKER):].strip()
        for line in result_text.split("\n")
        if line.startswith(SANDBOX_IMAGE_URL_MARKER)
    ]
    if not urls:
        return result_text, []

    debug_msgs: list[str] = []

    if is_vision_model_sync(main_model):
        describer = main_model
        debug_msgs.append(
            f"🖼️ Describing {len(urls)} sandbox image(s) via vision-capable "
            f"main model: {main_model}"
        )
    else:
        vision_model = _configured_vision_model()
        if not vision_model:
            debug_msgs.append(
                "⚠️ Sandbox screenshot description unavailable: main model "
                f"'{main_model}' is not vision-capable and no vision role "
                "model is configured"
            )
            note = load_prompt("vision/sandbox_screenshot_unavailable")
            return f"{result_text}\n\n{note}", debug_msgs
        describer = vision_model
        debug_msgs.append(
            f"🖼️ Describing {len(urls)} sandbox image(s) via vision role "
            f"model: {describer}"
        )

    vlm_prompt = load_prompt("vision/sandbox_screenshot")
    if focus:
        focus_prompt = load_prompt("vision/sandbox_screenshot_focus", question=focus)
        vlm_prompt = f"{vlm_prompt}\n\n{focus_prompt}"
        debug_msgs.append(f"🎯 Vision focus question: {focus[:100]}")
    parts: list[str] = [result_text]
    for url in urls:
        path = url_to_file_path(url, session_id)
        if path is None or not path.exists():
            debug_msgs.append(f"⚠️ Sandbox image path resolution failed: {url}")
            continue
        # Failures surface loudly in BOTH channels (tool result + debug) —
        # agreed error path, no silent skip. Raising instead would kill the
        # whole chat stream over a missing description.
        try:
            from .frame_sources import Frame
            from .vision_analyzer import analyze_frame
            frame = Frame(
                source_id=f"sandbox/{path.name}",
                timestamp=datetime.now(),
                image_bytes=path.read_bytes(),
                format=path.suffix.lstrip(".").lower() or "png",
            )
            # max_pixels=0: no downscale — rendered UI text/layout detail
            # matters and the render viewport bounds the size anyway.
            analysis = await analyze_frame(
                frame, vlm_prompt, model=describer, max_pixels=0
            )
            parts.append(load_prompt(
                "vision/sandbox_screenshot_result",
                filename=path.name,
                description=analysis.text.strip(),
            ))
            debug_msgs.append(
                f"🖼️ Sandbox image described: {path.name} "
                f"({len(analysis.text)} chars, {analysis.duration_ms:.0f} ms)"
            )
        except Exception as e:  # noqa: BLE001
            parts.append(load_prompt(
                "vision/sandbox_screenshot_error",
                filename=path.name,
                error=str(e),
            ))
            debug_msgs.append(
                f"⚠️ Sandbox image description failed for {path.name}: {e}"
            )

    return "\n\n".join(parts), debug_msgs


def _collect_images(work_dir: Path, session_id: str) -> list[str]:
    """Collect generated plot images, save to sandbox_output/{session_id}/, return URLs."""
    output_dir = _session_output_dir(session_id)

    urls: list[str] = []
    for png in sorted(work_dir.glob("__plot_*.png")):
        filename = f"{uuid.uuid4().hex[:8]}.png"
        shutil.copy2(png, output_dir / filename)
        urls.append(_sandbox_url(session_id, filename))
    return urls


def _collect_html(work_dir: Path, session_id: str) -> list[str]:
    """Persist every HTML produced in ``work_dir`` and return their URLs.

    Reads ``output.html`` first (legacy single-file convention) followed by
    every other ``*.html`` in alphabetical order. Empty files are skipped.
    Each kept file is copied to ``sandbox_output/{session_id}/`` under a
    fresh hex name so different runs don't collide.
    """
    all_files = list(work_dir.iterdir())
    log_message(f"Sandbox _collect_html: files={[f.name for f in all_files]}")

    candidates: list[Path] = []
    primary = work_dir / "output.html"
    if primary.exists():
        candidates.append(primary)
    for extra in sorted(work_dir.glob("*.html")):
        if extra.name == "__main.py" or extra in candidates:
            continue
        candidates.append(extra)

    urls: list[str] = []
    output_dir = _session_output_dir(session_id)
    for html_file in candidates:
        try:
            html_content = html_file.read_text(encoding="utf-8")
        except OSError as e:
            log_message(f"Sandbox _collect_html: cannot read {html_file.name}: {e}")
            continue
        if not html_content.strip():
            continue
        filename = f"{uuid.uuid4().hex[:8]}.html"
        (output_dir / filename).write_text(html_content, encoding="utf-8")
        urls.append(_sandbox_url(session_id, filename))

    if not urls:
        log_message("Sandbox _collect_html: no HTML file found")
    else:
        log_message(f"Sandbox: {len(urls)} HTML output(s) saved")
    return urls


# Extensions that we'll inline-embed when written into the documents mount.
_DOCS_HTML_EXTS = {".html", ".htm"}
_DOCS_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _collect_documents_changes(start_time: float) -> tuple[list[str], list[str]]:
    """Return URLs for HTML/image files written to ``DOCUMENTS_DIR`` after
    ``start_time``.

    ``execute_code_write`` mounts ``data/documents/`` read-write, so the
    model can produce named artifacts (``mandelbrot_3d_interactive.html``
    etc.). Those don't land in the sandbox temp dir — collecting them
    here closes the gap so they get embedded in the chat just like
    ``output.html``.

    Files served via the existing ``/_upload/documents/<name>`` mount;
    no copy needed.
    """
    if not DOCUMENTS_DIR.exists():
        return [], []
    html_urls: list[str] = []
    image_urls: list[str] = []
    for path in sorted(DOCUMENTS_DIR.iterdir()):
        # Skip symlinks: sandboxed code (execute_code_write) can create a symlink
        # in documents/ pointing at an arbitrary host path. Following it here (or
        # in the non-resolving document-browser layer) would expose files outside
        # documents/. Only embed real files this run produced.
        if path.is_symlink() or not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < start_time:
            continue
        ext = path.suffix.lower()
        url = f"/_upload/documents/{path.name}"
        if ext in _DOCS_HTML_EXTS:
            html_urls.append(url)
        elif ext in _DOCS_IMAGE_EXTS:
            image_urls.append(url)
    if html_urls or image_urls:
        log_message(
            f"Sandbox: documents/ produced {len(html_urls)} HTML, "
            f"{len(image_urls)} image(s)"
        )
    return html_urls, image_urls


async def execute_sandboxed_code(
    code: str,
    session_id: str = "",
    allow_write: bool = False,
) -> SandboxResult:
    """Execute Python code in a bubblewrap-sandboxed subprocess.

    Args:
        code: Python code to execute
        session_id: Session ID for output file organization and cleanup
        allow_write: If True, documents/ is mounted read-write (needs higher tier).
                     If False (default), documents/ is read-only.
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        result = SandboxResult()
        result.stderr = (
            "Sandbox error: bubblewrap (bwrap) not installed. "
            "Install with: sudo apt install bubblewrap"
        )
        result.exit_code = -1
        log_message("Sandbox: bwrap not available — refusing to execute")
        return result

    exec_id = uuid.uuid4().hex[:12]
    work_dir = Path(SANDBOX_WORK_DIR) / exec_id
    work_dir.mkdir(parents=True, exist_ok=True)

    script = _build_wrapper_script(code, work_dir)
    script_path = work_dir / "__main.py"
    script_path.write_text(script, encoding="utf-8")

    project_root = Path(__file__).parent.parent.parent
    venv_path = project_root / "venv"
    venv_python = str(venv_path / "bin" / "python3")
    if not Path(venv_python).exists():
        venv_python = shutil.which("python3") or "python3"
    site_packages = venv_path / "lib" / "python3.12" / "site-packages"

    # Inside the sandbox: work_dir is mounted at /work, documents at /work/documents
    sandbox_work = "/work"
    sandbox_script = f"{sandbox_work}/__main.py"
    sandbox_docs = f"{sandbox_work}/documents"

    bwrap_args: list[str] = [
        bwrap,
        "--die-with-parent",
        "--unshare-all",             # user, pid, net, ipc, uts, cgroup, mount
        "--new-session",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--ro-bind", "/usr", "/usr",
        # /etc NICHT komplett binden (Info-Disclosure: /etc/ssh, Server-
        # Configs, Hostnamen …) — nur was Python/matplotlib tatsächlich
        # brauchen. Netz ist eh unshared, CA-Pfade sind Defense-in-Depth.
        # *-try: fehlende Pfade (andere Distros) sind kein Fehler.
        "--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--ro-bind-try", "/etc/alternatives", "/etc/alternatives",
        "--ro-bind-try", "/etc/localtime", "/etc/localtime",
        "--ro-bind-try", "/etc/timezone", "/etc/timezone",
        "--ro-bind-try", "/etc/passwd", "/etc/passwd",
        "--ro-bind-try", "/etc/group", "/etc/group",
        "--ro-bind-try", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
        "--ro-bind-try", "/etc/fonts", "/etc/fonts",
        "--ro-bind-try", "/etc/ssl", "/etc/ssl",
        "--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates",
        "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib",
    ]
    if Path("/lib64").exists():
        bwrap_args += ["--symlink", "usr/lib64", "/lib64"]
    # Venv Python interpreter + site-packages (read-only)
    bwrap_args += ["--ro-bind", str(venv_path), str(venv_path)]
    # Work dir (read-write) for script + outputs
    bwrap_args += ["--bind", str(work_dir), sandbox_work]
    # Documents: read-only (default) or read-write (elevated tier)
    if DOCUMENTS_DIR.exists():
        mount_flag = "--bind" if allow_write else "--ro-bind"
        bwrap_args += [mount_flag, str(DOCUMENTS_DIR), sandbox_docs]
    # Environment + chdir
    bwrap_args += [
        "--chdir", sandbox_work,
        "--setenv", "HOME", sandbox_work,
        "--setenv", "MPLBACKEND", "Agg",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONUNBUFFERED", "1",
        "--setenv", "PYTHONPATH", str(site_packages) if site_packages.exists() else "",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--",
        venv_python, sandbox_script,
    ]

    log_message(
        f"Sandbox({exec_id}): executing {len(code)} chars "
        f"(docs={'rw' if allow_write else 'ro'})"
    )

    result = SandboxResult()
    sid = session_id or "unknown"
    # Snapshot before exec so we can pick up only files the model wrote
    # during THIS call (older artifacts in documents/ stay invisible).
    import time as _time
    docs_snapshot_time = _time.time()

    try:
        # NPROC-Budget: aktuelle Tasks der UID + erlaubte Sandbox-Kinder
        # (RLIMIT_NPROC zählt Tasks pro UID — siehe _set_resource_limits).
        nproc_limit = _count_user_tasks() + SANDBOX_MAX_PROCESSES
        proc = await asyncio.create_subprocess_exec(
            *bwrap_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: _set_resource_limits(nproc_limit),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=SANDBOX_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            result.timed_out = True
            result.stderr = f"Execution timed out after {SANDBOX_TIMEOUT_SECONDS}s"
            result.exit_code = -9
            log_message(f"Sandbox({exec_id}): timed out")
            return result

        result.stdout = stdout_bytes.decode("utf-8", errors="replace")[:SANDBOX_MAX_OUTPUT_BYTES]
        result.stderr = stderr_bytes.decode("utf-8", errors="replace")[:SANDBOX_MAX_OUTPUT_BYTES]
        result.exit_code = proc.returncode or 0
        result.images = _collect_images(work_dir, sid)
        result.html_urls = _collect_html(work_dir, sid)
        # When documents/ is mounted read-write, also surface any HTML/
        # image artifacts the model wrote there during this run. Only
        # files newer than docs_snapshot_time are picked up.
        if allow_write:
            doc_html, doc_images = _collect_documents_changes(docs_snapshot_time)
            result.html_urls.extend(doc_html)
            result.images.extend(doc_images)

        log_message(
            f"Sandbox({exec_id}): exit={result.exit_code}, "
            f"stdout={len(result.stdout)}b, stderr={len(result.stderr)}b, "
            f"images={len(result.images)}, html_urls={len(result.html_urls)}"
        )

    except Exception as e:
        result.stderr = f"Sandbox error: {e}"
        result.exit_code = -1
        log_message(f"Sandbox({exec_id}): {e}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result


def cleanup_session_screenshots(session_id: str) -> int:
    """Delete a session's browser screenshots, keep its deliverables.

    Used when a session is deleted: plots and generated HTML stay (the user
    curates those in the storage tab), the render_html screenshots go. The
    session directory is removed once it holds nothing else.

    Returns count of deleted files.
    """
    session_dir = _safe_session_subdir(session_id)
    if session_dir is None or not session_dir.exists():
        return 0
    count = 0
    for shot in session_dir.glob(f"{SCREENSHOT_PREFIX}*.png"):
        shot.unlink()
        count += 1
    if not any(session_dir.iterdir()):
        session_dir.rmdir()
    return count


def cleanup_session_sandbox(session_id: str) -> int:
    """Delete all sandbox output files for a session. Returns count of deleted files."""
    session_dir = _safe_session_subdir(session_id)
    if session_dir is None or not session_dir.exists():
        return 0
    count = sum(1 for _ in session_dir.iterdir())
    shutil.rmtree(session_dir, ignore_errors=True)
    return count


def _count_user_tasks() -> int:
    """Anzahl laufender Tasks (Threads!) der eigenen UID.

    RLIMIT_NPROC zählt im Kernel Tasks, nicht Prozesse — der AIfred-User hat
    typischerweise einige hundert Threads (Reflex-Worker, Torch, …). Ein
    Budget auf Prozessbasis läge weit darunter und ließe bwraps clone() mit
    EAGAIN scheitern."""
    uid = os.getuid()
    count = 0
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            if p.stat().st_uid == uid:
                count += len(os.listdir(p / "task"))
        except OSError:
            continue
    return count


def _set_resource_limits(nproc_limit: int) -> None:
    """Set resource limits for the subprocess (called via preexec_fn)."""
    import resource

    max_bytes = SANDBOX_MAX_RAM_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (SANDBOX_TIMEOUT_SECONDS, SANDBOX_TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # RLIMIT_AS is per-process — a fork bomb multiplies past it. Cap the process
    # count so N children can't each claim the full RAM limit.
    # ACHTUNG: RLIMIT_NPROC zählt TASKS (Threads) systemweit pro UID, nicht
    # pro Prozessbaum — ein absolutes Limit unterhalb der aktuellen Task-Zahl
    # des Users lässt schon bwraps clone() mit EAGAIN scheitern (Sandbox
    # komplett tot; genau so war der ursprüngliche WS2-Fix kaputt). Der
    # Caller übergibt daher aktuelle Task-Zahl + SANDBOX_MAX_PROCESSES.
    resource.setrlimit(resource.RLIMIT_NPROC, (nproc_limit, nproc_limit))
    # RLIMIT_FSIZE: cap single-file writes so sandboxed code can't fill the host
    # disk / tmpfs (RAM) with a giant file (no disk quota exists otherwise).
    max_file_bytes = SANDBOX_MAX_FILE_SIZE_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
