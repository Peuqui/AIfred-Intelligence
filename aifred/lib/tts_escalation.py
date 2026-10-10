"""TTS-Eskalationsliste — welche Engine auf welchem Rechner spricht.

Zwei Listen in ``settings.json``:

- ``tts_hosts``: andere Rechner, die TTS-Container mit derselben API
  betreiben (Name, Adresse, optional abweichende Ports je Engine). Dieser
  Rechner ist implizit.
- ``tts_escalation``: geordnete Einträge aus Engine und Host. Der erste
  passende Eintrag von oben spricht.

Der Code unterscheidet nur nach der Art eines Eintrags (lokal, remote,
Cloud), nie nach dem Namen eines Rechners.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from .tts_engines import TTSEngine, get_engine


@dataclass(frozen=True)
class TTSHost:
    """Ein anderer Rechner mit TTS-Containern.

    ``enabled``: aus = seine Einträge werden übersprungen und seine Container
    gestoppt (VRAM frei). ``ssh``: ``user@host:port`` — AIfred startet und
    stoppt die Container dort über ``scripts/tts-host-ctl.sh`` (eigener
    Schlüssel, erzwungener Befehl); leer = die Container laufen von selbst."""

    name: str
    address: str
    enabled: bool
    ssh: str
    ports: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EscalationEntry:
    """Ein Eintrag der Liste: Engine, gebunden an einen Host (``None`` = dieser Rechner)."""

    engine: TTSEngine
    host: TTSHost | None
    enabled: bool

    @property
    def label(self) -> str:
        """Kurzname für Debug-Zeilen, z. B. ``xtts@Aragon``, ``edge@cloud``."""
        if self.host is not None:
            return f"{self.engine.key}@{self.host.name}"
        return f"{self.engine.key}@{'cloud' if self.engine.cloud else 'local'}"


def _parse_hosts(raw_hosts: list[dict[str, Any]]) -> dict[str, TTSHost]:
    hosts: dict[str, TTSHost] = {}
    for raw in raw_hosts:
        name = str(raw["name"])
        if name in hosts:
            raise ValueError(f"tts_hosts: duplicate host name {name!r}")
        ports = {str(key): int(port) for key, port in (raw.get("ports") or {}).items()}
        for engine_key in ports:
            if get_engine(engine_key) is None:
                raise ValueError(f"tts_hosts[{name}]: port for unknown TTS engine {engine_key!r}")
        hosts[name] = TTSHost(
            name=name, address=str(raw["address"]), enabled=bool(raw["enabled"]),
            ssh=str(raw["ssh"]), ports=ports,
        )
    return hosts


def _bind_entry(raw: dict[str, Any], hosts: dict[str, TTSHost]) -> EscalationEntry:
    engine_key = str(raw["engine"])
    engine = get_engine(engine_key)
    if engine is None:
        raise ValueError(f"tts_escalation: unknown TTS engine {engine_key!r}")
    host_name = raw.get("host")
    if host_name is None:
        return EscalationEntry(engine=engine, host=None, enabled=bool(raw["enabled"]))
    host = hosts.get(str(host_name))
    if host is None:
        raise ValueError(f"tts_escalation: entry {engine_key!r} names unknown host {host_name!r}")
    remote = engine.at_host(host.address, host.ports.get(engine_key))
    return EscalationEntry(engine=remote, host=host, enabled=bool(raw["enabled"]))


def escalation_entries(settings: dict[str, Any] | None = None) -> list[EscalationEntry]:
    """Die Eskalationsliste aus den Einstellungen, in ihrer Reihenfolge (auch
    deaktivierte Einträge). Fehlerhafte Einträge lösen ``ValueError`` aus —
    eine kaputte Liste wird nicht still übergangen."""
    from .settings import persisted_settings

    source = settings if settings is not None else persisted_settings()
    hosts = _parse_hosts(source["tts_hosts"])
    return [_bind_entry(raw, hosts) for raw in source["tts_escalation"]]


# ── Auswahl und Synthese ───────────────────────────────────────────


class NoSpeechAvailable(RuntimeError):
    """Kein Eintrag der Liste kann gerade sprechen."""


def resolve_voice(engine_key: str, agent: str) -> tuple[str, float, float]:
    """Stimme, Tempo und Tonhöhe eines Agenten für eine Engine (SSOT).

    Reihenfolge: Einstellung des Users für diese Engine und diesen Agenten,
    dann die des Agenten ``aifred``, dann der Standard des Agenten aus
    ``agents.json``, dann der von ``aifred``. Ohne Stimme oder bei ungültigem
    Tempo/Tonhöhe: :class:`TTSFailure` — der Eintrag fällt aus, die Liste
    geht weiter."""
    from .agent_config import get_tts_voice_default
    from .settings import persisted_settings
    from .tts_engines import TTSFailure, parse_speed_factor

    user_voices = persisted_settings().get("tts_agent_voices_per_engine", {}).get(engine_key, {})
    user_cfg = user_voices.get(agent) or user_voices.get("aifred") or {}
    default_cfg = get_tts_voice_default(agent, engine_key)
    if not default_cfg.get("voice"):
        default_cfg = get_tts_voice_default("aifred", engine_key)

    voice = str(user_cfg.get("voice") or default_cfg.get("voice") or "")
    speed = parse_speed_factor(user_cfg.get("speed") or default_cfg.get("speed") or "1.0")
    pitch = parse_speed_factor(user_cfg.get("pitch") or default_cfg.get("pitch") or "1.0")
    if not voice:
        raise TTSFailure("software", f"no voice configured for agent {agent!r} on {engine_key}")
    if speed is None or pitch is None:
        raise TTSFailure("software", f"invalid speed/pitch for agent {agent!r} on {engine_key}")
    return voice, speed, pitch


async def _skip_reason(entry: EscalationEntry, report: Callable[[str], None]) -> str | None:
    """Warum dieser Eintrag gerade nicht sprechen kann — ``None`` = er kann.

    Ein lokaler GPU-Eintrag, dessen Container nicht läuft, wird gestartet,
    wenn sein gemessener Spitzenbedarf in den freien Speicher einer Karte
    passt; das Hauptmodell wird dafür nie neu geladen."""
    import asyncio

    engine = entry.engine
    if not entry.enabled:
        return "disabled"
    if not engine.is_remote and not await asyncio.to_thread(engine.is_installed):
        return "docker image not built"
    if entry.host is not None and not entry.host.enabled:
        return f"host {entry.host.name} switched off"
    if await asyncio.to_thread(engine.is_running):
        return None
    if entry.host is not None:
        if not entry.host.ssh:
            return f"not serving on {engine.address}"
        return await _start_remote_engine(entry.engine, entry.host, report)
    if not engine.needs_gpu:
        return "not available"
    return await _start_local_gpu_engine(engine, report)


def host_control(host: TTSHost, *command: str) -> str:
    """Run the TTS host's control script via SSH (``start <service>``,
    ``stop``, ``status``) and return its output. The key is pinned to that
    script on the host, so nothing else can run. ``RuntimeError`` with the
    host's message on failure."""
    import subprocess

    from .config import TTS_HOST_SSH_COMMAND_TIMEOUT_S, TTS_HOST_SSH_CONNECT_TIMEOUT_S, TTS_HOST_SSH_KEY

    target, _, port = host.ssh.rpartition(":")
    if not target or not port.isdigit():
        raise RuntimeError(f"tts_hosts[{host.name}].ssh must be user@host:port, got {host.ssh!r}")
    try:
        result = subprocess.run(
            [
                "ssh", "-i", str(TTS_HOST_SSH_KEY), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", f"ConnectTimeout={TTS_HOST_SSH_CONNECT_TIMEOUT_S}", "-p", port, target, *command,
            ],
            capture_output=True, text=True, timeout=TTS_HOST_SSH_COMMAND_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{host.name}: '{' '.join(command)}' timed out") from exc
    if result.returncode != 0:
        raise RuntimeError(f"{host.name}: '{' '.join(command)}' failed: {result.stderr.strip()[-300:]}")
    return result.stdout


async def _start_remote_engine(engine: TTSEngine, host: TTSHost, report: Callable[[str], None]) -> str | None:
    """Start the engine's container on the TTS host and wait until it serves
    — the reply waits for it (a fast remote card beats a worse fallback)."""
    import asyncio
    import time

    from .formatting import format_number

    report(f"TTS escalation: starting {engine.key} on {host.name}")
    started = time.monotonic()
    try:
        await asyncio.to_thread(host_control, host, "start", engine.service_dir)
    except RuntimeError as exc:
        return f"start failed: {exc}"
    deadline = started + engine.startup_timeout_s
    while time.monotonic() < deadline:
        if await asyncio.to_thread(engine.is_running):
            report(
                f"TTS escalation: {engine.key} on {host.name} ready after "
                f"{format_number(time.monotonic() - started, 1)} s"
            )
            return None
        await asyncio.sleep(1)
    return f"not ready on {host.name} within {format_number(engine.startup_timeout_s)} s"


async def _start_local_gpu_engine(engine: TTSEngine, report: Callable[[str], None]) -> str | None:
    """Lokale GPU-Engine starten, wenn ihr Spitzenbedarf auf eine Karte passt:
    bevorzugt die Sammelkarte, sonst die Karte mit dem meisten freien Speicher.
    Lädt das Hauptmodell später neu und braucht die Karte, räumt der
    GPU-Wächter der Backends den Container wieder ab (Hauptmodell vor TTS)."""
    import asyncio

    from . import tts_vram_cache
    from .config import LLAMACPP_TTS_BURNIN_HEADROOM_MB
    from .formatting import format_number
    from .nvidia_smi import query
    from .vision_gpu_select import pick_tts_gpu

    peak = tts_vram_cache.get(engine.key)
    if peak is None:
        return "no burn-in peak measured — VRAM need unknown"
    needed = peak + LLAMACPP_TTS_BURNIN_HEADROOM_MB
    rows = await asyncio.to_thread(query, "index,uuid,memory.free") or []
    cards = {int(row["index"]): (str(row["uuid"]), int(row["memory.free"])) for row in rows}
    if not cards:
        return "nvidia-smi lists no GPUs"
    fitting = [index for index, (_uuid, free) in cards.items() if free >= needed]
    if not fitting:
        most = max(cards, key=lambda index: cards[index][1])
        return (
            f"needs {format_number(needed)} MiB, most free {format_number(cards[most][1])} MiB "
            f"on GPU {most}"
        )
    home = await asyncio.to_thread(pick_tts_gpu)
    gpu = home if home in fitting else max(fitting, key=lambda index: cards[index][1])
    placed = engine if gpu == home else engine.on_gpu(cards[gpu][0])
    report(
        f"TTS escalation: starting {engine.key} on GPU {gpu} "
        f"(needs {format_number(needed)} MiB, free {format_number(cards[gpu][1])} MiB)"
    )
    ok, message, _device = await asyncio.to_thread(placed.ensure_ready)
    return None if ok else f"start failed: {message}"


_ENGINE_SLOTS: dict[tuple[str, str], "asyncio.Semaphore"] = {}


def _engine_slots(engine: TTSEngine) -> "asyncio.Semaphore":
    """Shared limit of simultaneous syntheses per engine and host
    (``max_parallel_requests``) — across every reply and channel."""
    import asyncio

    key = (engine.key, engine.address)
    if key not in _ENGINE_SLOTS:
        _ENGINE_SLOTS[key] = asyncio.Semaphore(engine.max_parallel_requests)
    return _ENGINE_SLOTS[key]


class SpeechRun:
    """Eine Ansage oder Antwort: welcher Eintrag spricht, und was bei einem
    Ausfall passiert.

    Vor dem ersten Ton fällt ein Eintrag still aus (nur Debug-Zeile), der
    nächste passende übernimmt. Fällt ein Eintrag aus, nachdem er schon
    gesprochen hat, sagt der nächste zuerst den Stimmwechsel samt Grund an
    und spricht dann den Satz, der nicht fertig wurde. Einmal ausgefallene
    Einträge bleiben für diesen Lauf aus — die Stimme springt nicht hin und
    her. Die Auswahl ist per Lock serialisiert, damit parallel erzeugte
    Sätze keinen Container doppelt starten."""

    def __init__(
        self,
        language: str,
        label: str,
        *,
        entries: list[EscalationEntry] | None = None,
        report: Callable[[str], None] | None = None,
    ) -> None:
        """``report`` receives the English debug lines (who speaks, skips,
        failures); default is the debug bus. The browser passes its own
        console (``add_debug``) so the lines reach the session's console."""
        import asyncio

        from .debug_bus import debug

        self.language = language
        self.label = label
        self._report = report or debug
        self._entries = entries if entries is not None else escalation_entries()
        self._current: EscalationEntry | None = None
        self._failed: set[str] = set()
        self._spoken: set[str] = set()
        self._switch_reason: str | None = None
        # Who spoke, in order, with the reason that made each successor take over.
        self._speakers: list[tuple[EscalationEntry, str | None]] = []
        self._lock = asyncio.Lock()

    async def entry(self) -> EscalationEntry:
        """Der Eintrag, der jetzt spricht — beim ersten Aufruf ausgewählt.
        :class:`NoSpeechAvailable`, wenn keiner kann."""
        async with self._lock:
            if self._current is not None and self._current.label not in self._failed:
                return self._current
            for entry in self._entries:
                if entry.label in self._failed:
                    continue
                reason = await _skip_reason(entry, lambda message: self._report(f"🔊 [{self.label}] {message}"))
                if reason is None:
                    self._report(f"🔊 [{self.label}] TTS escalation: {entry.label} speaks")
                    self._current = entry
                    return entry
                self._report(f"🔊 [{self.label}] TTS escalation: skip {entry.label} ({reason})")
            raise NoSpeechAvailable(f"[{self.label}] no TTS entry can speak")

    async def synthesize(self, text: str, agent: str) -> str:
        """``text`` sprechen lassen; Audio-URL des Eintrags, der es geschafft hat.
        :class:`NoSpeechAvailable`, wenn die Liste erschöpft ist."""
        from .audio_processing import generate_tts
        from .tts_engines import TTSFailure

        while True:
            entry = await self.entry()
            spoken_text = self._announcement(entry) + text
            try:
                voice, speed, pitch = resolve_voice(entry.engine.key, agent)
                async with _engine_slots(entry.engine):
                    url = await generate_tts(
                        spoken_text, voice, speed, entry.engine,
                        pitch=pitch, agent=agent, language=self.language,
                    )
            except TTSFailure as failure:
                self._fail(entry, failure.reason, str(failure))
                continue
            except Exception as exc:  # noqa: BLE001 — any other error is ours: next entry
                self._fail(entry, "software", f"{type(exc).__name__}: {exc}")
                continue
            if entry.label not in self._spoken:
                self._speakers.append((entry, self._switch_reason))
            if self._switch_reason is not None:
                self._report(f"🔊 [{self.label}] TTS escalation: voice change announced, {entry.label} continues")
                self._switch_reason = None
            self._spoken.add(entry.label)
            return url

    def note(self, lang: str) -> str:
        """Vermerk für die Chat-Blase: wer gesprochen hat, bei einem Wechsel mit
        Grund, z. B. ``XTTS · Aragon → DashScope · Cloud (Serverausfall)``.
        Leer, wenn noch niemand gesprochen hat."""
        from .i18n import t

        parts = []
        for entry, reason in self._speakers:
            part = f"{entry.engine.label_short} · {location_label(entry, lang)}"
            if reason is not None:
                part += f" ({t(f'tts_reason_{reason}', lang=lang)})"
            parts.append(part)
        return " → ".join(parts)

    def _fail(self, entry: EscalationEntry, reason: str, detail: str) -> None:
        self._failed.add(entry.label)
        self._report(f"🔊 [{self.label}] TTS escalation: {entry.label} failed ({reason}): {detail}")
        if entry.label in self._spoken and self._switch_reason is None:
            self._switch_reason = reason

    def _announcement(self, entry: EscalationEntry) -> str:
        """Satz vor dem ersten Text des Nachfolgers nach einem Ausfall mitten im Text."""
        from .i18n import t

        if self._switch_reason is None:
            return ""
        target = entry.engine.label_short
        if entry.host is not None:
            target = t("tts_switch_target_remote", lang=self.language, engine=target, host=entry.host.name)
        return t(f"tts_switch_{self._switch_reason}", lang=self.language, target=target) + " "


async def choose_speaker(engine_key: str, label: str) -> EscalationEntry:
    """Eine Engine für eine Aufgabe, die durchgehend dieselbe Stimme braucht
    (Hörbuch): ``engine_key`` gesetzt → genau diese Engine auf diesem Rechner,
    sofern sie gerade kann; leer → der oberste passende Eintrag der Liste.
    :class:`NoSpeechAvailable` mit Grund, wenn keine kann."""
    from .tts_engines import require_engine

    if not engine_key:
        return await SpeechRun("", label).entry()
    from .debug_bus import debug

    entry = EscalationEntry(engine=require_engine(engine_key), host=None, enabled=True)
    reason = await _skip_reason(entry, lambda message: debug(f"🔊 [{label}] {message}"))
    if reason is not None:
        raise NoSpeechAvailable(f"[{label}] TTS engine {engine_key!r} cannot speak now: {reason}")
    return entry


# ── Bausteine für Browser und Menü ─────────────────────────────────


def location_label(entry: EscalationEntry, lang: str) -> str:
    """Wo ein Eintrag spricht: Name des Hosts, „Cloud“, „lokal“ (Container auf
    diesem Rechner) oder „CPU“ (Prozess auf diesem Rechner)."""
    from .i18n import t

    if entry.host is not None:
        return entry.host.name
    if entry.engine.cloud:
        return t("tts_location_cloud", lang=lang)
    if entry.engine.runs_in_container:
        return t("tts_location_local", lang=lang)
    return t("tts_location_cpu", lang=lang)


def first_enabled_entry(settings: dict[str, Any] | None = None) -> EscalationEntry | None:
    """Der oberste aktive Eintrag — seine Sprecheinheit bestimmt, ob der Browser
    satzweise streamt (der tatsächlich sprechende Eintrag steht erst beim ersten
    Satz fest)."""
    return next((entry for entry in escalation_entries(settings) if entry.enabled), None)


def usable_engine_keys(settings: dict[str, Any] | None = None) -> list[str]:
    """Engines whose voices are worth configuring, in registry order: ready on
    this machine (image built, or none needed) or in the list on another host.
    Engine dropdowns offer only these — an engine nobody can run is noise."""
    from .tts_engines import TTS_ENGINES

    remote = {entry.engine.key for entry in escalation_entries(settings) if entry.host is not None}
    return [key for key, engine in TTS_ENGINES.items() if key in remote or engine.is_installed()]


def planned_tts_engine(model_id: str, settings: dict[str, Any] | None = None) -> str:
    """Die lokale GPU-Engine, für die das LLM-Profil ``<model>-tts-<engine>``
    Platz freihält: der oberste aktive lokale GPU-Eintrag, für den das Modell
    ein kalibriertes TTS-Profil hat. ``""``, wenn keiner passt — dann lädt das
    Basisprofil, und lokale GPU-Einträge sprechen nur, wenn Platz frei ist."""
    from pathlib import Path

    from .calibration import has_llamaswap_tts_variant
    from .config import LLAMASWAP_CONFIG_PATH

    for entry in escalation_entries(settings):
        engine = entry.engine
        if (
            entry.enabled and entry.host is None and engine.needs_gpu
            and has_llamaswap_tts_variant(Path(LLAMASWAP_CONFIG_PATH), model_id, engine.key)
        ):
            return engine.key
    return ""


def entry_status(entry: EscalationEntry) -> str:
    """What an entry would do right now, as a key (``tts_status_<key>`` in
    i18n): checks only — health, image, host switch, credentials — and never
    starts anything. For the status column of the list editor."""
    engine = entry.engine
    if not entry.enabled:
        return "disabled"
    if entry.host is not None and not entry.host.enabled:
        return "host_off"
    if not engine.is_remote and engine.runs_in_container and not engine.is_installed():
        return "no_image"
    if engine.is_running():
        return "cloud" if engine.cloud else "running" if engine.default_port else "ready"
    if engine.cloud:
        return "no_access"
    if entry.host is not None and not entry.host.ssh:
        return "unreachable"
    return "sleeping"
