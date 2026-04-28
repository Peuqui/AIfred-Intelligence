"""
Agent Bundle Export/Import.

A self-contained ZIP archive that can hold one or many agents, exchangeable
between AIfred installations. Aggregates the three storage locations
(agents.json, prompts/{de,en}/<id>/, settings.json sampling+TTS keys) into
one archive so users can hand over agents without manual file-copying.

Bundle layout (ZIP):
  manifest.json                 format_version, agent_ids[], exported_at
  agents/<id>/config.json       entry from agents.json
  agents/<id>/sampling.json     all <id>_* keys from settings.json
  agents/<id>/tts.json          tts_agent_voices_per_engine[*][<id>]
  agents/<id>/prompts/de/*.txt
  agents/<id>/prompts/en/*.txt
  voices/xtts/<name>.wav        optional, deduplicated across agents
  voices/moss-tts/<name>.wav

Voice samples live at the top level (not per-agent) so a WAV that's
referenced by multiple agents is included only once. Plugins/tools listed
in config.tools that the target installation doesn't have are silently
ignored at runtime.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Iterable, Literal

from .agent_config import load_agents_raw, save_agents_raw
from .config import DATA_DIR, PROJECT_ROOT

BUNDLE_FORMAT_VERSION = 1

PROMPTS_DIR = PROJECT_ROOT / "prompts"
XTTS_VOICES_DIR = PROJECT_ROOT / "docker" / "xtts" / "voices"
MOSS_VOICES_DIR = PROJECT_ROOT / "docker" / "moss-tts" / "voices"
SETTINGS_FILE = DATA_DIR / "settings.json"

ConflictStrategy = Literal["abort", "overwrite", "rename"]

_AGENT_ID_RE = re.compile(r"^[a-z0-9_]+$")


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_settings(settings: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def _voice_file_name(voice: str) -> str:
    """Strip the UI '★ ' prefix to get the on-disk WAV stem."""
    return voice.lstrip("★").strip() if voice else ""


def _is_safe_member(name: str) -> bool:
    """Reject ZIP members that try to escape via '..' or absolute paths."""
    if name.startswith("/") or name.startswith("\\"):
        return False
    parts = Path(name).parts
    return ".." not in parts


def _engine_voice_dir(engine_or_dir: str) -> Path | None:
    """Map engine identifier or bundle dir name to the on-disk voices dir."""
    if engine_or_dir == "xtts":
        return XTTS_VOICES_DIR
    if engine_or_dir in ("moss", "moss-tts"):
        return MOSS_VOICES_DIR
    return None


def _engine_to_bundle_dir(engine: str) -> str | None:
    """Map settings.json engine name to the bundle directory name."""
    if engine == "xtts":
        return "xtts"
    if engine == "moss":
        return "moss-tts"
    return None


def _bundle_dir_to_engine(dir_name: str) -> str | None:
    """Inverse of _engine_to_bundle_dir — settings key by engine."""
    if dir_name == "xtts":
        return "xtts"
    if dir_name == "moss-tts":
        return "moss"
    return None


# ─────────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────────


def export_bundle(agent_ids: Iterable[str]) -> bytes:
    """Pack the given agents into a single ZIP and return raw bytes.

    Voice samples referenced by multiple agents are deduplicated.
    Raises KeyError if any requested agent doesn't exist.
    """
    agents = load_agents_raw()
    settings = _load_settings()

    requested = list(agent_ids)
    if not requested:
        raise ValueError("No agents selected for export")
    missing = [a for a in requested if a not in agents]
    if missing:
        raise KeyError(f"Agents not found: {', '.join(missing)}")

    manifest = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "agent_ids": requested,
        "exported_at": int(time.time()),
    }

    buf = io.BytesIO()
    added_voices: set[tuple[str, str]] = set()  # (bundle_dir, stem) — dedup

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

        for agent_id in requested:
            base = f"agents/{agent_id}"
            zf.writestr(
                f"{base}/config.json",
                json.dumps(agents[agent_id], indent=2, ensure_ascii=False),
            )

            sampling = {k: v for k, v in settings.items() if k.startswith(f"{agent_id}_")}
            zf.writestr(
                f"{base}/sampling.json",
                json.dumps(sampling, indent=2, ensure_ascii=False),
            )

            tts_per_engine: dict[str, dict] = {}
            for engine, agent_map in (settings.get("tts_agent_voices_per_engine") or {}).items():
                if agent_id in agent_map:
                    tts_per_engine[engine] = agent_map[agent_id]
            zf.writestr(
                f"{base}/tts.json",
                json.dumps(tts_per_engine, indent=2, ensure_ascii=False),
            )

            for lang in ("de", "en"):
                prompt_dir = PROMPTS_DIR / lang / agent_id
                if not prompt_dir.is_dir():
                    continue
                for prompt_file in sorted(prompt_dir.glob("*.txt")):
                    zf.write(prompt_file, f"{base}/prompts/{lang}/{prompt_file.name}")

            for engine, entry in tts_per_engine.items():
                voice_stem = _voice_file_name(entry.get("voice", ""))
                if not voice_stem:
                    continue
                bundle_dir = _engine_to_bundle_dir(engine)
                if bundle_dir is None:
                    continue
                if (bundle_dir, voice_stem) in added_voices:
                    continue
                src = (_engine_voice_dir(engine) or Path("/dev/null")) / f"{voice_stem}.wav"
                if src.is_file():
                    zf.write(src, f"voices/{bundle_dir}/{voice_stem}.wav")
                    added_voices.add((bundle_dir, voice_stem))

    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────


def _resolve_id_collision(desired_id: str, existing_ids: set[str]) -> str:
    """Append _imported, _imported_2, … until unique."""
    if desired_id not in existing_ids:
        return desired_id
    base = f"{desired_id}_imported"
    if base not in existing_ids:
        return base
    n = 2
    while f"{base}_{n}" in existing_ids:
        n += 1
    return f"{base}_{n}"


def peek_bundle(zip_bytes: bytes) -> dict:
    """Read manifest + per-agent display names without writing.

    Used by the UI to show a checkbox list and warn about ID collisions.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))

        if manifest.get("format_version") != BUNDLE_FORMAT_VERSION:
            raise ValueError(
                f"Unsupported bundle format version: {manifest.get('format_version')}"
            )

        agent_ids = list(manifest.get("agent_ids") or [])
        if not agent_ids:
            raise ValueError("Bundle contains no agents")

        existing = set(load_agents_raw().keys())
        agents_info: list[dict] = []
        for agent_id in agent_ids:
            if not _AGENT_ID_RE.match(agent_id):
                raise ValueError(f"Invalid agent_id in bundle: {agent_id!r}")
            try:
                cfg = json.loads(zf.read(f"agents/{agent_id}/config.json"))
            except KeyError:
                raise ValueError(f"Bundle missing config for agent: {agent_id}")
            agents_info.append({
                "agent_id": agent_id,
                "display_name": cfg.get("display_name", agent_id),
                "description": cfg.get("description", ""),
                "exists_locally": agent_id in existing,
            })

    return {
        "format_version": manifest["format_version"],
        "exported_at": manifest.get("exported_at"),
        "agents": agents_info,
    }


def import_bundle(
    zip_bytes: bytes,
    selected_ids: Iterable[str] | None = None,
    conflict: ConflictStrategy = "abort",
) -> tuple[list[str], list[str]]:
    """Unpack a bundle and write the selected (or all) agents into the install.

    Returns ``(effective_agent_ids, warnings)``. Effective IDs may differ
    from bundle IDs when ``conflict='rename'`` is applied.
    """
    warnings: list[str] = []
    effective_ids: list[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for member in zf.namelist():
            if not _is_safe_member(member):
                raise ValueError(f"Unsafe path in bundle: {member!r}")

        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format_version") != BUNDLE_FORMAT_VERSION:
            raise ValueError(
                f"Unsupported bundle format version: {manifest.get('format_version')}"
            )

        bundle_ids = list(manifest.get("agent_ids") or [])
        wanted = list(selected_ids) if selected_ids is not None else bundle_ids
        unknown = [a for a in wanted if a not in bundle_ids]
        if unknown:
            raise ValueError(f"Bundle does not contain: {', '.join(unknown)}")

        agents = load_agents_raw()
        settings = _load_settings()
        tts_root = settings.setdefault("tts_agent_voices_per_engine", {})

        for bundle_id in wanted:
            if not _AGENT_ID_RE.match(bundle_id):
                raise ValueError(f"Invalid agent_id in bundle: {bundle_id!r}")

            if bundle_id in agents:
                if conflict == "abort":
                    raise FileExistsError(
                        f"Agent '{bundle_id}' existiert schon. "
                        f"Wähle 'overwrite' oder 'rename'."
                    )
                if conflict == "rename":
                    effective_id = _resolve_id_collision(
                        bundle_id, set(agents.keys()) | set(effective_ids)
                    )
                    warnings.append(
                        f"'{bundle_id}' existierte schon — importiert als '{effective_id}'."
                    )
                else:
                    effective_id = bundle_id
            else:
                effective_id = bundle_id

            base = f"agents/{bundle_id}"
            config = json.loads(zf.read(f"{base}/config.json"))
            sampling = json.loads(zf.read(f"{base}/sampling.json"))
            tts_per_engine = json.loads(zf.read(f"{base}/tts.json"))

            agents[effective_id] = config

            for member in zf.namelist():
                prefix = f"{base}/prompts/"
                if not member.startswith(prefix):
                    continue
                rest = member[len(prefix):]
                parts = rest.split("/")
                if len(parts) != 2 or parts[0] not in ("de", "en"):
                    continue
                target_dir = PROMPTS_DIR / parts[0] / effective_id
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / parts[1]).write_bytes(zf.read(member))

            if effective_id != bundle_id:
                renamed: dict = {}
                for k, v in sampling.items():
                    new_key = k.replace(f"{bundle_id}_", f"{effective_id}_", 1)
                    renamed[new_key] = v
                sampling = renamed

            for k, v in sampling.items():
                settings[k] = v

            for engine, entry in tts_per_engine.items():
                tts_root.setdefault(engine, {})[effective_id] = entry

            if (config.get("tools") or []):
                warnings.append(
                    f"'{effective_id}': Tool-Whitelist übernommen — fehlende Tools "
                    f"werden zur Laufzeit ignoriert."
                )

            effective_ids.append(effective_id)

        save_agents_raw(agents)
        _save_settings(settings)

        for member in zf.namelist():
            if not member.startswith("voices/"):
                continue
            parts = Path(member).parts
            if len(parts) != 3:
                continue
            bundle_dir, filename = parts[1], parts[2]
            target_dir = _engine_voice_dir(bundle_dir)
            if target_dir is None:
                warnings.append(f"Unbekannter Voice-Engine-Pfad: {member}")
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / filename
            if target.exists():
                warnings.append(
                    f"Voice '{bundle_dir}/{filename}' existierte schon — übersprungen."
                )
                continue
            target.write_bytes(zf.read(member))

    return effective_ids, warnings
