"""Sandboxed Python code execution via subprocess.

Runs user-provided Python code in an isolated subprocess with:
- Resource limits (RAM via RLIMIT_AS, CPU via RLIMIT_CPU)
- Timeout enforcement
- Matplotlib auto-capture
- HTML output detection (for interactive visualizations)

Output files are stored in data/sandbox_output/{session_id}/ for
session-scoped cleanup (like images in data/images/{session_id}/).
"""

import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    DOCUMENTS_DIR,
    SANDBOX_MAX_OUTPUT_BYTES,
    SANDBOX_MAX_RAM_MB,
    SANDBOX_TIMEOUT_SECONDS,
    SANDBOX_WORK_DIR,
)

from .logging_utils import log_message


@dataclass
class SandboxResult:
    """Result of a sandboxed code execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    images: list[str] = field(default_factory=list)  # URLs to plot images
    html_url: str = ""  # URL to interactive HTML output
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


def _session_output_dir(session_id: str) -> Path:
    """Get or create session-specific sandbox output directory."""
    from .config import SANDBOX_OUTPUT_DIR
    session_dir = SANDBOX_OUTPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _sandbox_url(session_id: str, filename: str) -> str:
    """Build full URL for a sandbox output file (respects BACKEND_URL)."""
    from .config import BACKEND_URL
    relative = f"/_upload/sandbox_output/{session_id}/{filename}"
    return f"{BACKEND_URL}{relative}" if BACKEND_URL else relative


def _collect_images(work_dir: Path, session_id: str) -> list[str]:
    """Collect generated plot images, save to sandbox_output/{session_id}/, return URLs."""
    output_dir = _session_output_dir(session_id)

    urls: list[str] = []
    for png in sorted(work_dir.glob("__plot_*.png")):
        filename = f"{uuid.uuid4().hex[:8]}.png"
        shutil.copy2(png, output_dir / filename)
        urls.append(_sandbox_url(session_id, filename))
    return urls


def _collect_html(work_dir: Path, session_id: str) -> str:
    """Find HTML output file and persist it in sandbox_output/{session_id}/.

    Returns URL to the served file, or empty string if no HTML found.
    """
    all_files = list(work_dir.iterdir())
    log_message(f"Sandbox _collect_html: files={[f.name for f in all_files]}")

    html_file = work_dir / "output.html"
    if not html_file.exists():
        html_files = [f for f in work_dir.glob("*.html") if f.name != "__main.py"]
        if html_files:
            html_file = html_files[0]
        else:
            log_message("Sandbox _collect_html: no HTML file found")
            return ""

    html_content = html_file.read_text(encoding="utf-8")
    if not html_content.strip():
        log_message("Sandbox _collect_html: HTML file is empty")
        return ""

    output_dir = _session_output_dir(session_id)
    filename = f"{uuid.uuid4().hex[:8]}.html"
    (output_dir / filename).write_text(html_content, encoding="utf-8")

    url = _sandbox_url(session_id, filename)
    log_message(f"Sandbox: HTML output saved → {url}")
    return url


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
        "--ro-bind", "/etc", "/etc",
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

    try:
        proc = await asyncio.create_subprocess_exec(
            *bwrap_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_set_resource_limits,
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
        result.html_url = _collect_html(work_dir, sid)

        log_message(
            f"Sandbox({exec_id}): exit={result.exit_code}, "
            f"stdout={len(result.stdout)}b, stderr={len(result.stderr)}b, "
            f"images={len(result.images)}, html_url={result.html_url!r}"
        )

    except Exception as e:
        result.stderr = f"Sandbox error: {e}"
        result.exit_code = -1
        log_message(f"Sandbox({exec_id}): {e}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result


def cleanup_session_sandbox(session_id: str) -> int:
    """Delete all sandbox output files for a session. Returns count of deleted files."""
    from .config import SANDBOX_OUTPUT_DIR
    session_dir = SANDBOX_OUTPUT_DIR / session_id
    if not session_dir.exists():
        return 0
    count = sum(1 for _ in session_dir.iterdir())
    shutil.rmtree(session_dir, ignore_errors=True)
    return count


def _set_resource_limits() -> None:
    """Set resource limits for the subprocess (called via preexec_fn)."""
    import resource

    max_bytes = SANDBOX_MAX_RAM_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (SANDBOX_TIMEOUT_SECONDS, SANDBOX_TIMEOUT_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
