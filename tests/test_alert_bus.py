"""Tests for the generic proactive alert pipeline core (alert_bus):
matching, throttle/dedup, quiet hours, rule loading. Delivery is injected as
a recording fake — the real SSoT delivery is covered separately."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aifred.lib.alert_bus import AlertDispatcher, AlertEvent, AlertRule

_T0 = datetime(2026, 6, 3, 14, 0, 0)


def _ev(**kw) -> AlertEvent:
    base = dict(
        producer="vision", category="face_unknown", source_id="cam/office",
        severity="warning", title="Unbekannt", body="cam/office", timestamp=_T0,
    )
    base.update(kw)
    return AlertEvent(**base)


def _recorder():
    """A deliver function that records (ev, rule) calls and reports success."""
    calls: list[tuple] = []

    async def deliver(ev, rule):
        calls.append((ev, rule))
        return True

    return calls, deliver


class TestMatching:
    def test_producer_category_source_match(self):
        r = AlertRule(producer="vision", sinks=["telegram"], category="face_unknown",
                      source_id="cam/office")
        assert r.matches(_ev())
        assert not r.matches(_ev(producer="system"))
        assert not r.matches(_ev(category="motion"))
        assert not r.matches(_ev(source_id="cam/door"))

    def test_none_filters_match_any(self):
        r = AlertRule(producer="vision", sinks=["telegram"])  # category/source = any
        assert r.matches(_ev(category="motion", source_id="cam/anything"))

    def test_min_severity(self):
        r = AlertRule(producer="vision", sinks=["telegram"], min_severity="warning")
        assert r.matches(_ev(severity="warning"))
        assert r.matches(_ev(severity="critical"))
        assert not r.matches(_ev(severity="info"))


class TestDelivery:
    def test_matching_event_delivered(self):
        calls, deliver = _recorder()
        d = AlertDispatcher([AlertRule(producer="vision", sinks=["telegram"])], deliver=deliver)
        assert asyncio.run(d.emit(_ev())) == 1
        assert len(calls) == 1 and calls[0][1].sinks == ["telegram"]

    def test_non_matching_not_delivered(self):
        calls, deliver = _recorder()
        d = AlertDispatcher(
            [AlertRule(producer="vision", sinks=["telegram"], category="motion")],
            deliver=deliver,
        )
        assert asyncio.run(d.emit(_ev(category="face_unknown"))) == 0
        assert calls == []

    def test_multiple_matching_rules_each_deliver(self):
        calls, deliver = _recorder()
        rules = [
            AlertRule(producer="vision", sinks=["telegram"]),
            AlertRule(producer="vision", sinks=["email"]),
        ]
        assert asyncio.run(AlertDispatcher(rules, deliver=deliver).emit(_ev())) == 2
        assert len(calls) == 2

    def test_deliver_false_not_counted(self):
        async def deliver(ev, rule):
            return False
        d = AlertDispatcher([AlertRule(producer="vision", sinks=["telegram"])], deliver=deliver)
        assert asyncio.run(d.emit(_ev())) == 0

    def test_deliver_exception_swallowed(self):
        async def deliver(ev, rule):
            raise RuntimeError("boom")
        d = AlertDispatcher([AlertRule(producer="vision", sinks=["telegram"])], deliver=deliver)
        assert asyncio.run(d.emit(_ev())) == 0  # no crash, not counted


class TestThrottle:
    def _disp(self, calls_deliver):
        calls, deliver = calls_deliver
        return calls, AlertDispatcher(
            [AlertRule(producer="vision", sinks=["telegram"], min_interval_sec=300)],
            deliver=deliver,
        )

    def test_same_dedup_key_within_interval_suppressed(self):
        calls, d = self._disp(_recorder())
        assert asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0))) == 1
        assert asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0 + timedelta(seconds=60)))) == 0
        assert len(calls) == 1

    def test_different_dedup_key_not_throttled(self):
        calls, d = self._disp(_recorder())
        asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0)))
        assert asyncio.run(d.emit(_ev(dedup_key="c2", timestamp=_T0 + timedelta(seconds=10)))) == 1
        assert len(calls) == 2

    def test_interval_elapsed_allows_again(self):
        calls, d = self._disp(_recorder())
        asyncio.run(d.emit(_ev(dedup_key="c", timestamp=_T0)))
        assert asyncio.run(d.emit(_ev(dedup_key="c", timestamp=_T0 + timedelta(seconds=301)))) == 1
        assert len(calls) == 2


class TestQuietHours:
    def test_event_in_quiet_window_suppressed(self):
        calls, deliver = _recorder()
        d = AlertDispatcher(
            [AlertRule(producer="vision", sinks=["telegram"], quiet_hours=(22, 7))],
            deliver=deliver,
        )
        # 02:00 is inside 22→07 (wraps midnight) → suppressed
        assert asyncio.run(d.emit(_ev(timestamp=_T0.replace(hour=2)))) == 0
        # 14:00 is outside → delivered
        assert asyncio.run(d.emit(_ev(timestamp=_T0))) == 1


class TestLoadRules:
    def _write(self, monkeypatch, tmp_path, content: str):
        import aifred.lib.alert_bus as ab
        p = tmp_path / "alert_rules.json"
        p.write_text(content, encoding="utf-8")
        monkeypatch.setattr(ab, "_rules_path", lambda: p)

    def test_missing_file_no_rules(self, monkeypatch, tmp_path):
        import aifred.lib.alert_bus as ab
        monkeypatch.setattr(ab, "_rules_path", lambda: tmp_path / "nope.json")
        assert ab.load_rules() == []

    def test_parses_rules_and_quiet_hours_tuple(self, monkeypatch, tmp_path):
        import aifred.lib.alert_bus as ab
        self._write(monkeypatch, tmp_path,
                    '[{"producer":"vision","category":"face_unknown",'
                    '"sinks":["telegram"],"min_interval_sec":300,'
                    '"quiet_hours":[22,7],"rule_id":"r1","compose":"llm","ignored":"x"}]')
        rules = ab.load_rules()
        assert len(rules) == 1
        r = rules[0]
        assert r.producer == "vision" and r.sinks == ["telegram"]
        assert r.quiet_hours == (22, 7)  # list → tuple
        assert r.rule_id == "r1" and r.compose == "llm"

    def test_invalid_entries_skipped(self, monkeypatch, tmp_path):
        import aifred.lib.alert_bus as ab
        self._write(monkeypatch, tmp_path,
                    '[{"producer":"vision","sinks":["telegram"]}, {"no":"producer"}, 42]')
        assert len(ab.load_rules()) == 1

    def test_bad_json_no_rules(self, monkeypatch, tmp_path):
        import aifred.lib.alert_bus as ab
        self._write(monkeypatch, tmp_path, "{ not json")
        assert ab.load_rules() == []
