"""Tests for aifred.lib.browser_render (render_html tool backend)."""

import asyncio
import shutil
import uuid

import pytest

from aifred.lib.browser_render import (
    RenderResult,
    _apply_action,
    console_message_ignored,
    render_html_in_browser,
    resolve_sandbox_html_path,
)
from aifred.lib.vendor_files import vendor_file
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



# ── evaluate: values out of the running page ─────────────────────

_SIMULATION_PAGE = """<!DOCTYPE html><html><body>
<script>
const rod = {length: 2.5};
let frame = 0;
setInterval(() => { frame += 1; }, 10);
function crankPin(angle) { return {x: Math.cos(angle), y: Math.sin(angle)}; }
</script>
<script type="module">const hidden = 7;</script>
</body></html>"""


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_evaluate_reads_values_from_the_running_page(session_html, tmp_path):
    from aifred.lib import config
    fname = _write_html(config.SANDBOX_OUTPUT_DIR, _SIMULATION_PAGE)
    result = asyncio.run(render_html_in_browser(
        f"/_upload/sandbox_output/{SESSION}/{fname}", SESSION, wait_ms=200,
        actions=[
            {"evaluate": "rod.length"},                       # top-level const of a classic script
            {"evaluate": "[0, Math.PI / 2].map(crankPin).map(p => Math.round(p.x * 1000) / 1000)"},
            # A Promise is awaited: one expression samples the animation over time.
            {"evaluate": "new Promise(done => { const seen = [frame]; setTimeout(() => done(frame > seen[0]), 100); })"},
            {"evaluate": "hidden"},                           # inside a module script: not reachable
            {"evaluate": "({ünits: 'mm'})"},
        ],
    ))
    assert result.error == "" and not result.timed_out
    assert result.evaluations == ["action 1: 2.5", "action 2: [1, 0]", "action 3: true", 'action 5: {"ünits": "mm"}']
    assert len(result.action_errors) == 1 and "hidden is not defined" in result.action_errors[0]


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_the_model_gets_the_values_under_their_own_heading(session_html):
    from aifred.plugins.tools.sandbox import get_sandbox_tools

    render = next(tool for tool in get_sandbox_tools(session_id=SESSION) if tool.name == "render_html")
    answer = asyncio.run(render.executor(
        html_url=f"/_upload/sandbox_output/{SESSION}/{session_html}", wait_ms=200,
        actions=[{"evaluate": "document.getElementById('state').textContent"}],
    ))
    assert 'EVALUATE RESULTS (1):\naction 1: "initial"' in answer


# ── click with force, and the error text of a failed click ───────

# A big button that never holds still (it shakes by a few pixels every frame): Playwright's "stable" check
# never passes, a click without force times out, a forced one hits it.
_SHAKING_BUTTON_PAGE = """<!DOCTYPE html><html><body style="margin:0">
<button id="go" style="position:absolute;left:300px;top:300px;width:300px;height:200px"
  onclick="document.getElementById('state').textContent='CLICKED'">Go</button>
<div id="state">initial</div>
<script>
let tick = 0;
(function shake() { document.getElementById('go').style.transform = `translateX(${(tick++ % 2) * 4}px)`; requestAnimationFrame(shake); })();
</script></body></html>"""


class _RecordingPage:
    """Stand-in for a Playwright page: remembers how click was called."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.clicks: list[tuple[str, dict]] = []
        self.failure = failure

    async def click(self, selector: str, **options) -> None:
        self.clicks.append((selector, options))
        if self.failure:
            raise self.failure


def _apply(page: _RecordingPage, action: dict) -> RenderResult:
    result = RenderResult()
    asyncio.run(_apply_action(page, action, 0, result, take_screenshot=None))
    return result


def test_force_reaches_the_click_and_is_off_by_default():
    page = _RecordingPage()
    _apply(page, {"click": "#go", "force": True})
    _apply(page, {"click": "#go"})
    assert page.clicks == [("#go", {"force": True}), ("#go", {"force": False})]


def test_a_failed_click_tells_about_force_and_evaluate():
    result = _apply(_RecordingPage(TimeoutError("element is not stable")), {"click": "#go"})
    assert "element is not stable" in result.action_errors[0]
    assert '"force": true' in result.action_errors[0] and "evaluate" in result.action_errors[0]


def test_a_failed_forced_click_does_not_repeat_the_force_advice():
    result = _apply(_RecordingPage(TimeoutError("no such element")), {"click": "#go", "force": True})
    assert "no such element" in result.action_errors[0] and "retry with" not in result.action_errors[0]


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_forced_click_hits_a_button_that_never_holds_still(tmp_path, monkeypatch):
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    fname = _write_html(out_root, _SHAKING_BUTTON_PAGE)
    url = f"/_upload/sandbox_output/{SESSION}/{fname}"
    state = {"evaluate": "document.getElementById('state').textContent"}
    plain = asyncio.run(render_html_in_browser(
        url, SESSION, wait_ms=200, actions=[{"click": "#go"}, state],
    ))
    forced = asyncio.run(render_html_in_browser(
        url, SESSION, wait_ms=200, actions=[{"click": "#go", "force": True}, state],
    ))
    assert len(plain.action_errors) == 1 and "force" in plain.action_errors[0]
    assert plain.evaluations == ['action 2: "initial"']
    assert forced.action_errors == [] and forced.evaluations == ['action 2: "CLICKED"']


# ── a URL that is no HTML page ───────────────────────────────────


@pytest.mark.parametrize("url", ["/_upload/documents/bild.png", "/_upload/images/abc/shot.png?x=1", "documents/skizze.JPG"])
def test_an_image_url_gets_its_own_error_that_points_to_read_file_and_vision_analyze(url):
    result = asyncio.run(render_html_in_browser(url, SESSION))
    assert "HTML pages only" in result.error and "read_file" in result.error and "vision_analyze" in result.error


def test_a_missing_html_page_keeps_the_invalid_url_error():
    result = asyncio.run(render_html_in_browser("/_upload/documents/gibt-es-nicht.html", SESSION))
    assert "SANDBOX_HTML_URL" in result.error


# ── What a rendered page may reach: its own area, nothing else ───

_MARKER = "nur-fuer-aifred-4711"


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_page_cannot_open_a_file_outside_its_area(session_html, tmp_path):
    # Page script could navigate to any file: URL and hand back what it shows (.env, the session secret).
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text(_MARKER, encoding="utf-8")
    result = asyncio.run(render_html_in_browser(
        f"/_upload/sandbox_output/{SESSION}/{session_html}", SESSION, wait_ms=100,
        actions=[{"evaluate": f"location.href = 'file://{secret}'"}, {"wait_ms": 500},
                 {"evaluate": "document.documentElement.innerText"}],
    ))
    assert not any(_MARKER in value for value in result.evaluations), result.evaluations


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_page_cannot_ask_a_local_service(session_html):
    import http.server
    import threading

    class _Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("access-control-allow-origin", "*")
            self.end_headers()
            self.wfile.write(_MARKER.encode())

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = asyncio.run(render_html_in_browser(
            f"/_upload/sandbox_output/{SESSION}/{session_html}", SESSION, wait_ms=100,
            actions=[{"evaluate": f"fetch('http://127.0.0.1:{server.server_port}/').then(r => r.text())"
                                  ".catch(() => 'refused')"}],
        ))
    finally:
        server.shutdown()
    assert result.evaluations == ['action 1: "refused"'], result.evaluations


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_a_page_still_loads_a_file_beside_it(tmp_path, monkeypatch):
    from aifred.lib import config
    out_root = tmp_path / "sandbox_output"
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", out_root)
    page = ('<!DOCTYPE html><html><body><img id="pic" src="beside.svg" '
            'onload="window.loaded = true" onerror="window.loaded = false"></body></html>')
    fname = _write_html(out_root, page)
    (out_root / SESSION / "beside.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>', encoding="utf-8")
    result = asyncio.run(render_html_in_browser(
        f"/_upload/sandbox_output/{SESSION}/{fname}", SESSION, wait_ms=300,
        actions=[{"evaluate": "window.loaded"}],
    ))
    assert result.evaluations == ["action 1: true"], (result.evaluations, result.action_errors)
