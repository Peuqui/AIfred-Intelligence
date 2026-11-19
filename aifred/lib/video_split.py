"""Eine Videodatei ohne Neukodieren in Teile unter einer Größengrenze zerlegen.

Geschnitten wird nur an Schlüsselbildern (``-c copy``), damit jedes Teil für sich abspielbar ist. Die
Schnittpunkte folgen den echten Paketgrößen aus ffprobe, nicht einer mittleren Bitrate; die tatsächliche
Größe jedes Teils wird nach dem Schneiden geprüft. Nur das Videobild wird übernommen (``-map 0:v:0``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# Aufschlag des MP4-Containers je Teil, den die Paketgrößen nicht enthalten: Tabellen je Bild (Größe,
# Zeit, Chunk-Offset, Schlüsselbild-Liste, Zeitversatz) und Kopfdaten. Großzügig geschätzt, die Prüfung
# nach dem Schneiden fängt eine Fehlschätzung als SplitError ab.
CONTAINER_BYTES_PER_PACKET = 32
CONTAINER_BYTES_FIXED = 64 * 1024


class SplitError(Exception):
    """Die Datei lässt sich nicht in Teile unter der Grenze zerlegen (oder ffprobe/ffmpeg scheiterte)."""


@dataclass(frozen=True)
class _Section:
    """Die Pakete von einem Schlüsselbild bis vor das nächste."""

    first_frame: int
    packets: int
    size: int

    @property
    def estimated_part_size(self) -> int:
        return self.size + self.packets * CONTAINER_BYTES_PER_PACKET


def _run(command: list[str]) -> str:
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        raise SplitError(f"{command[0]} ({process.returncode}): {process.stderr[-300:]}")
    return process.stdout


def _sections(source: Path) -> list[_Section]:
    """Abschnitte zwischen Schlüsselbildern, in Dekodierreihenfolge. Das erste Paket muss ein Schlüsselbild sein."""
    output = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=size,flags", "-of", "csv=p=0", str(source),
    ])
    sections: list[_Section] = []
    for index, line in enumerate(output.splitlines()):
        size_text, flags = line.split(",")[:2]
        if "K" in flags:
            sections.append(_Section(first_frame=index, packets=0, size=0))
        elif not sections:
            raise SplitError(f"{source.name}: starts without a key frame")
        last = sections[-1]
        sections[-1] = _Section(last.first_frame, last.packets + 1, last.size + int(size_text))
    if not sections:
        raise SplitError(f"{source.name}: no video packets")
    return sections


def _greedy_groups(sections: list[_Section], max_bytes: int, source: Path) -> list[list[_Section]]:
    budget = max_bytes - CONTAINER_BYTES_FIXED
    groups: list[list[_Section]] = []
    used = 0
    for section in sections:
        if section.estimated_part_size > budget:
            raise SplitError(
                f"{source.name}: the section at frame {section.first_frame} between two key frames "
                f"is {section.estimated_part_size} bytes, over the limit of {max_bytes}"
            )
        if groups and used + section.estimated_part_size <= budget:
            groups[-1].append(section)
            used += section.estimated_part_size
        else:
            groups.append([section])
            used = section.estimated_part_size
    return groups


def split_video(source: Path, max_bytes: int, out_dir: Path) -> list[Path]:
    """Teile von ``source``, jedes höchstens ``max_bytes`` groß, in Reihenfolge in ``out_dir``.

    Liegt die Datei schon unter der Grenze, ist das Ergebnis die Datei selbst (kein Schnitt, nichts in
    ``out_dir``). Die Quelle bleibt unverändert. Wirft :class:`SplitError`, wenn ein Abschnitt zwischen zwei
    Schlüsselbildern allein über der Grenze liegt, ein Teil nach dem Schneiden doch zu groß ist oder
    ffprobe/ffmpeg scheitern.
    """
    if source.stat().st_size <= max_bytes:
        return [source]
    groups = _greedy_groups(_sections(source), max_bytes, source)
    # segment_frames (Bildnummern) statt segment_times: exakt, ohne Rundung der Zeitstempel.
    cut_frames = [group[0].first_frame for group in groups[1:]]
    pattern = out_dir / f"{source.stem}_part%03d{source.suffix}"
    _run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-map", "0:v:0", "-c", "copy",
        "-f", "segment", "-segment_frames", ",".join(str(frame) for frame in cut_frames),
        "-reset_timestamps", "1", "-segment_format_options", "movflags=+faststart", str(pattern),
    ])
    parts = [out_dir / f"{source.stem}_part{index:03d}{source.suffix}" for index in range(len(groups))]
    missing = [part.name for part in parts if not part.exists()]
    if missing:
        raise SplitError(f"{source.name}: ffmpeg did not produce {', '.join(missing)}")
    too_big = [part.name for part in parts if part.stat().st_size > max_bytes]
    if too_big:
        raise SplitError(f"{source.name}: part(s) over the limit of {max_bytes} bytes after cutting: {', '.join(too_big)}")
    return parts
