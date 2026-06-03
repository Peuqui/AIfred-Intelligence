"""Generic proactive alert pipeline — producer- and channel-agnostic core.

Producers (vision, system, scheduler, …) emit a neutral :class:`AlertEvent`.
The :class:`AlertDispatcher` matches it against central :class:`AlertRule`s,
throttles (dedup by key + per-rule cooldown + quiet hours), and delivers to
the rule's sinks.

Sinks are NOT a separate registry — the single source of truth is
``plugin_registry``: each sink name resolves to a channel plugin via
``plugin_registry.get_channel(name)`` and the dispatcher calls its
``send_proactive(...)``. Producers and channels stay plugins; this core only
orchestrates. See docs/de/architecture/proactive-alerts.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

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


# name -> channel-like object exposing async send_proactive(text=, media=).
ChannelResolver = Callable[[str], Any]


def _default_resolver(name: str) -> Any:
    from .plugin_registry import get_channel
    return get_channel(name)


class AlertDispatcher:
    """Matches AlertEvents against rules, throttles, delivers to sinks.

    ``channel_resolver`` defaults to the real ``plugin_registry`` (SSoT); tests
    inject a fake. State is the last-sent time per ``(rule, dedup_key)``, which
    drives the cooldown and is pruned to stay bounded.
    """

    def __init__(
        self,
        rules: list[AlertRule],
        *,
        channel_resolver: ChannelResolver | None = None,
    ) -> None:
        self.rules = list(rules)
        self._resolve = channel_resolver or _default_resolver
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
        """Route one event. Returns how many sink deliveries succeeded."""
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
            sent_any = False
            for sink_name in rule.sinks:
                channel = self._resolve(sink_name)
                if channel is None:
                    logger.warning("alert: sink '%s' unknown (no channel)", sink_name)
                    continue
                try:
                    ok = await channel.send_proactive(text=_format(ev), media=ev.media)
                except NotImplementedError:
                    logger.warning("alert: channel '%s' has no proactive send", sink_name)
                    continue
                except Exception as e:  # noqa: BLE001
                    logger.warning("alert: send via '%s' failed: %s", sink_name, e)
                    continue
                if ok:
                    sent_any = True
                    delivered += 1
            if sent_any:
                self._last_sent[self._rule_key(rule, ev)] = now
        return delivered


def _format(ev: AlertEvent) -> str:
    """Default text rendering — title + body. (AIfred-composed phrasing is a
    later, optional layer on top.)"""
    if ev.title and ev.body:
        return f"{ev.title}\n{ev.body}"
    return ev.title or ev.body
