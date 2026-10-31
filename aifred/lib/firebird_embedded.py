"""Embedded Firebird 2.5 for tools outside the EPIM plugin.

Plugins must not depend on each other and a tool must keep working while the
EPIM plugin is switched off, so it reaches EPIM files through this module.
"""

import ctypes
import os
import threading
from pathlib import Path

import fdb
import fdb.services

from .config import PROJECT_ROOT

FB_DIR = PROJECT_ROOT / "lib" / "firebird25"
FB_LIB = FB_DIR / "libfbembed.so"
# LD_LIBRARY_PATH set at runtime does not reach dlopen() of transitive
# dependencies, so libfbembed's ICU libraries are loaded explicitly first.
_ICU_LIBS = ("libicudata.so.57", "libicuuc.so.57", "libicui18n.so.57")
# The embedded engine skips authentication; EPIM's only user is SYSDBA.
_USER = "SYSDBA"
_EMBEDDED_PASSWORD = "masterkey"

_load_lock = threading.Lock()
_loaded = False


def _load_library() -> None:
    global _loaded
    with _load_lock:
        if _loaded:
            return
        if not FB_LIB.exists():
            raise FileNotFoundError(f"Firebird embedded library not found: {FB_LIB}")
        os.environ["FIREBIRD"] = str(FB_DIR)
        for lib_name in _ICU_LIBS:
            ctypes.cdll.LoadLibrary(str(FB_DIR / lib_name))
        fdb.load_api(str(FB_LIB))
        _loaded = True


def connect(db_path: Path) -> fdb.Connection:
    """Connect without EPIM's database triggers: they would write a session row
    on every connect."""
    _load_library()
    return fdb.connect(dsn=str(db_path), user=_USER, password=_EMBEDDED_PASSWORD, charset="UTF8",
                       fb_library_name=str(FB_LIB), no_db_triggers=1)


def restore(backup: Path, dest: Path) -> None:
    """Restore an EPIM backup file into the new file ``dest``."""
    _load_library()
    service = fdb.services.connect(host="", user=_USER, password=_EMBEDDED_PASSWORD)
    try:
        service.restore(str(backup), str(dest), no_db_triggers=1)
        errors = [line for line in service.readlines() if "ERROR" in line.upper()]
    finally:
        service.close()
    if errors:
        raise RuntimeError(f"restore of {backup} failed: " + "; ".join(errors[:3]))
