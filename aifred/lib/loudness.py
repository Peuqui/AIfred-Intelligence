"""Loudness Index — SQLite-cached EBU R128 measurements per audio file.

Pro File einmal die ``integrated loudness`` (LUFS), den ``true peak``
(dBFS) und die Dauer messen, in SQLite cachen, bei Wiedergabe nur den
Gain-Offset nachschlagen. So können alle Audio-Channels ihre Tracks auf
ein einheitliches Lautheits-Ziel (default: -16 LUFS) bringen — Old
Masters wie *Jailhouse Rock* werden runter-, Quiet-Mastering wird
hoch-gerampt — ohne dass jeder Play eine neue Messung kostet.

Single source of truth: die SQLite-DB. Channels rufen
``loudness_index.get_gain_db(file_path)`` und wenden das Ergebnis selbst
auf ihre Pipeline an (mpv ``-af volume=XdB``, ffmpeg-Pipe etc.).

Messung:
    ``ffmpeg -i FILE -af loudnorm=print_format=json -f null -``
gibt am Ende einen JSON-Block auf stderr mit ``input_i`` (LUFS) und
``input_tp`` (dBFS True Peak). Dauer kommt aus der ``Duration:``-Zeile,
die ffmpeg standardmäßig beim Öffnen ausgibt. Eine Messung dauert
typisch ~10-30 s pro 4-min-Track (I/O + Decode-bound, CPU ~10%).

Lazy + Batch:
    ``request_lazy_analysis(path)`` startet eine Background-Messung
    (Concurrency-Limit via Semaphore) — Caller bekommt sofort None
    zurück und kann ohne Normalisierung starten; beim nächsten Play
    ist der Wert da. Für initialen Library-Scan gibt es das CLI-Tool
    ``scripts/scan_loudness.py`` (sync, sequenziell oder parallel).
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .config import (
    DATA_DIR,
    LOUDNESS_CEILING_DBFS,
    LOUDNESS_FADE_IN_SEC,
    LOUDNESS_FADE_OUT_SEC,
    LOUDNESS_TARGET_LUFS,
)
from .logging_utils import log_message

LOUDNESS_DB = DATA_DIR / "loudness.sqlite"
FFMPEG_BINARY = "/usr/bin/ffmpeg"

# Maximale parallele Messungen (CPU + I/O konkurrieren). 2 ist sicher
# auf jedem System; höher macht keinen Spaß auf NFS-Mounts.
ANALYSIS_CONCURRENCY = 2

# Timeout pro Datei. Hörbücher können 10+ Stunden lang sein → groß
# wählen. Messung läuft I/O-bound, kein Tight-Loop — kein Risiko.
ANALYSIS_TIMEOUT_SEC = 1800  # 30 min

# Regex für ``Duration: HH:MM:SS.ms`` aus ffmpeg-stderr
_DURATION_RE = re.compile(
    r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", re.IGNORECASE,
)


@dataclass
class LoudnessInfo:
    """Cached measurement for one file."""

    path: str                          # absolute, resolved
    mtime: int                         # filesystem mtime at analysis time
    integrated_lufs: Optional[float]   # None if measurement failed
    true_peak_dbfs: Optional[float]    # None if measurement failed
    duration_sec: Optional[float]      # None if unknown
    analyzed_at: int                   # epoch seconds


@dataclass
class ScanResult:
    scanned: int
    analyzed: int       # newly analyzed (was unknown or stale)
    cached: int         # already up-to-date, no work
    failed: int
    elapsed_sec: float


def _calc_gain_db(
    integrated_lufs: float,
    true_peak_dbfs: Optional[float],
    target_lufs: float,
    ceiling_dbfs: float,
) -> float:
    """Berechne den anzuwendenden Gain in dB.

    Naiv wäre ``target - measured``. Bei sehr leisen Originalen würde
    das aber den True-Peak über das Ceiling drücken (= Clipping).
    Deshalb clamp: nie mehr Gain als das Ceiling - True-Peak erlaubt.
    Konsequenz: sehr leise Tracks bleiben evtl. unter dem Ziel-LUFS,
    sind aber zumindest nicht verzerrt.
    """
    raw_gain = target_lufs - integrated_lufs
    if true_peak_dbfs is None:
        return raw_gain
    headroom = ceiling_dbfs - true_peak_dbfs
    return min(raw_gain, headroom)


def _parse_duration(stderr: str) -> Optional[float]:
    m = _DURATION_RE.search(stderr)
    if not m:
        return None
    h, mi, s, ms = m.groups()
    try:
        secs: float = (
            int(h) * 3600 + int(mi) * 60 + int(s)
            + int(ms) / (10 ** len(ms))
        )
    except ValueError:
        return None
    return secs


def _parse_loudnorm_json(stderr: str) -> Optional[dict[str, Any]]:
    """Find the JSON block at the end of loudnorm's stderr output.

    loudnorm prints a multi-line JSON object after the "[Parsed_loudnorm
    ..." line. We grab the last balanced ``{...}`` block from the tail.
    """
    # Suche das letzte '{' — der JSON-Block kommt am Schluss
    last_open = stderr.rfind("{")
    if last_open < 0:
        return None
    last_close = stderr.rfind("}")
    if last_close < last_open:
        return None
    blob = stderr[last_open:last_close + 1]
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _measure_file_sync(file_path: Path) -> Optional[LoudnessInfo]:
    """Run ffmpeg loudnorm measurement on file. Sync, blocking.

    Returns ``LoudnessInfo`` with the measurement, or ``None`` if ffmpeg
    couldn't open the file. Partial results (lufs but no true_peak,
    duration but no lufs) are returned as ``LoudnessInfo`` with the
    missing field as ``None`` — caller decides how to handle that.
    """
    try:
        stat = file_path.stat()
    except OSError as exc:
        log_message(
            f"loudness: stat failed for {file_path.name}: {exc}", "warning",
        )
        return None
    mtime = int(stat.st_mtime)

    cmd = [
        FFMPEG_BINARY,
        "-hide_banner",
        "-nostats",
        "-i", str(file_path),
        "-af", "loudnorm=print_format=json",
        "-f", "null",
        "-",
    ]
    try:
        # ffmpeg gibt teils Latin-1-ID3-Tags 1:1 auf stderr aus —
        # ``text=True`` ohne ``errors="replace"`` würde an
        # nicht-UTF-8-Bytes crashen. Ungültige Bytes durch ? ersetzen
        # ist OK, weil unsere Parser nur ASCII brauchen (Duration-Regex
        # und JSON-Block).
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ANALYSIS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        log_message(
            f"loudness: timeout analyzing {file_path.name}", "warning",
        )
        return None
    except OSError as exc:
        log_message(
            f"loudness: ffmpeg spawn failed for {file_path.name}: {exc}",
            "warning",
        )
        return None

    if proc.returncode != 0:
        log_message(
            f"loudness: ffmpeg rc={proc.returncode} for {file_path.name}",
            "warning",
        )
        return None

    stderr = proc.stderr or ""
    duration = _parse_duration(stderr)
    data = _parse_loudnorm_json(stderr)

    integrated: Optional[float] = None
    true_peak: Optional[float] = None
    if data is not None:
        try:
            i_str = data.get("input_i")
            tp_str = data.get("input_tp")
            if i_str is not None and i_str != "-inf":
                integrated = float(i_str)
            if tp_str is not None and tp_str != "-inf":
                true_peak = float(tp_str)
        except (TypeError, ValueError):
            pass

    return LoudnessInfo(
        path=str(file_path),
        mtime=mtime,
        integrated_lufs=integrated,
        true_peak_dbfs=true_peak,
        duration_sec=duration,
        analyzed_at=int(time.time()),
    )


class LoudnessIndex:
    """SQLite-backed loudness measurement cache with lazy analysis."""

    def __init__(self, path: Path = LOUDNESS_DB) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Lazy-Analyse-Dedup: Set der gerade laufenden/geplanten Pfade
        self._inflight: set[str] = set()
        self._inflight_lock = threading.Lock()
        # Concurrency-Limit für Background-Messungen (asyncio-only).
        # Wird beim ersten Aufruf erzeugt — Loop muss laufen.
        self._sem: Optional[asyncio.Semaphore] = None
        self._init_schema()

    # ── Schema ──────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS loudness (
                    path             TEXT PRIMARY KEY,
                    mtime            INTEGER NOT NULL,
                    integrated_lufs  REAL,
                    true_peak_dbfs   REAL,
                    duration_sec     REAL,
                    analyzed_at      INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_loudness_analyzed
                    ON loudness(analyzed_at);
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── Lookup ──────────────────────────────────────────────

    def get_info(self, file_path: Path | str) -> Optional[LoudnessInfo]:
        """Return cached info if present and mtime matches; else None.

        Stale entries (mtime mismatch) are NOT returned — caller should
        treat as "needs re-analysis". Same for failed measurements
        (integrated_lufs is NULL): we keep the row to avoid re-trying
        every play, but ``get_info`` returns the row so caller can see
        the failure and skip normalization.
        """
        try:
            resolved = Path(file_path).expanduser().resolve()
            stat = resolved.stat()
        except OSError:
            return None
        path_str = str(resolved)
        mtime = int(stat.st_mtime)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM loudness WHERE path = ?", (path_str,),
            ).fetchone()
        if row is None:
            return None
        if row["mtime"] != mtime:
            return None
        return LoudnessInfo(
            path=row["path"],
            mtime=row["mtime"],
            integrated_lufs=row["integrated_lufs"],
            true_peak_dbfs=row["true_peak_dbfs"],
            duration_sec=row["duration_sec"],
            analyzed_at=row["analyzed_at"],
        )

    def get_gain_db(
        self,
        file_path: Path | str,
        target_lufs: float = LOUDNESS_TARGET_LUFS,
        ceiling_dbfs: float = LOUDNESS_CEILING_DBFS,
    ) -> Optional[float]:
        """Return the gain in dB to apply, or ``None`` if unknown.

        ``None`` means: no measurement available (either never analyzed
        or analysis previously failed). Caller should play unmodified
        and may schedule ``request_lazy_analysis`` for next time.
        """
        info = self.get_info(file_path)
        if info is None or info.integrated_lufs is None:
            return None
        return _calc_gain_db(
            info.integrated_lufs,
            info.true_peak_dbfs,
            target_lufs,
            ceiling_dbfs,
        )

    def get_duration(self, file_path: Path | str) -> Optional[float]:
        """Cached duration in seconds, or None."""
        info = self.get_info(file_path)
        return info.duration_sec if info is not None else None

    # ── Analyse ─────────────────────────────────────────────

    def analyze_file(self, file_path: Path | str) -> Optional[LoudnessInfo]:
        """Synchron messen + cachen. Returns LoudnessInfo (auch bei
        partiellem Erfolg) oder None wenn ffmpeg gar nicht starten konnte
        bzw. das File nicht stat-bar war.

        Idempotent: re-running auf einem unveränderten File überschreibt
        den Cache mit denselben Werten (mtime gleich → analyzed_at neu).
        """
        try:
            resolved = Path(file_path).expanduser().resolve()
        except OSError:
            return None
        info = _measure_file_sync(resolved)
        if info is None:
            return None
        with self._lock, self._connect() as conn:
            conn.execute("""
                INSERT INTO loudness (path, mtime, integrated_lufs,
                    true_peak_dbfs, duration_sec, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime           = excluded.mtime,
                    integrated_lufs = excluded.integrated_lufs,
                    true_peak_dbfs  = excluded.true_peak_dbfs,
                    duration_sec    = excluded.duration_sec,
                    analyzed_at     = excluded.analyzed_at
            """, (info.path, info.mtime, info.integrated_lufs,
                  info.true_peak_dbfs, info.duration_sec, info.analyzed_at))
            conn.commit()
        return info

    def request_lazy_analysis(self, file_path: Path | str) -> bool:
        """Schedule background measurement. Returns True wenn neu
        eingereiht, False wenn schon in-flight oder im Cache aktuell.

        Erfordert einen laufenden asyncio-Loop. Wird das aus einem
        Sync-Kontext aufgerufen, gibt's eine RuntimeError-Warnung und
        False zurück (kein Crash).
        """
        try:
            resolved = Path(file_path).expanduser().resolve()
        except OSError:
            return False
        path_str = str(resolved)

        # Bereits aktuell im Cache → kein Bedarf
        if self.get_info(resolved) is not None:
            return False

        with self._inflight_lock:
            if path_str in self._inflight:
                return False
            self._inflight.add(path_str)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Kein Loop → wir können nicht im Hintergrund messen.
            # Inflight-Marker wieder freigeben.
            with self._inflight_lock:
                self._inflight.discard(path_str)
            log_message(
                "loudness.request_lazy_analysis: no running loop, skipping",
                "warning",
            )
            return False

        if self._sem is None:
            self._sem = asyncio.Semaphore(ANALYSIS_CONCURRENCY)

        loop.create_task(
            self._run_lazy_analysis(resolved),
            name=f"loudness-analyze-{resolved.name}",
        )
        return True

    async def _run_lazy_analysis(self, file_path: Path) -> None:
        path_str = str(file_path)
        sem = self._sem
        assert sem is not None  # set by request_lazy_analysis before scheduling
        try:
            async with sem:
                loop = asyncio.get_event_loop()
                start = time.monotonic()
                info = await loop.run_in_executor(
                    None, self.analyze_file, file_path,
                )
                elapsed = time.monotonic() - start
                if info and info.integrated_lufs is not None:
                    log_message(
                        f"loudness: {file_path.name} → "
                        f"{info.integrated_lufs:.1f} LUFS, "
                        f"TP {info.true_peak_dbfs} dBFS "
                        f"({elapsed:.1f}s)",
                    )
                else:
                    log_message(
                        f"loudness: {file_path.name} measurement failed "
                        f"({elapsed:.1f}s)",
                        "warning",
                    )
        finally:
            with self._inflight_lock:
                self._inflight.discard(path_str)

    # ── Batch-Scan ──────────────────────────────────────────

    def scan_directory(
        self,
        root_path: Path | str,
        on_progress: Optional[Callable[[int, int, int], None]] = None,
        force: bool = False,
        workers: int = 1,
    ) -> ScanResult:
        """Alle Audio-Files unter ``root_path`` messen, optional parallel.

        Symlinks werden gefolgt. Files mit aktuellem Cache werden
        übersprungen (außer ``force=True``). Mit ``workers > 1`` laufen
        mehrere ffmpeg-Prozesse parallel — ffmpeg gibt während
        ``subprocess.run`` den GIL frei, daher reicht ThreadPoolExecutor.
        SQLite-Writes werden durch ``self._lock`` serialisiert; bei
        I/O-bound NAS-Scans hilft Parallelität trotzdem deutlich.
        """
        from concurrent.futures import ThreadPoolExecutor
        from .audio_sources import ALLOWED_EXTENSIONS

        root = Path(root_path).expanduser().resolve()
        start = time.monotonic()

        if not root.is_dir():
            return ScanResult(0, 0, 0, 0, 0.0)

        # Files vorab sammeln, damit Worker eine geschlossene Liste
        # abarbeiten und Progress sinnvoll zählbar bleibt.
        files = [
            child for child in root.rglob("*")
            if child.is_file()
            and child.suffix.lower() in ALLOWED_EXTENSIONS
        ]

        # Counters thread-safe halten — ohne Lock wären Updates aus
        # mehreren Workern nicht atomar.
        counts = {"scanned": 0, "analyzed": 0, "cached": 0, "failed": 0}
        counts_lock = threading.Lock()

        def _process(child: Path) -> None:
            if not force and self.get_info(child) is not None:
                with counts_lock:
                    counts["scanned"] += 1
                    counts["cached"] += 1
                    sc, an, fa = counts["scanned"], counts["analyzed"], counts["failed"]
                if on_progress:
                    on_progress(sc, an, fa)
                return
            # Worker-Isolation: ein einzelnes broken File darf nicht den
            # ganzen ThreadPool umreissen. Bei einem Library-Scan ueber
            # tausende Files ist mit defekten Tags / NAS-Glitches /
            # Sub-Encoder-Bugs zu rechnen — als ``failed`` markieren und
            # weiterlaufen.
            try:
                info = self.analyze_file(child)
            except Exception as exc:  # noqa: BLE001
                log_message(
                    f"loudness.scan_directory: {child.name} threw "
                    f"{type(exc).__name__}: {exc} — marked failed",
                    "warning",
                )
                info = None
            with counts_lock:
                counts["scanned"] += 1
                if info is None or info.integrated_lufs is None:
                    counts["failed"] += 1
                else:
                    counts["analyzed"] += 1
                sc, an, fa = counts["scanned"], counts["analyzed"], counts["failed"]
            if on_progress:
                on_progress(sc, an, fa)

        if workers <= 1:
            for child in files:
                _process(child)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # list() um Exceptions zu propagieren
                list(pool.map(_process, files))

        elapsed = time.monotonic() - start
        log_message(
            f"loudness.scan_directory[{root}]: "
            f"scanned={counts['scanned']} analyzed={counts['analyzed']} "
            f"cached={counts['cached']} failed={counts['failed']} "
            f"workers={workers} in {elapsed:.1f}s"
        )
        return ScanResult(
            counts["scanned"], counts["analyzed"],
            counts["cached"], counts["failed"], elapsed,
        )

    # ── Maintenance ─────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM loudness"
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM loudness WHERE integrated_lufs IS NULL"
            ).fetchone()[0]
        return {"total": int(total), "failed": int(failed)}

    def clear_all(self) -> int:
        with self._lock, self._connect() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM loudness").fetchone()[0]
            conn.execute("DELETE FROM loudness")
            conn.commit()
            try:
                conn.execute("VACUUM")
            except sqlite3.OperationalError:
                pass
        return int(cnt)


loudness_index = LoudnessIndex()


# ── Hilfsfunktionen für ffmpeg-Filter-Strings ─────────────────


def build_music_filter_chain(
    file_path: Path | str,
    *,
    target_lufs: float = LOUDNESS_TARGET_LUFS,
    ceiling_dbfs: float = LOUDNESS_CEILING_DBFS,
    fade_in_sec: float = LOUDNESS_FADE_IN_SEC,
    fade_out_sec: float = LOUDNESS_FADE_OUT_SEC,
) -> list[str]:
    """Return ffmpeg/mpv ``-af`` filters for music playback.

    Channels nutzen das Ergebnis als Komma-separierte Filter-Chain. Wenn
    keine Loudness-Daten da sind, wird kein ``volume`` aufgenommen — wir
    schedulen aber eine Lazy-Analyse, damit's beim nächsten Play da ist.
    Fade-In wird immer dazu genommen (auch ohne Loudness-Wert), Fade-
    Out nur wenn die Dauer bekannt ist.

    Note: Caller sollte das nur für ``audio_type == "music"`` und nicht
    für HTTP-Streams aufrufen.
    """
    filters: list[str] = []
    info = loudness_index.get_info(file_path)

    if info is None:
        # Erstes Erlebnis dieses Files — Background-Analyse anwerfen,
        # damit der nächste Play normalisiert ist.
        loudness_index.request_lazy_analysis(file_path)
    elif info.integrated_lufs is not None:
        gain = _calc_gain_db(
            info.integrated_lufs,
            info.true_peak_dbfs,
            target_lufs,
            ceiling_dbfs,
        )
        # Mikro-Gains < 0.1 dB sind hörbar irrelevant — Filter sparen
        if abs(gain) >= 0.1:
            filters.append(f"volume={gain:.2f}dB")

    if fade_in_sec > 0:
        filters.append(f"afade=t=in:d={fade_in_sec}")

    if fade_out_sec > 0 and info is not None and info.duration_sec is not None:
        # Fade-Out so positionieren, dass er genau am Track-Ende endet.
        # Bei sehr kurzen Tracks (< 2x fade_out) Fade-Out weglassen,
        # sonst überlappt er den Fade-In.
        if info.duration_sec > 2 * fade_out_sec:
            fade_start = info.duration_sec - fade_out_sec
            filters.append(f"afade=t=out:st={fade_start:.2f}:d={fade_out_sec}")

    name = Path(file_path).name
    if info is None:
        log_message(
            f"🎚️ loudness[{name}]: no cached data, lazy-analysis scheduled "
            f"— filters: {filters or '(none)'}"
        )
    elif info.integrated_lufs is None:
        log_message(
            f"🎚️ loudness[{name}]: measurement failed previously "
            f"— filters: {filters or '(none)'}"
        )
    else:
        log_message(
            f"🎚️ loudness[{name}]: "
            f"{info.integrated_lufs:.1f} LUFS → target {target_lufs} "
            f"(TP {info.true_peak_dbfs} dBFS, dur {info.duration_sec:.1f}s) "
            f"→ filters: {filters}"
        )

    return filters
