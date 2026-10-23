"""Embedded Firebird 2.5 for EPIM databases — the one place that loads the
library and opens connections (used by the database layer and maintenance).

EssentialPIM stores its data in a Firebird 2.5 file (ODS 11.2). The embedded
engine ships in ``lib/firebird25`` of the repository.
"""

import ctypes
import os
import threading
from pathlib import Path

import fdb
import fdb.services

from ....lib.config import PROJECT_ROOT

FB_DIR = PROJECT_ROOT / "lib" / "firebird25"
FB_LIB = FB_DIR / "libfbembed.so"
# LD_LIBRARY_PATH set at runtime does not reach dlopen() of transitive
# dependencies, so libfbembed's ICU libraries are loaded explicitly first.
_ICU_LIBS = ("libicudata.so.57", "libicuuc.so.57", "libicui18n.so.57")
# The embedded engine skips authentication, but EPIM's triggers and views
# resolve the editing user through USER against its USERS table, whose only
# entry is SYSDBA. The password is not checked by the embedded engine.
EPIM_USER = "SYSDBA"
_EMBEDDED_PASSWORD = "masterkey"

_load_lock = threading.Lock()
_loaded = False


def load_library() -> None:
    """Point Firebird at the bundled engine and load it (once per process)."""
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
    """Connect without EPIM's database triggers: DB_CONNECT0/DB_DISCONNECT0
    register every session in its CONNECTIONS table, which would write to the
    file on each connect (and make a read-only file unopenable). Table
    triggers still run."""
    load_library()
    return fdb.connect(
        dsn=str(db_path),
        user=EPIM_USER,
        password=_EMBEDDED_PASSWORD,
        charset="UTF8",
        fb_library_name=str(FB_LIB),
        no_db_triggers=1,
    )


def services() -> fdb.services.Connection:
    """Local Services Manager of the embedded engine (backup, restore,
    validation, access mode)."""
    load_library()
    return fdb.services.connect(host="", user=EPIM_USER, password=_EMBEDDED_PASSWORD)
