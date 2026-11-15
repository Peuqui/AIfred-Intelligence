"""A sandbox page as one standalone file: what it loads from /vendor/ written into it, as a file has no server behind it.

Three kinds of loading are covered:

* a classic library (``<script src="/vendor/three.min.js">``) is written into the page as script text;
* ES modules (an import map, ``<script type="module">`` importing from it): every module of the page's import graph
  (three.js, its add-ons, Rapier) is written in as Base64, and a small script at the place of the import map makes
  object URLs of them and sets the import map. The specifiers inside the modules are rewritten to one name per file
  (``vendor-module:three/addons/controls/OrbitControls.js``), because a relative import does not resolve from an
  object URL;
* every other file the page names under /vendor/ (a model, a font: ``"/vendor/models/RobotExpressive/….glb"``) is
  written in as a ``data:`` URL in place of the path.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import posixpath
import re

from .config import VENDOR_URL
from .vendor_files import vendor_file

# What names another module: import … from "x", import "x", export … from "x", and import("x").
_STATIC = re.compile(r"""(\b(?:import|export)\s*(?:[\w$*\s{},]*?\s*from\s*)?)(["'])([^"']+)\2""")
_DYNAMIC = re.compile(r"""(\bimport\(\s*)(["'])([^"']+)\2""")
_IMPORT_MAP = re.compile(r'<script\s+type="importmap"\s*>(.*?)</script>', re.DOTALL)
_MODULE_SCRIPT = re.compile(r'(<script\s+type="module"\s*>)(.*?)(</script>)', re.DOTALL)
_CLASSIC_LIBRARY = re.compile(rf'<script\s+src="{re.escape(VENDOR_URL)}/([^"]+)"\s*>\s*</script>')
_VENDOR_FILE_NAME = re.compile(rf"""(["'`])({re.escape(VENDOR_URL)}/[^"'`<>\s)]+)\1""")
_MIME = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".json": "application/json", ".wasm": "application/wasm"}
_MODULE_NAME = "vendor-module:"


def standalone_page(page: str) -> str:
    """``page`` with everything it loads from /vendor/ written into it."""
    page = _with_modules(page)
    page = _CLASSIC_LIBRARY.sub(_inline_classic_library, page)
    return _VENDOR_FILE_NAME.sub(_as_data_url, page)


def _specifiers(code: str) -> list[str]:
    return [match.group(3) for pattern in (_STATIC, _DYNAMIC) for match in pattern.finditer(code)]


def _import_map(page: str) -> dict[str, str]:
    """The ``imports`` of the page's import map (empty without one)."""
    match = _IMPORT_MAP.search(page)
    imports: dict[str, str] = json.loads(match.group(1)).get("imports", {}) if match else {}
    return imports


def _resolve(specifier: str, importer: str | None, import_map: dict[str, str]) -> str | None:
    """The /vendor/ path of the file a specifier names, None when it names none of ours."""
    url: str | None
    if specifier.startswith(("./", "../")):
        url = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier)) if importer else None
    elif specifier.startswith("/"):
        url = specifier
    elif specifier in import_map:
        url = import_map[specifier]
    else:
        url = next((value + specifier[len(key):] for key, value in import_map.items()
                    if key.endswith("/") and specifier.startswith(key)), None)
    return url if url and url.startswith(f"{VENDOR_URL}/") and vendor_file(url) else None


def _module_name(url: str) -> str:
    return _MODULE_NAME + url.removeprefix(f"{VENDOR_URL}/")


def _collect(entries: list[str], import_map: dict[str, str]) -> dict[str, str]:
    """The modules of the import graph below ``entries`` (specifiers of the page): /vendor/ path → code."""
    modules: dict[str, str] = {}
    queue = [_resolve(entry, None, import_map) for entry in entries]
    while queue:
        url = queue.pop()
        file = vendor_file(url) if url else None
        if url is None or file is None or url in modules:
            continue
        modules[url] = file.read_text(encoding="utf-8")
        queue.extend(_resolve(specifier, url, import_map) for specifier in _specifiers(modules[url]))
    return modules


def _renamed(code: str, importer: str | None, import_map: dict[str, str]) -> str:
    """``code`` with every specifier that names a module of ours replaced by that module's name."""
    def replace(match: re.Match[str]) -> str:
        url = _resolve(match.group(3), importer, import_map)
        return match.group(0) if url is None else f"{match.group(1)}{match.group(2)}{_module_name(url)}{match.group(2)}"

    return _DYNAMIC.sub(replace, _STATIC.sub(replace, code))


def _with_modules(page: str) -> str:
    import_map = _import_map(page)
    scripts = [match.group(2) for match in _MODULE_SCRIPT.finditer(page)]
    modules = _collect([specifier for script in scripts for specifier in _specifiers(script)], import_map)
    if not modules:
        return page
    files = {
        _module_name(url): base64.b64encode(_renamed(code, url, import_map).encode("utf-8")).decode("ascii")
        for url, code in modules.items()
    }
    bootstrap = (
        "<script>(function(){const files=" + json.dumps(files) + ";const imports={};"
        "for(const name in files){const bytes=Uint8Array.from(atob(files[name]),c=>c.charCodeAt(0));"
        "imports[name]=URL.createObjectURL(new Blob([bytes],{type:'text/javascript'}));}"
        "const map=document.createElement('script');map.type='importmap';"
        "map.textContent=JSON.stringify({imports});document.head.appendChild(map);})();</script>"
    )
    page = _MODULE_SCRIPT.sub(lambda m: m.group(1) + _renamed(m.group(2), None, import_map) + m.group(3), page)
    if _IMPORT_MAP.search(page):
        return _IMPORT_MAP.sub(lambda _: bootstrap, page, count=1)
    return _MODULE_SCRIPT.sub(lambda m: bootstrap + m.group(0), page, count=1)


def _inline_classic_library(match: re.Match[str]) -> str:
    """A ``</script`` inside a library would end its script element early, so it is written as ``<\\/script``."""
    library = vendor_file(f"{VENDOR_URL}/{match.group(1)}")
    if library is None:
        return match.group(0)
    return f"<script>{library.read_text(encoding='utf-8').replace('</script', '<\\/script')}</script>"


def _as_data_url(match: re.Match[str]) -> str:
    """The name of a file of /vendor/ (a model, a font) as a ``data:`` URL; a name that is no file stays."""
    file = vendor_file(match.group(2))
    if file is None:
        return match.group(0)
    mime = _MIME.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    quote = match.group(1)
    return f"{quote}data:{mime};base64,{base64.b64encode(file.read_bytes()).decode('ascii')}{quote}"
