"""Tests für aifred.lib.toast — die Toast-SSOT der Oberfläche."""

from __future__ import annotations

import pytest

from aifred.lib.toast import TOAST_KINDS, toast


@pytest.mark.parametrize("kind", TOAST_KINDS)
def test_every_kind_builds_an_event(kind):
    assert toast(kind, "Hallo") is not None


def test_unknown_kind_is_an_error():
    with pytest.raises(ValueError):
        toast("fatal", "x")
