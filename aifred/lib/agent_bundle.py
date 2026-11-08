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
  voices/<name>/<name>.wav      optional, deduplicated across agents; the whole
  voices/<name>/<name>.txt|.lab folder of the shared voice tree (the voices folder of the TTS service, its /config)

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
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from . import prompt_loader
from .agent_config import (
    AGENT_ID_PATTERN,
    config_from_raw,
    load_agents_raw,
    prompt_folder_taken,
    save_agents_raw,
)
from .i18n import t
from .settings import SETTINGS_FILE, save_settings
from .tts_client import get_tts_client
from .user_error import BadRequest, Conflict, Missing

BUNDLE_FORMAT_VERSION = 1



class BundleUnreadable(BadRequest):
    """A bundle peek_bundle and import_bundle cannot take: no ZIP, a file missing in it, broken JSON, or contents the
    checks refuse."""


@contextmanager
def _opened(zip_bytes: bytes) -> Iterator[zipfile.ZipFile]:
    """The bundle as a ZIP; what the archive or its JSON cannot give becomes ``BundleUnreadable`` with the reason."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            yield zf
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as error:  # no ZIP, a member missing, broken JSON
        raise BundleUnreadable("bundle_unreadable", reason=str(error)) from error

ConflictStrategy = Literal["abort", "overwrite", "rename"]

# Whitelist for files inside agents/<id>/prompts/<lang>/ and voices/<engine>/.
# Strict to keep ZIP-import from writing weird filenames into prompts/voices.
_BUNDLE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _load_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data: dict = json.load(f)
        return data


def _voice_file_name(voice: str) -> str:
    """Strip the UI '★ ' prefix to get the on-disk WAV stem."""
    return voice.lstrip("★").strip() if voice else ""


def _is_safe_member(name: str) -> bool:
    """Reject ZIP members that try to escape via '..' or absolute paths."""
    if name.startswith(("/", "\\")):
        return False
    parts = Path(name).parts
    return ".." not in parts


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
        raise BadRequest("no_agents_selected")
    missing = [a for a in requested if a not in agents]
    if missing:
        raise Missing("agents_not_found", agents=", ".join(missing))

    voices_dir = get_tts_client().voices_dir()
    manifest = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "agent_ids": requested,
        "exported_at": int(time.time()),
    }

    buf = io.BytesIO()
    added_voices: set[str] = set()  # voice folders already in the ZIP

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
                prompt_dir = prompt_loader.PROMPTS_DIR / lang / agent_id
                if not prompt_dir.is_dir():
                    continue
                for prompt_file in sorted(prompt_dir.glob("*.txt")):
                    zf.write(prompt_file, f"{base}/prompts/{lang}/{prompt_file.name}")

            for entry in tts_per_engine.values():
                voice_stem = _voice_file_name(entry.get("voice", ""))
                voice_dir = voices_dir / voice_stem
                # Built-in speakers and cloud voices have no folder in the tree.
                if not voice_stem or voice_stem in added_voices or not voice_dir.is_dir():
                    continue
                for voice_file in sorted(voice_dir.iterdir()):
                    zf.write(voice_file, f"voices/{voice_stem}/{voice_file.name}")
                added_voices.add(voice_stem)

    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────


def _resolve_id_collision(desired_id: str, existing_ids: set[str], taken: Callable[[str], bool]) -> str:
    """Append _imported, _imported_2, … until no agent has the id and ``taken`` (a prompt folder) says it is free."""
    def free(candidate: str) -> bool:
        return candidate not in existing_ids and not taken(candidate)

    if free(desired_id):
        return desired_id
    base = f"{desired_id}_imported"
    if free(base):
        return base
    n = 2
    while not free(f"{base}_{n}"):
        n += 1
    return f"{base}_{n}"


def peek_bundle(zip_bytes: bytes) -> dict:
    """Read manifest + per-agent display names without writing.

    Used by the UI to show a checkbox list and warn about ID collisions.
    """
    with _opened(zip_bytes) as zf:
        manifest = json.loads(zf.read("manifest.json"))

        if manifest.get("format_version") != BUNDLE_FORMAT_VERSION:
            raise BundleUnreadable("bundle_version", version=manifest.get("format_version"))

        agent_ids = list(manifest.get("agent_ids") or [])
        if not agent_ids:
            raise BundleUnreadable("bundle_empty")

        existing = set(load_agents_raw().keys())
        agents_info: list[dict] = []
        for agent_id in agent_ids:
            if not re.fullmatch(AGENT_ID_PATTERN, agent_id):
                raise BundleUnreadable("bundle_bad_id", agent=agent_id)
            try:
                cfg = json.loads(zf.read(f"agents/{agent_id}/config.json"))
            except KeyError as error:
                raise BundleUnreadable("bundle_no_config", agent=agent_id) from error
            agents_info.append({
                "agent_id": agent_id,
                "display_name": cfg.get("display_name", agent_id),
                "description": cfg.get("description", ""),
                "exists_locally": agent_id in existing or prompt_folder_taken(agent_id, existing),
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
    lang: str | None = None,
) -> tuple[list[str], list[str]]:
    """Unpack a bundle and write the selected (or all) agents into the install.

    Returns ``(effective_agent_ids, warnings)``, the warnings in ``lang`` (None: the UI language). Effective IDs may
    differ from bundle IDs when ``conflict='rename'`` is applied.
    """
    warnings: list[str] = []
    effective_ids: list[str] = []

    with _opened(zip_bytes) as zf:
        voices_dir: Path | None = None  # asked of the TTS service when the first voice file comes
        for member in zf.namelist():
            if not _is_safe_member(member):
                raise BundleUnreadable("bundle_unsafe_path", member=member)

        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format_version") != BUNDLE_FORMAT_VERSION:
            raise BundleUnreadable("bundle_version", version=manifest.get("format_version"))

        bundle_ids = list(manifest.get("agent_ids") or [])
        wanted = list(selected_ids) if selected_ids is not None else bundle_ids
        unknown = [a for a in wanted if a not in bundle_ids]
        if unknown:
            raise BundleUnreadable("bundle_lacks", agents=", ".join(unknown))

        agents = load_agents_raw()
        settings = _load_settings()
        tts_root = settings.setdefault("tts_agent_voices_per_engine", {})

        for bundle_id in wanted:
            if not re.fullmatch(AGENT_ID_PATTERN, bundle_id):
                raise BundleUnreadable("bundle_bad_id", agent=bundle_id)

            # A folder of the prompts that is no agent's (shared, utility …) is no agent to overwrite: only a new id.
            taken = prompt_folder_taken(bundle_id, agents)
            if bundle_id in agents or taken:
                if conflict == "abort":
                    raise Conflict("bundle_agent_exists", agent=bundle_id)
                if conflict == "rename":
                    effective_id = _resolve_id_collision(
                        bundle_id, set(agents.keys()) | set(effective_ids), taken=lambda name: prompt_folder_taken(name, agents),
                    )
                    warnings.append(t("bundle_renamed", lang=lang, agent=bundle_id, new=effective_id))
                elif taken:
                    raise Conflict("agent_id_taken", agent=bundle_id)
                else:
                    effective_id = bundle_id
            else:
                effective_id = bundle_id

            base = f"agents/{bundle_id}"
            config = json.loads(zf.read(f"{base}/config.json"))
            sampling = json.loads(zf.read(f"{base}/sampling.json"))
            tts_per_engine = json.loads(zf.read(f"{base}/tts.json"))

            # Its layers point into its own folder, where the files below go (until 08.11.2026 they kept the
            # bundle's paths: an agent renamed on a conflict read, and on deletion removed, the original's folder).
            config["prompts"] = {key: f"{effective_id}/{Path(path).name}" for key, path in (config.get("prompts") or {}).items()}
            try:
                config_from_raw(config)   # what loading agents.json reads: a field missing there breaks every agent
            except KeyError as missing:
                raise BundleUnreadable("bundle_config_incomplete", agent=bundle_id, field=str(missing)) from missing
            agents[effective_id] = config

            prompts_root = prompt_loader.PROMPTS_DIR.resolve()
            for member in zf.namelist():
                prefix = f"{base}/prompts/"
                if not member.startswith(prefix):
                    continue
                rest = member[len(prefix):]
                parts = rest.split("/")
                if len(parts) != 2 or parts[0] not in ("de", "en"):
                    continue
                if not _BUNDLE_FILE_RE.match(parts[1]):
                    warnings.append(t("bundle_skipped_prompt_name", lang=lang, member=member))
                    continue
                target_dir = prompt_loader.PROMPTS_DIR / parts[0] / effective_id
                target_dir.mkdir(parents=True, exist_ok=True)
                target_path = (target_dir / parts[1]).resolve()
                try:
                    target_path.relative_to(prompts_root)
                except ValueError:
                    warnings.append(t("bundle_skipped_prompt_outside", lang=lang, member=member))
                    continue
                target_path.write_bytes(zf.read(member))

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
                warnings.append(t("bundle_tools_kept", lang=lang, agent=effective_id))

            effective_ids.append(effective_id)

        save_agents_raw(agents)
        save_settings(settings)

        for member in zf.namelist():
            if not member.startswith("voices/"):
                continue
            vparts = Path(member).parts
            if len(vparts) != 3:
                continue
            voice_name, filename = vparts[1], vparts[2]
            if not all(_BUNDLE_FILE_RE.match(part) and not part.startswith(".") for part in (voice_name, filename)):
                warnings.append(t("bundle_skipped_voice_path", lang=lang, member=member))
                continue
            if voices_dir is None:
                voices_dir = get_tts_client().voices_dir()
            target = voices_dir / voice_name / filename
            if target.exists():
                warnings.append(t("bundle_voice_exists", lang=lang, voice=f"{voice_name}/{filename}"))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))

    return effective_ids, warnings
