"""Which session opens after the open one was deleted (17.09.2026).

The picker lists sessions newest first; the nearest remaining one below the
deleted open session takes over, else the nearest above, else a new session.
"""

from aifred.state._session_mixin import _successor_session


def test_next_below_takes_over() -> None:
    assert _successor_session(["a", "b", "c"], "b", {"b"}) == "c"


def test_last_in_list_falls_back_to_the_one_above() -> None:
    assert _successor_session(["a", "b", "c"], "c", {"c"}) == "b"


def test_bulk_delete_skips_deleted_neighbours() -> None:
    order = ["a", "b", "c", "d", "e"]
    assert _successor_session(order, "b", {"b", "c", "d"}) == "e"
    assert _successor_session(order, "d", {"d", "e", "c"}) == "b"


def test_nothing_left_means_new_session() -> None:
    assert _successor_session(["a", "b"], "a", {"a", "b"}) is None
    assert _successor_session([], "a", {"a"}) is None


def test_open_session_missing_from_picker_takes_first_remaining() -> None:
    assert _successor_session(["a", "b"], "x", {"x", "a"}) == "b"
