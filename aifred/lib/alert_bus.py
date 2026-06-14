"""Generic proactive alert pipeline — producer- and channel-agnostic core.

Producers (vision, system, scheduler, …) emit a neutral :class:`AlertEvent`.
The :class:`AlertDispatcher` matches it against central :class:`AlertRule`s,
throttles (dedup by key + per-rule cooldown + quiet hours), and delivers to
the rule's sinks.

Sinks are NOT a separate registry — the single source of truth is the
channel plugins' existing ``send_reply`` path, wrapped by
``message_processor.announce_to_channel(channel, recipient, text, media)``
(recipient resolution included). Producers and channels stay plugins; this
core only orchestrates. See docs/de/architecture/proactive-alerts.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Severity ordering for `min_severity` rule filtering.
_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


@dataclass
class AlertEvent:
    """A producer-neutral alert. The core never reads producer-specific
    fields — everything routable lives in these flat attributes."""

    producer: str                 # "vision", "system", "scheduler", …
    category: str                 # "face_unknown", "gpu_overheat", …
    source_id: str = ""           # "cam/office", "gpu:3", task id, …
    severity: str = "info"        # "info" | "warning" | "critical"
    title: str = ""
    body: str = ""
    # Throttle axis: repeated events sharing a dedup_key within a rule's
    # cooldown collapse to one alert. Vision uses the cluster_id (one alert
    # per happening); a scheduler would use the task id, etc.
    dedup_key: str = ""
    media: str | None = None      # primary image (subject; single-image channels)
    # Optional second image FILE PATH for the VLM: the wide context view of the
    # same moment. The VLM describes ``media`` (subject) + ``media_context``
    # (scene) together. None when there is only one view.
    media_context: str | None = None
    # Additional image URLs for the browser session (e.g. wide + zoom + crop).
    # ``media`` is the subject view; this is the full gallery shown in chat.
    media_gallery: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertRule:
    """Central routing rule. Filters are AND-combined; ``None`` = any."""

    producer: str
    sinks: list[str]                              # channel names (plugin_registry)
    category: str | None = None
    source_id: str | None = None
    min_severity: str = "info"
    min_interval_sec: float = 0.0                 # cooldown per (rule, dedup_key)
    quiet_hours: tuple[int, int] | None = None    # (start_hour, end_hour), local
    rule_id: str = ""                             # stable id for throttle bookkeeping
    compose: str = ""                             # "template" | "llm"; "" = config default

    def matches(self, ev: AlertEvent) -> bool:
        if ev.producer != self.producer:
            return False
        if self.category is not None and ev.category != self.category:
            return False
        if self.source_id is not None and ev.source_id != self.source_id:
            return False
        return _SEVERITY_ORDER.get(ev.severity, 0) >= _SEVERITY_ORDER.get(
            self.min_severity, 0
        )


# Deliver one matched event for one rule. Returns True if it reached at least
# one destination (a channel or the browser session). Injectable for tests;
# the default routes through the autonomous-delivery SSoT.
DeliverFn = Callable[["AlertEvent", "AlertRule"], Awaitable[bool]]


class AlertDispatcher:
    """Matches AlertEvents against rules, throttles, and hands each fired rule
    to the deliver function. ``deliver`` defaults to the SSoT path
    (:func:`_default_deliver`); tests inject a fake. State is the last-sent time
    per ``(rule, dedup_key)``, which drives the cooldown and is pruned bounded.
    """

    def __init__(
        self,
        rules: list[AlertRule],
        *,
        deliver: "DeliverFn | None" = None,
    ) -> None:
        from .config import ALERT_DEDUP_RETENTION_SEC
        self.rules = list(rules)
        self._deliver = deliver or _default_deliver
        self._last_sent: dict[tuple[str, str], datetime] = {}
        # How long to remember a fired (rule, dedup_key) — the longer of the
        # rules' time cooldowns and the cluster-dedup retention. Keeping a key
        # this long is what makes "one alert per cluster" hold.
        self._prune_cutoff = max(
            [r.min_interval_sec for r in self.rules] + [ALERT_DEDUP_RETENTION_SEC]
        )

    def _rule_key(self, rule: AlertRule, ev: AlertEvent) -> tuple[str, str]:
        return (rule.rule_id or rule.producer, ev.dedup_key)

    @staticmethod
    def _in_quiet_hours(rule: AlertRule, now: datetime) -> bool:
        if not rule.quiet_hours:
            return False
        start, end = rule.quiet_hours
        h = now.hour
        if start <= end:
            return start <= h < end
        return h >= start or h < end  # window wraps midnight

    def _throttled(self, rule: AlertRule, ev: AlertEvent, now: datetime) -> bool:
        last = self._last_sent.get(self._rule_key(rule, ev))
        if last is None:
            return False
        # A non-empty dedup_key identifies one discrete happening (vision:
        # the cluster_id). One alert per happening — every repeat of the SAME
        # key is suppressed for as long as we remember it (see _prune). A new
        # happening carries a new key and alerts immediately. This is "one
        # alert per cluster", independent of any wall-clock window. Keyless
        # producers fall back to a per-rule time cooldown.
        if ev.dedup_key:
            return True
        if rule.min_interval_sec <= 0:
            return False
        return (now - last).total_seconds() < rule.min_interval_sec

    def _prune(self, now: datetime) -> None:
        """Forget last-sent entries older than the retention window — beyond
        it they can neither time-throttle a keyless event nor dedup a
        (deterministic, non-recurring) cluster key, so keeping them just leaks
        memory."""
        self._last_sent = {
            k: t for k, t in self._last_sent.items()
            if (now - t).total_seconds() < self._prune_cutoff
        }

    async def emit(self, ev: AlertEvent) -> int:
        """Route one event. Returns how many rules delivered."""
        now = ev.timestamp or datetime.now()
        self._prune(now)
        delivered = 0
        for rule in self.rules:
            if not rule.matches(ev):
                continue
            if self._in_quiet_hours(rule, now):
                continue
            if self._throttled(rule, ev, now):
                continue
            try:
                ok = await self._deliver(ev, rule)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "alert: deliver failed for rule '%s': %s",
                    rule.rule_id or rule.producer, e,
                )
                ok = False
            if ok:
                self._last_sent[self._rule_key(rule, ev)] = now
                delivered += 1
        return delivered


def _format(ev: AlertEvent) -> str:
    """Template text rendering — title + body (deterministic, no LLM)."""
    if ev.title and ev.body:
        return f"{ev.title}\n{ev.body}"
    return ev.title or ev.body


async def _describe_media_via_vlm(ev: AlertEvent) -> str | None:
    """Beschreibt den Alert-Frame (``ev.media``) mit dem aktiven VLM und
    returnt eine kurze Szenenbeschreibung — oder ``None`` bei Fehlschlag
    (kein Frame, Modell nicht geladen, leere Antwort). Einzelbild über den
    geteilten VLM-Pfad (``analyze_sequence`` → Downscale auf
    VISION_VLM_MAX_PIXELS). Stellt das Kamera-Briefing (``prompt_context``)
    als Kontext voran, damit das VLM weiß, worauf die Kamera blickt."""
    if not ev.media:
        return None
    from pathlib import Path
    frame_file = Path(ev.media)
    if not frame_file.exists():
        return None
    try:
        from datetime import datetime as _dt
        from .frame_sources import Frame
        from .prompt_loader import get_vision_event_single_prompt
        from .vision_analyzer import analyze_sequence
        from .vision_prewarm import get_active_vlm_model
        from .vision_store import VisionStore

        model = get_active_vlm_model()
        if not model:
            return None
        prompt = get_vision_event_single_prompt().strip()
        rec = VisionStore().get_source(ev.source_id) if ev.source_id else None
        briefing = str((rec or {}).get("prompt_context") or "").strip()
        if briefing:
            prompt = f"{briefing}\n\n{prompt}"
        # Personalisierung: hat die Gesichtserkennung eine Person sicher
        # identifiziert (emit_face_alert setzt das nur bei face_known),
        # bekommt das VLM den Namen — so wird aus "ein Mann mit Brille"
        # ein "Peuqui sitzt am Schreibtisch". Nur als Fakt vorangestellt,
        # nicht suggestiv ("prüfe ob…"), das Match steht ja schon fest.
        identities = ev.metadata.get("identity_names") or []
        if isinstance(identities, str):  # Toleranz für Altformat
            identities = [identities]
        identities = [str(n).strip() for n in identities if str(n).strip()]
        if identities:
            who = ", ".join(identities)
            prompt = (
                f"Die im Bild sicher erkannten Personen sind: {who}. "
                f"Nenne jede beim Namen.\n\n{prompt}"
            )
        # Subjekt-Ansicht (Zoom) zuerst, dann die Weitwinkel-Kontext-Ansicht
        # desselben Moments — das VLM sieht Nahaufnahme UND Szene. Der Crop
        # entfällt bewusst (das Gesicht hat InsightFace bereits erkannt; das VLM
        # macht Kontext-Beschreibung, keine Re-Identifikation).
        ts_v = ev.timestamp or _dt.now()
        frames = [Frame(source_id=ev.source_id or "", timestamp=ts_v,
                        image_bytes=frame_file.read_bytes())]
        if ev.media_context:
            ctx_file = Path(ev.media_context)
            if ctx_file.exists():
                frames.append(Frame(source_id=ev.source_id or "", timestamp=ts_v,
                                    image_bytes=ctx_file.read_bytes()))
        result = await analyze_sequence(frames, prompt, model=str(model))
        desc = (result.text or "").strip()
        return desc or None
    except Exception as e:  # noqa: BLE001
        logger.warning("alert: VLM describe failed for %s: %s", ev.source_id, e)
        return None


async def _compose_via_llm(ev: AlertEvent, extra_context: str = "") -> str | None:
    """AIfred formuliert den Alert via ``process_inbound`` — das legt
    gleichzeitig die Browser-Session an. Returnt den Text oder None bei
    Fehlschlag (Caller fällt dann aufs Template zurück).

    ``extra_context``: optionale VLM-Bildbeschreibung, die AIfred als
    zusätzlichen Kontext mitbekommt (compose-Modus ``vlm+llm``)."""
    from datetime import datetime as _dt
    from .envelope import InboundMessage
    from .message_processor import process_inbound

    prompt = f"[{ev.producer}] {ev.title} — {ev.body}".strip(" —")
    if extra_context:
        prompt = f"{prompt}\n\nBildbeschreibung (VLM): {extra_context}"
    msg = InboundMessage(
        channel=ev.producer,
        channel_id=ev.source_id or ev.producer,
        sender="system",
        text=prompt,
        timestamp=ev.timestamp or _dt.now(),
        metadata={"wake_agent": "aifred", "max_tier": 0},
        target_agent="aifred",
    )
    out = await process_inbound(msg)
    return out.text if out and out.text else None


async def _default_deliver(ev: AlertEvent, rule: AlertRule) -> bool:
    """SSoT-Zustellung: Text erzeugen (Template oder LLM), als normale
    Browser-Session sichtbar machen und an die Kanal-Sinks der Regel
    announcen. Eine Wahrheit pro Kanal (``announce_to_channel``) und pro
    Session-Eintrag (``record_autonomous_turn`` bzw. ``process_inbound``)."""
    from .config import ALERT_COMPOSE_DEFAULT
    from .message_processor import (
        announce_to_channel,
        record_autonomous_turn,
        resolve_announce_targets,
    )

    mode = (rule.compose or ALERT_COMPOSE_DEFAULT).lower()
    text = _format(ev)
    recorded = False  # browser session already written?

    # VLM-Bildbeschreibung holen, wenn der Modus sie braucht ("vlm" oder
    # "vlm+llm"). Läuft hier — NACH dem Dedup in emit() — also genau einmal
    # pro Vorkommnis, nicht pro Frame.
    vlm_desc: str | None = None
    if "vlm" in mode:
        vlm_desc = await _describe_media_via_vlm(ev)
        # Beschreibung in die Vision-Events des Clusters zurückschreiben:
        # der Alert hat das VLM ohnehin laufen lassen — so erscheint der
        # Text sofort im Casus-Log UND der nächtliche bulk-describe
        # überspringt diese Events (kein zweiter VLM-Lauf für dasselbe
        # Vorkommnis). dedup_key IST der cluster_id (siehe vision_alerts).
        if vlm_desc and ev.producer == "vision" and ev.dedup_key:
            try:
                from .vision_store import VisionStore
                n = VisionStore().apply_cluster_description(
                    ev.dedup_key, vlm_desc, "alert-vlm",
                )
                if n:
                    logger.info(
                        "alert: persisted VLM description to %d event(s) "
                        "in cluster %s", n, ev.dedup_key,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("alert: persist VLM description failed: %s", e)

    if mode in ("llm", "vlm+llm"):
        # AIfred-Pfad: formuliert den finalen Text, bei "vlm+llm" mit der
        # VLM-Beschreibung als Kontext. process_inbound legt die Session an.
        try:
            composed = await _compose_via_llm(ev, extra_context=vlm_desc or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("alert: LLM compose failed (%s) — template fallback", e)
            composed = None
        if composed:
            text = composed
            recorded = True  # process_inbound recorded the session itself
    elif mode == "vlm":
        # Reine VLM-Beschreibung in den Body. Bei leerer VLM-Antwort bleibt
        # der Template-Text (gleiches Degradations-Muster wie bei "llm").
        if vlm_desc:
            text = f"{ev.title}\n{vlm_desc}\n{ev.body}".strip()

    if not recorded:
        try:
            record_autonomous_turn(
                ev.producer, ev.source_id or ev.producer,
                ev.title or ev.category, text, media=ev.media,
                media_gallery=ev.media_gallery,
            )
            recorded = True
        except Exception as e:  # noqa: BLE001
            logger.warning("alert: session record failed: %s", e)

    # Severity → Audio-Type-Tupel fuer Sinks die einen lokalen Sound vor
    # der Nachricht abspielen koennen (FreeEcho.2: alarm_wav vs.
    # notification_wav). ``critical``/``warning`` (z.B. unbekannte Person)
    # triggern den auffaelligen alarm-Sound, ``info`` (z.B. bekannte Person)
    # den sanften notification-Sound. Andere Sinks (Telegram, Email, …)
    # ignorieren das Feld stillschweigend.
    audio_type = "alarm" if ev.severity in ("critical", "warning") else "notification"
    sink_metadata = {
        "audio_type": audio_type,
        "severity": ev.severity,
        "category": ev.category,
    }

    # Sink-Format: "channel" oder "channel:ziel". ziel ist kanalspezifisch —
    # fuer freeecho2 ein room, "@gruppe" oder "*" (Broadcast). Die Expansion
    # auf konkrete Empfaenger macht resolve_announce_targets serverseitig;
    # pro Empfaenger genau ein announce_to_channel (SSoT, kein Parallelpfad).
    channel_ok = False
    for sink in rule.sinks:
        channel, _, target = sink.partition(":")
        recipients = resolve_announce_targets(channel, target) or [target]
        for recipient in recipients:
            if await announce_to_channel(
                channel, recipient, text, media=ev.media, metadata=sink_metadata,
            ):
                channel_ok = True

    # Browser-Session zählt als Zustellung (Kontroll-Trail) — auch wenn ein
    # Kanal nicht konfiguriert ist.
    return channel_ok or recorded


# ── Runtime: central rule config + shared dispatcher ──────────────────────

def _rules_path():
    from .config import DATA_DIR
    return DATA_DIR / "alert_rules.json"


def load_rules() -> list[AlertRule]:
    """Load the central alert rules from ``data/alert_rules.json`` (a JSON
    list of rule objects). Missing/invalid file → no rules (no alerts), a safe
    default. Unknown keys are ignored so the schema can grow."""
    import json

    path = _rules_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("alert rules unreadable (%s): %s", path, e)
        return []
    if not isinstance(raw, list):
        logger.warning("alert rules: expected a JSON list, got %s", type(raw).__name__)
        return []
    fields = {
        "producer", "sinks", "category", "source_id", "min_severity",
        "min_interval_sec", "quiet_hours", "rule_id", "compose",
    }
    rules: list[AlertRule] = []
    for entry in raw:
        if not isinstance(entry, dict) or "producer" not in entry or "sinks" not in entry:
            logger.warning("alert rules: skipping invalid entry %r", entry)
            continue
        kwargs = {k: v for k, v in entry.items() if k in fields}
        qh = kwargs.get("quiet_hours")
        if isinstance(qh, list) and len(qh) == 2:
            kwargs["quiet_hours"] = (int(qh[0]), int(qh[1]))
        rules.append(AlertRule(**kwargs))
    return rules


_default_dispatcher: AlertDispatcher | None = None


def get_default_dispatcher() -> AlertDispatcher:
    """Shared dispatcher, rules loaded once from the central config. Producers
    emit here. Sinks resolve via plugin_registry (SSoT)."""
    global _default_dispatcher
    if _default_dispatcher is None:
        rules = load_rules()
        _default_dispatcher = AlertDispatcher(rules)
        logger.info("alert dispatcher ready (%d rule(s))", len(rules))
    return _default_dispatcher


def reload_rules() -> int:
    """Regeln frisch von ``data/alert_rules.json`` laden und den laufenden
    Dispatcher neu aufbauen — damit Änderungen aus der UI sofort greifen, ohne
    Service-Neustart. Returnt die Anzahl geladener Regeln. (Throttle-State wird
    dabei zurückgesetzt — im schlimmsten Fall ein zusätzlicher Alert.)"""
    global _default_dispatcher
    rules = load_rules()
    _default_dispatcher = AlertDispatcher(rules)
    logger.info("alert dispatcher reloaded (%d rule(s))", len(rules))
    return len(rules)
