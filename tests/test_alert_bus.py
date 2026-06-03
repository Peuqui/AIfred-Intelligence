"""Tests for the generic proactive alert pipeline core (alert_bus)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aifred.lib.alert_bus import AlertDispatcher, AlertEvent, AlertRule

_T0 = datetime(2026, 6, 3, 14, 0, 0)


class FakeChannel:
    """Records proactive sends; mimics a channel plugin's send_proactive."""

    def __init__(self, *, supported: bool = True, ok: bool = True) -> None:
        self.supported = supported
        self.ok = ok
        self.sends: list[tuple[str, str | None]] = []

    async def send_proactive(self, *, text: str, media: str | None = None) -> bool:
        if not self.supported:
            raise NotImplementedError("no proactive")
        self.sends.append((text, media))
        return self.ok


def _ev(**kw) -> AlertEvent:
    base = dict(
        producer="vision", category="face_unknown", source_id="cam/office",
        severity="warning", title="Unbekannt", body="cam/office", timestamp=_T0,
    )
    base.update(kw)
    return AlertEvent(**base)


def _dispatch(rules, channels):
    return AlertDispatcher(rules, channel_resolver=lambda n: channels.get(n))


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
    def test_matching_event_delivered_to_sink(self):
        tg = FakeChannel()
        d = _dispatch([AlertRule(producer="vision", sinks=["telegram"])], {"telegram": tg})
        n = asyncio.run(d.emit(_ev(media="/x/a.jpg")))
        assert n == 1
        assert tg.sends == [("Unbekannt\ncam/office", "/x/a.jpg")]

    def test_non_matching_event_not_delivered(self):
        tg = FakeChannel()
        d = _dispatch([AlertRule(producer="vision", sinks=["telegram"], category="motion")],
                      {"telegram": tg})
        assert asyncio.run(d.emit(_ev(category="face_unknown"))) == 0
        assert tg.sends == []

    def test_fans_out_to_multiple_sinks(self):
        tg, mail = FakeChannel(), FakeChannel()
        d = _dispatch([AlertRule(producer="vision", sinks=["telegram", "email"])],
                      {"telegram": tg, "email": mail})
        assert asyncio.run(d.emit(_ev())) == 2
        assert len(tg.sends) == 1 and len(mail.sends) == 1

    def test_unknown_sink_skipped_no_crash(self):
        d = _dispatch([AlertRule(producer="vision", sinks=["ghost"])], {})
        assert asyncio.run(d.emit(_ev())) == 0

    def test_channel_without_proactive_skipped(self):
        tg = FakeChannel(supported=False)
        d = _dispatch([AlertRule(producer="vision", sinks=["telegram"])], {"telegram": tg})
        assert asyncio.run(d.emit(_ev())) == 0


class TestThrottle:
    def test_same_dedup_key_within_interval_suppressed(self):
        tg = FakeChannel()
        d = _dispatch(
            [AlertRule(producer="vision", sinks=["telegram"], min_interval_sec=300)],
            {"telegram": tg},
        )
        assert asyncio.run(d.emit(_ev(dedup_key="cluster-1", timestamp=_T0))) == 1
        # 60s later, same cluster → throttled
        assert asyncio.run(d.emit(_ev(dedup_key="cluster-1", timestamp=_T0 + timedelta(seconds=60)))) == 0
        assert len(tg.sends) == 1

    def test_different_dedup_key_not_throttled(self):
        tg = FakeChannel()
        d = _dispatch(
            [AlertRule(producer="vision", sinks=["telegram"], min_interval_sec=300)],
            {"telegram": tg},
        )
        asyncio.run(d.emit(_ev(dedup_key="cluster-1", timestamp=_T0)))
        # different happening → its own alert
        assert asyncio.run(d.emit(_ev(dedup_key="cluster-2", timestamp=_T0 + timedelta(seconds=10)))) == 1
        assert len(tg.sends) == 2

    def test_interval_elapsed_allows_again(self):
        tg = FakeChannel()
        d = _dispatch(
            [AlertRule(producer="vision", sinks=["telegram"], min_interval_sec=300)],
            {"telegram": tg},
        )
        asyncio.run(d.emit(_ev(dedup_key="c", timestamp=_T0)))
        assert asyncio.run(d.emit(_ev(dedup_key="c", timestamp=_T0 + timedelta(seconds=301)))) == 1
        assert len(tg.sends) == 2


class TestQuietHours:
    def test_event_in_quiet_window_suppressed(self):
        tg = FakeChannel()
        d = _dispatch(
            [AlertRule(producer="vision", sinks=["telegram"], quiet_hours=(22, 7))],
            {"telegram": tg},
        )
        # 02:00 is inside 22→07 (wraps midnight) → suppressed
        night = _T0.replace(hour=2)
        assert asyncio.run(d.emit(_ev(timestamp=night))) == 0
        # 14:00 is outside → delivered
        assert asyncio.run(d.emit(_ev(timestamp=_T0))) == 1
