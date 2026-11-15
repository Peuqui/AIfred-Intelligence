"""Tests for aifred.lib.browser_render (render_html tool backend)."""

import asyncio
import shutil
import uuid

import pytest

from aifred.lib.browser_render import (
    console_message_ignored,
    render_html_in_browser,
    resolve_sandbox_html_path,
    vendor_file,
)
from aifred.plugins.tools.sandbox import _looks_like_html_document

SESSION = uuid.uuid4().hex  # 32-hex like real session ids

_TEST_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<button id="go" onclick="document.getElementById('state').textContent='CLICKED';
console.log('click handled')">Go</button>
<div id="state">initial</div>
<script>console.error('boom'); console.log('ok');</script>
</body></html>"""


def _write_html(out_root, content: str) -> str:
    session_dir = out_root / SESSION
    session_dir.mkdir(parents=True, exist_ok=True)
    from aifred.lib.sandbox import output_filename
    fname = output_filename("seite", content.encode("utf-8"), ".html")
    (session_dir / fname).write_text(content, encoding="utf-8")
    return fname


@pytest.fixture()
def session_html(tmp_path, monkeypatch):
    """Create a sandbox_output/<session>/ dir with one HTML file."""
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    return _write_html(out_root, _TEST_PAGE)


# ── resolve_sandbox_html_path ────────────────────────────────────


def test_resolve_valid_url(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    path = resolve_sandbox_html_path(url, SESSION)
    assert path is not None and path.name == session_html


def test_resolve_with_backend_prefix(session_html):
    url = f"http://localhost:8002/_upload/sandbox_output/{SESSION}/{session_html}"
    path = resolve_sandbox_html_path(url, SESSION)
    assert path is not None and path.name == session_html


def test_resolve_rejects_foreign_session(session_html):
    other = uuid.uuid4().hex
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    assert resolve_sandbox_html_path(url, other) is None


def test_resolve_rejects_traversal(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/../../../etc/passwd"
    assert resolve_sandbox_html_path(url, SESSION) is None


def test_resolve_rejects_bad_filename(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/evil.html"
    assert resolve_sandbox_html_path(url, SESSION) is None


# ── vision_analyze can resolve sandbox screenshots ───────────────


def test_url_to_file_path_resolves_sandbox_output(tmp_path, monkeypatch):
    from aifred.lib import config
    from aifred.lib.vision_utils import url_to_file_path
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    shot = out_root / SESSION / "abcd1234.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"png")
    url = f"/_upload/sandbox_output/{SESSION}/abcd1234.png"
    assert url_to_file_path(url, SESSION) == shot
    # VI7: foreign session must be rejected
    assert url_to_file_path(url, uuid.uuid4().hex) is None


# ── STDOUT HTML guard ────────────────────────────────────────────


def test_html_guard_detects_document():
    assert _looks_like_html_document("<!DOCTYPE html><html>...")
    assert _looks_like_html_document("  \n<html lang=\"de\">")
    assert not _looks_like_html_document("result: 42")
    assert not _looks_like_html_document("use <html> tags for markup")


# ── integration: real headless Chrome via Playwright ─────────────

_chrome_missing = shutil.which("google-chrome") is None


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_render_real_browser(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    result = asyncio.run(render_html_in_browser(url, SESSION, wait_ms=300))
    assert result.error == "" and not result.timed_out
    joined = "\n".join(result.console_messages)
    assert "boom" in joined and "ok" in joined
    assert len(result.screenshot_urls) == 1
    assert result.screenshot_urls[0].endswith(".png")


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_render_with_actions(session_html):
    url = f"/_upload/sandbox_output/{SESSION}/{session_html}"
    result = asyncio.run(
        render_html_in_browser(
            url, SESSION, wait_ms=300,
            actions=[
                {"screenshot": True},          # initial state
                {"click": "#go"},              # changes #state → CLICKED
                {"wait_ms": 100},
                {"click": "#does-not-exist"},  # must land in action_errors
            ],
        )
    )
    assert result.error == "" and not result.timed_out
    joined = "\n".join(result.console_messages)
    assert "click handled" in joined  # click actually executed page JS
    assert len(result.screenshot_urls) == 2  # intermediate + final
    assert len(result.action_errors) == 1
    assert "does-not-exist" in result.action_errors[0]


# ── the local libraries and the console the model gets ───────────

def test_the_vendor_folder_is_read_with_its_sub_folders() -> None:
    for path in ("/vendor/three.min.js", "/vendor/three/three.module.min.js", "/vendor/three/addons/controls/OrbitControls.js"):
        assert vendor_file(path) is not None, path


@pytest.mark.parametrize("path", [
    "/vendor/../README.md", "/vendor/three/../../../etc/passwd", "/vendor/%2e%2e/%2e%2e/README.md",
    "/vendor/three/nothing.js", "/vendor/three", "/elsewhere/three.min.js",
])
def test_a_path_that_leaves_the_vendor_folder_or_leads_to_nothing_is_no_library(path: str) -> None:
    assert vendor_file(path) is None


def test_the_drivers_hint_about_reading_the_canvas_does_not_reach_the_model() -> None:
    hint = "[.WebGL-0x27d4007a0600]GL Driver Message (OpenGL, Performance, GL_CLOSE_PATH_NV, High): GPU stall due to ReadPixels"
    assert console_message_ignored(hint)
    assert not console_message_ignored("Uncaught TypeError: x is not a function")


_MODULE_PAGE = """<!DOCTYPE html><html><body>
<script type="importmap">{"imports":{"three":"/vendor/three/three.module.min.js","three/addons/":"/vendor/three/addons/"}}</script>
<script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
console.log("modules: three " + THREE.REVISION + ", " + typeof OrbitControls);
</script></body></html>"""


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_page_loads_three_and_its_add_ons_as_local_modules(tmp_path, monkeypatch):
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    fname = _write_html(out_root, _MODULE_PAGE)
    result = asyncio.run(render_html_in_browser(f"/_upload/sandbox_output/{SESSION}/{fname}", SESSION, wait_ms=500))
    assert result.error == "" and not result.timed_out
    assert result.console_messages == ["log: modules: three 160, function"]


_PHYSICS_PAGE = """<!DOCTYPE html><html><body>
<script type="importmap">{"imports":{"rapier":"/vendor/rapier/rapier.module.js"}}</script>
<script type="module">
import RAPIER from "rapier";
await RAPIER.init();
const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
const ball = world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(0, 10, 0));
world.createCollider(RAPIER.ColliderDesc.ball(0.5), ball);
for (let i = 0; i < 60; i++) world.step();
console.log("fell: " + (ball.translation().y < 10 && ball.translation().y > 0));
</script></body></html>"""


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_page_runs_the_local_physics_engine(tmp_path, monkeypatch):
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    fname = _write_html(out_root, _PHYSICS_PAGE)
    result = asyncio.run(render_html_in_browser(f"/_upload/sandbox_output/{SESSION}/{fname}", SESSION, wait_ms=1000))
    assert result.error == "" and not result.timed_out
    assert result.console_messages == ["log: fell: true"]


_FIGURE_PAGE = """<!DOCTYPE html><html><body>
<script type="importmap">{"imports":{"three":"/vendor/three/three.module.min.js","three/addons/":"/vendor/three/addons/"}}</script>
<script type="module">
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FontLoader } from "three/addons/loaders/FontLoader.js";
import { TextGeometry } from "three/addons/geometries/TextGeometry.js";
const gltf = await new GLTFLoader().loadAsync("/vendor/models/RobotExpressive/RobotExpressive.glb");
console.log("clips: " + gltf.animations.length + ", " + gltf.animations.some(clip => clip.name === "Walking"));
const font = await new FontLoader().loadAsync("/vendor/three/fonts/helvetiker_regular.typeface.json");
console.log("text: " + (new TextGeometry("V8", { font, size: 1, depth: 0.2 }).attributes.position.count > 0));
</script></body></html>"""


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_page_loads_the_free_figure_and_a_font(tmp_path, monkeypatch):
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    fname = _write_html(out_root, _FIGURE_PAGE)
    result = asyncio.run(render_html_in_browser(f"/_upload/sandbox_output/{SESSION}/{fname}", SESSION, wait_ms=1000))
    assert result.error == "" and not result.timed_out
    assert result.console_messages == ["log: clips: 14, true", "log: text: true"]

