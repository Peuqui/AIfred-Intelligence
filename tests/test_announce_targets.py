"""Tests for sink-target resolution (groups / broadcast / single room).

resolve_announce_targets expands a sink target into concrete recipients —
freeecho2 supports "*" (all connected rooms) and "@group" (configured rooms);
every other channel resolves to a single recipient. Broadcast/group knowledge
stays server-side so the firmware stays room-only."""

from __future__ import annotations

import aifred.lib.message_processor as mp


def _set_devices(monkeypatch, rooms):
    import aifred.plugins.channels.freeecho2_channel as fe
    monkeypatch.setattr(fe, "_devices", {r: object() for r in rooms})


class TestFreeEcho2Targets:
    def test_broadcast_all_connected_rooms(self, monkeypatch):
        _set_devices(monkeypatch, ["wohnzimmer", "kueche"])
        out = mp.resolve_announce_targets("freeecho2", "*")
        assert set(out) == {"wohnzimmer", "kueche"}

    def test_broadcast_empty_when_none_connected(self, monkeypatch):
        _set_devices(monkeypatch, [])
        assert mp.resolve_announce_targets("freeecho2", "*") == []

    def test_group_resolves_to_configured_rooms(self, monkeypatch):
        monkeypatch.setattr(
            mp, "_freeecho2_groups",
            lambda: {"erdgeschoss": ["wohnzimmer", "kueche"]},
        )
        out = mp.resolve_announce_targets("freeecho2", "@erdgeschoss")
        assert out == ["wohnzimmer", "kueche"]

    def test_unknown_group_is_empty(self, monkeypatch):
        monkeypatch.setattr(mp, "_freeecho2_groups", lambda: {})
        assert mp.resolve_announce_targets("freeecho2", "@nope") == []

    def test_explicit_room_passes_through(self, monkeypatch):
        monkeypatch.setattr(mp, "_resolve_channel_recipient",
                            lambda ch, r: r)
        assert mp.resolve_announce_targets("freeecho2", "wohnzimmer") == ["wohnzimmer"]


class TestOtherChannelTargets:
    def test_single_resolved_recipient(self, monkeypatch):
        monkeypatch.setattr(mp, "_resolve_channel_recipient",
                            lambda ch, r: "chat-42")
        assert mp.resolve_announce_targets("telegram", "") == ["chat-42"]

    def test_empty_when_unresolvable(self, monkeypatch):
        monkeypatch.setattr(mp, "_resolve_channel_recipient", lambda ch, r: "")
        assert mp.resolve_announce_targets("telegram", "") == []


class TestGroupsLoader:
    def test_missing_file_no_groups(self, monkeypatch, tmp_path):
        import aifred.lib.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
        assert mp._freeecho2_groups() == {}

    def test_parses_groups_and_skips_non_lists(self, monkeypatch, tmp_path):
        import aifred.lib.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
        (tmp_path / "freeecho2_groups.json").write_text(
            '{"eg": ["wohnzimmer", "kueche"], "bad": "not-a-list"}',
            encoding="utf-8",
        )
        groups = mp._freeecho2_groups()
        assert groups == {"eg": ["wohnzimmer", "kueche"]}
