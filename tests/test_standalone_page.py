"""A sandbox page as one standalone file (lib/standalone_page.py): what it loads from /vendor/ is written into it."""

from __future__ import annotations

import asyncio
import html
import shutil
from pathlib import Path

import pytest

from aifred.lib.standalone_page import standalone_page

_MODULE_PAGE = """<!DOCTYPE html><html><head>
<script type="importmap">{"imports":{"three":"/vendor/three/three.module.min.js","three/addons/":"/vendor/three/addons/","rapier":"/vendor/rapier/rapier.module.js"}}</script>
</head><body><script type="module">
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { FontLoader } from "three/addons/loaders/FontLoader.js";
import { TextGeometry } from "three/addons/geometries/TextGeometry.js";
import RAPIER from "rapier";
await RAPIER.init();
const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
const gltf = await new GLTFLoader().loadAsync("/vendor/models/RobotExpressive/RobotExpressive.glb");
const font = await new FontLoader().loadAsync("/vendor/three/fonts/helvetiker_regular.typeface.json");
const text = new TextGeometry("V8", { font, size: 1, depth: 0.2 }).attributes.position.count > 0;
console.log("standalone: " + THREE.REVISION + " " + typeof OrbitControls + " " + gltf.animations.length + " " + text + " " + typeof world.step);
</script></body></html>"""


def test_a_classic_library_is_written_into_the_page() -> None:
    page = standalone_page('<script src="/vendor/three.min.js"></script><p>x</p>')
    assert "/vendor/" not in page and "REVISION" in page and "<p>x</p>" in page


def test_the_modules_of_a_page_and_their_files_are_written_into_it() -> None:
    page = standalone_page(_MODULE_PAGE)
    assert "/vendor/" not in page                      # no path to a server is left
    assert "vendor-module:three/addons/controls/OrbitControls.js" in page
    assert page.count('type="importmap"') == 0         # the import map is made by the script at its place
    assert "importmap" in page and "createObjectURL" in page


def test_a_name_that_is_no_file_of_the_vendor_folder_stays() -> None:
    page = '<script>fetch("/vendor/nothing.glb"); fetch("/elsewhere/x.glb");</script>'
    assert standalone_page(page) == page


def test_a_model_and_a_font_become_data_urls() -> None:
    page = standalone_page("<script>load('/vendor/models/RobotExpressive/RobotExpressive.glb'); load('/vendor/three/fonts/helvetiker_regular.typeface.json')</script>")
    assert "data:model/gltf-binary;base64," in page and "data:application/json;base64," in page and "/vendor/" not in page


_chrome_missing = shutil.which("google-chrome") is None


@pytest.mark.skipif(_chrome_missing, reason="google-chrome not installed")
def test_the_standalone_page_runs_in_the_sandbox_frame_without_a_server(tmp_path: Path) -> None:
    """The way the chat export holds it: in an iframe with ``sandbox="allow-scripts"`` and ``srcdoc``, opened from a file
    with every request but file, data and blob refused."""
    page = standalone_page(_MODULE_PAGE)
    outer = tmp_path / "export.html"
    outer.write_text(f'<!DOCTYPE html><iframe sandbox="allow-scripts" srcdoc="{html.escape(page)}"></iframe>', encoding="utf-8")
    messages: list[str] = []
    refused: list[str] = []

    async def run() -> None:
        from playwright.async_api import async_playwright

        from aifred.lib.config import BROWSER_RENDER_CHANNEL

        async def only_local(route) -> None:  # type: ignore[no-untyped-def]
            if route.request.url.split(":", 1)[0] in ("file", "data", "blob", "about"):
                await route.continue_()
            else:
                refused.append(route.request.url)
                await route.abort()

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=BROWSER_RENDER_CHANNEL, headless=True)
            try:
                tab = await browser.new_page()
                tab.on("console", lambda message: messages.append(f"{message.type}: {message.text}"))
                tab.on("pageerror", lambda error: messages.append(f"pageerror: {error}"))
                await tab.route("**/*", only_local)
                await tab.goto(outer.as_uri())
                await tab.wait_for_timeout(6000)
            finally:
                await browser.close()

    asyncio.run(run())
    assert refused == []
    assert [m for m in messages if m.startswith("log: standalone")] == ["log: standalone: 160 function 14 true function"], messages
