"""Tests for the generic proactive alert pipeline core (alert_bus):
matching, throttle/dedup, quiet hours, rule loading. Delivery is injected as
a recording fake — the real SSoT delivery is covered separately."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from aifred.lib.alert_bus import AlertDispatcher, AlertEvent, AlertRule
from aifred.lib.read_mark import BubbleRef

_T0 = datetime(2026, 6, 3, 14, 0, 0)


def _ev(**kw: Any) -> AlertEvent:
    base: dict[str, Any] = {
        "producer": "vision", "category": "face_unknown", "source_id": "cam/office",
        "severity": "warning", "title": "Unbekannt", "body": "cam/office", "timestamp": _T0,
    }
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
    """One alert per cluster (dedup_key): the SAME key never re-alerts while
    remembered; a NEW key alerts immediately, regardless of timing. Keyless
    events are never throttled."""

    def _disp(self, calls_deliver):
        calls, deliver = calls_deliver
        return calls, AlertDispatcher(
            [AlertRule(producer="vision", sinks=["telegram"])],
            deliver=deliver,
        )

    def test_same_cluster_alerts_once(self):
        calls, d = self._disp(_recorder())
        assert asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0))) == 1
        # Repeat of the same cluster, even much later → still suppressed.
        assert asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0 + timedelta(seconds=60)))) == 0
        assert asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0 + timedelta(seconds=600)))) == 0
        assert len(calls) == 1

    def test_different_cluster_not_throttled(self):
        calls, d = self._disp(_recorder())
        asyncio.run(d.emit(_ev(dedup_key="c1", timestamp=_T0)))
        # A new cluster fires at once — even seconds later (no wall-clock gate).
        assert asyncio.run(d.emit(_ev(dedup_key="c2", timestamp=_T0 + timedelta(seconds=10)))) == 1
        assert len(calls) == 2

    def test_cluster_refires_after_retention(self):
        calls, d = self._disp(_recorder())
        asyncio.run(d.emit(_ev(dedup_key="c", timestamp=_T0)))
        # Beyond the dedup retention (1800s) the key is pruned and may fire
        # again — bounds memory; a real cluster_id never actually recurs.
        assert asyncio.run(d.emit(_ev(dedup_key="c", timestamp=_T0 + timedelta(seconds=2000)))) == 1
        assert len(calls) == 2

    def test_keyless_never_throttled(self):
        # No dedup_key → no dedup axis, every event fires.
        calls, d = self._disp(_recorder())
        assert asyncio.run(d.emit(_ev(dedup_key="", timestamp=_T0))) == 1
        assert asyncio.run(d.emit(_ev(dedup_key="", timestamp=_T0 + timedelta(seconds=1)))) == 1
        assert len(calls) == 2


class TestQuietHours:
    def test_event_in_quiet_window_suppressed(self):
        _calls, deliver = _recorder()
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


class TestSinkMedia:
    """Welche Medien an die Kanäle (Telegram) gehen: Bild samt Kontext, bei Burst-Meldungen nur die Filme."""

    def _deliver_once(self, monkeypatch, ev: AlertEvent, compose: str = "") -> dict[str, Any]:
        import aifred.lib.message_processor as mp
        from aifred.lib.alert_bus import _default_deliver
        from aifred.lib.routing_table import routing_table

        sent: dict[str, Any] = {}

        async def fake_announce(channel, recipient, text, *, session_id=None, bubble=None, media=None, metadata=None):
            sent.update(channel=channel, media=media, metadata=metadata or {})
            return True

        monkeypatch.setattr(mp, "announce_to_channel", fake_announce)
        monkeypatch.setattr(mp, "resolve_announce_targets", lambda channel, target: ["1"])
        monkeypatch.setattr(mp, "record_autonomous_turn", lambda *a, **k: BubbleRef("sess", "t1"))
        monkeypatch.setattr(mp, "session_for_channel", lambda *a, **k: "sess")
        monkeypatch.setattr(routing_table, "get_route", lambda *a, **k: None)
        rule = AlertRule(producer="vision", sinks=["telegram:1"], rule_id="r", compose=compose)
        asyncio.run(_default_deliver(ev, rule))
        return sent

    def _bubble_announced(self, monkeypatch, compose: str, reply_bubbles: list[str]) -> str | None:
        """The bubble the announcement carries to the channel (where a speaking device keeps its audio and marks it read)."""
        import aifred.lib.message_processor as mp
        from aifred.lib.alert_bus import _default_deliver
        from aifred.lib.envelope import OutboundMessage, ReplyPart
        from aifred.lib.routing_table import routing_table

        seen: dict[str, str | None] = {}

        async def fake_announce(channel, recipient, text, *, session_id=None, bubble=None, media=None, metadata=None):
            seen["bubble"] = bubble
            return True

        async def fake_process_inbound(message):
            parts = [ReplyPart("aifred", f"Antwort {n}", stamp) for n, stamp in enumerate(reply_bubbles)]
            return OutboundMessage(channel="vision", channel_id="x", recipient="x", text="Antworten", media=None,
                                   metadata={}, parts=parts)

        monkeypatch.setattr(mp, "announce_to_channel", fake_announce)
        monkeypatch.setattr(mp, "process_inbound", fake_process_inbound)
        monkeypatch.setattr(mp, "resolve_announce_targets", lambda channel, target: ["1"])
        monkeypatch.setattr(mp, "record_autonomous_turn", lambda *a, **k: BubbleRef("sess", "t-recorded"))
        monkeypatch.setattr(mp, "session_for_channel", lambda *a, **k: "sess")
        monkeypatch.setattr(routing_table, "get_route", lambda *a, **k: None)
        asyncio.run(_default_deliver(_ev(), AlertRule(producer="vision", sinks=["telegram:1"], rule_id="r", compose=compose)))
        return seen["bubble"]

    def test_the_announcement_carries_the_bubble_it_recorded(self, monkeypatch):
        assert self._bubble_announced(monkeypatch, "template", []) == "t-recorded"

    def test_an_answer_of_several_agents_carries_the_last_bubble_all_of_it_is_spoken_up_to_there(self, monkeypatch):
        assert self._bubble_announced(monkeypatch, "llm", ["t1", "t2"]) == "t2"

    def test_new_media_goes_to_the_channel_with_its_context(self, monkeypatch):
        ev = _ev(media="/x/zoom.jpg", media_context="/x/wide.jpg")
        sent = self._deliver_once(monkeypatch, ev)
        assert sent["media"] == "/x/zoom.jpg"
        assert sent["metadata"]["media_context"] == "/x/wide.jpg"

    def test_burst_films_travel_as_album(self, monkeypatch):
        """Die Filme der Burst-Zusammenfassung gehen als Album mit."""
        sent = self._deliver_once(
            monkeypatch, _ev(media_album=["/f/wide.mp4", "/f/zoom.mp4"]), compose="template",
        )
        assert sent["metadata"]["media_album"] == ["/f/wide.mp4", "/f/zoom.mp4"]

    def test_the_first_burst_report_reaches_telegram_without_album_or_image(self, monkeypatch):
        import aifred.lib.alert_bus as ab
        import aifred.lib.vision_alerts as va

        captured: list[AlertEvent] = []

        class _Capture:
            async def emit(self, ev):
                captured.append(ev)
                return 1

        monkeypatch.setattr(va, "_vigilantia_armed", lambda: True)
        monkeypatch.setattr(ab, "get_default_dispatcher", lambda: _Capture())
        report = va.BurstReport("cam/door", "c1", None)
        report.observe_face("face_known", "Anna", 0.9)
        asyncio.run(report.send())
        (first,) = captured
        sent = self._deliver_once(monkeypatch, first, compose="vlm")
        assert sent["media"] is None
        assert "media_album" not in sent["metadata"] and "media_context" not in sent["metadata"]

    def test_no_album_without_films(self, monkeypatch):
        sent = self._deliver_once(monkeypatch, _ev(media="/x/zoom.jpg"))
        assert "media_album" not in sent["metadata"]


class TestReadyDescription:
    """Eine fertige Beschreibung (Burst-Abschluss) wird nicht neu geholt; sie steht nur bei "vlm" im Text."""

    def _deliver(self, monkeypatch, compose: str, **event: Any) -> tuple[str, list[str]]:
        import aifred.lib.alert_bus as ab
        import aifred.lib.message_processor as mp

        asked: list[str] = []
        recorded: list[str] = []

        async def describe(ev):
            asked.append("vlm")
            return "geholt"

        async def announce(channel, recipient, text, **kw):
            return True

        monkeypatch.setattr(ab, "_describe_media_via_vlm", describe)
        monkeypatch.setattr(mp, "announce_to_channel", announce)

        def record(*a, **kw):
            recorded.append(a[3])
            return BubbleRef("sess", "t1")

        monkeypatch.setattr(mp, "record_autonomous_turn", record)
        monkeypatch.setattr(mp, "resolve_announce_targets", lambda channel, target: [target])
        rule = AlertRule(producer="vision", sinks=["telegram"], compose=compose, rule_id="r")
        assert asyncio.run(ab._deliver_in_session(_ev(**event), rule))
        return recorded[0], asked

    def test_a_rule_with_vlm_uses_it_without_asking_the_describer(self, monkeypatch):
        text, asked = self._deliver(monkeypatch, "vlm", description="Fertig beschrieben.", describe=False)
        assert "Fertig beschrieben." in text and asked == []

    def test_a_ready_description_wins_over_describe(self, monkeypatch):
        text, asked = self._deliver(monkeypatch, "vlm", description="Fertig beschrieben.", describe=True)
        assert "Fertig beschrieben." in text and "geholt" not in text and asked == []

    def test_a_rule_without_vlm_keeps_it_out_of_the_text(self, monkeypatch):
        text, asked = self._deliver(monkeypatch, "template", description="Fertig beschrieben.", describe=False)
        assert "Fertig beschrieben." not in text and asked == []

    def test_without_a_ready_description_the_describer_is_asked(self, monkeypatch):
        text, asked = self._deliver(monkeypatch, "vlm")
        assert "geholt" in text and asked == ["vlm"]


class TestFilmPlayers:
    def test_films_become_inline_players_for_the_browser_session(self):
        from aifred.lib.alert_bus import _film_players

        html = _film_players([
            "/x/c-1_170000_wide.mp4", "/x/c-1_170000_zoom.mp4", "/x/crop.jpg",
        ])
        assert html.count("<video controls") == 2
        assert "Weitwinkel" in html and "Zoom" in html
        assert "crop.jpg" not in html  # Fotos des Albums sind kein Film

    def test_no_players_without_films(self):
        from aifred.lib.alert_bus import _film_players

        assert _film_players(["/x/1.jpg"]) == ""


class TestDeliveryInTheAlertsSession:
    """What a delivery does (the describer, the wording) reaches the debug console of the alert's own session."""

    def test_the_delivery_runs_in_the_session_of_the_alert(self, monkeypatch):
        from aifred.lib import alert_bus
        import aifred.lib.message_processor as mp
        from aifred.lib.debug_bus import _current_session

        asked: list[tuple[str, str]] = []
        seen: list[str | None] = []

        def session_for_channel(channel: str, channel_id: str) -> str:
            asked.append((channel, channel_id))
            return "vigilantia-session"

        async def deliver_in_session(ev: AlertEvent, rule: AlertRule) -> bool:
            seen.append(_current_session.get())
            return True

        monkeypatch.setattr(mp, "session_for_channel", session_for_channel)
        monkeypatch.setattr(alert_bus, "_deliver_in_session", deliver_in_session)
        ev = _ev(session_key="Hauseingang")
        assert asyncio.run(alert_bus._default_deliver(ev, AlertRule(producer="vision", sinks=[], rule_id="r")))
        assert asked == [("vision", "Hauseingang")]      # the same key record_autonomous_turn writes under
        assert seen == ["vigilantia-session"]
        assert _current_session.get() is None            # the scope ends with the delivery
