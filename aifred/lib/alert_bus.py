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
    media: str | None = None      # optional image path / URL
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
        self.rules = list(rules)
        self._deliver = deliver or _default_deliver
        self._last_sent: dict[tuple[str, str], datetime] = {}
        self._max_interval = max(
            (r.min_interval_sec for r in self.rules), default=0.0
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
        if rule.min_interval_sec <= 0:
            return False
        last = self._last_sent.get(self._rule_key(rule, ev))
        return last is not None and (now - last).total_seconds() < rule.min_interval_sec

    def _prune(self, now: datetime) -> None:
        """Drop last-sent entries older than the longest cooldown — beyond it
        they can never throttle anything, so keeping them just leaks memory."""
        if self._max_interval <= 0:
            self._last_sent.clear()
            return
        cutoff = self._max_interval
        self._last_sent = {
            k: t for k, t in self._last_sent.items()
            if (now - t).total_seconds() < cutoff
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


async def _compose_via_llm(ev: AlertEvent) -> str | None:
    """AIfred formuliert den Alert via ``process_inbound`` — das legt
    gleichzeitig die Browser-Session an. Returnt den Text oder None bei
    Fehlschlag (Caller fällt dann aufs Template zurück)."""
    from datetime import datetime as _dt
    from .envelope import InboundMessage
    from .message_processor import process_inbound

    prompt = f"[{ev.producer}] {ev.title} — {ev.body}".strip(" —")
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
    from .message_processor import announce_to_channel, record_autonomous_turn

    mode = rule.compose or ALERT_COMPOSE_DEFAULT
    text = _format(ev)
    recorded = False  # browser session already written?

    if mode == "llm":
        try:
            composed = await _compose_via_llm(ev)
        except Exception as e:  # noqa: BLE001
            logger.warning("alert: LLM compose failed (%s) — template fallback", e)
            composed = None
        if composed:
            text = composed
            recorded = True  # process_inbound recorded the session itself

    if not recorded:
        try:
            record_autonomous_turn(
                ev.producer, ev.source_id or ev.producer,
                ev.title or ev.category, text,
            )
            recorded = True
        except Exception as e:  # noqa: BLE001
            logger.warning("alert: session record failed: %s", e)

    channel_ok = False
    for sink in rule.sinks:
        if await announce_to_channel(sink, "", text, media=ev.media):
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
