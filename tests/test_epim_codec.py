"""FIELDSDATA codec + merge for the EPIM plugin.

The length prefix in EPIM's FIELDSDATA is the UTF-8 *byte* count, not the
character count — empirically verified against the real database (byte-length
reproduces 398/399 contacts exactly, char-length only 298/399). A char-based
codec corrupts and drops every field following a multibyte character. These
tests pin the byte-accurate behaviour and the lossless read-modify-write merge
(which must preserve fields whose id has no human-readable name).
"""

from __future__ import annotations

import pytest

from aifred.plugins.tools.epim.db import (
    _decode_fieldsdata_items,
    _encode_fieldsdata_items,
    decode_fieldsdata,
    encode_fieldsdata,
    merge_fieldsdata,
)

# Human-name → field id, and its inverse for decoding.
_NAME_TO_ID = {"Vorname": 1, "Nachname": 2, "Telefon": 3}
_ID_TO_NAME = {v: k for k, v in _NAME_TO_ID.items()}


def test_ascii_roundtrip():
    raw = _encode_fieldsdata_items([(1, "Stefan"), (2, "Meier")])
    assert _decode_fieldsdata_items(raw) == [(1, "Stefan"), (2, "Meier")]


def test_length_prefix_is_byte_count_not_char_count():
    # "Anästhesie" is 10 characters but 11 UTF-8 bytes (ä = 2 bytes).
    raw = _encode_fieldsdata_items([(1, "Anästhesie")])
    # header = 8 hex id + 4 hex length; length must be 0x000B (11), not 0x000A.
    assert raw[8:12] == "000B"
    assert _decode_fieldsdata_items(raw) == [(1, "Anästhesie")]


def test_multibyte_does_not_shift_following_fields():
    # A char-based decoder would misalign after the umlaut field and either
    # corrupt or drop "Meier". The byte-based codec must keep it intact.
    raw = _encode_fieldsdata_items([(1, "Jörg"), (2, "Meier"), (3, "0561")])
    assert _decode_fieldsdata_items(raw) == [(1, "Jörg"), (2, "Meier"), (3, "0561")]


def test_decode_maps_known_ids_and_keeps_unknown_as_field_id():
    raw = _encode_fieldsdata_items([(1, "Stefan"), (99, "Bürozeiten")])
    decoded = decode_fieldsdata(raw, _ID_TO_NAME)
    assert decoded == {"Vorname": "Stefan", "field_99": "Bürozeiten"}


def test_encode_accepts_field_id_form_for_custom_fields():
    # The decoder emits ``field_<id>`` for ids without a name; encode must be
    # able to write them back so a round-trip keeps custom fields.
    encoded = encode_fieldsdata({"Vorname": "Stefan", "field_99": "x"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(encoded) == [(1, "Stefan"), (99, "x")]


def test_encode_rejects_unmappable_names():
    # Fail-loud (Review 2026-08-12): ein still verworfener Feldname ließ das
    # Tool Erfolg melden, ohne dass der Wert je in der DB ankam.
    with pytest.raises(ValueError, match="Unbekannt"):
        encode_fieldsdata({"Vorname": "Stefan", "Unbekannt": "y"}, _NAME_TO_ID)


def test_merge_rejects_unmappable_names():
    existing = _encode_fieldsdata_items([(1, "Stefan")])
    with pytest.raises(ValueError, match="Unbekannt"):
        merge_fieldsdata(existing, {"Unbekannt": "y"}, _NAME_TO_ID)


def test_merge_preserves_other_fields():
    existing = _encode_fieldsdata_items([(1, "Stefan"), (2, "Meier"), (3, "0561")])
    merged = merge_fieldsdata(existing, {"Telefon": "9999"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(merged) == [(1, "Stefan"), (2, "Meier"), (3, "9999")]


def test_merge_preserves_custom_field_without_name():
    # This is the data-loss case: a naive name-level merge drops field_99.
    existing = _encode_fieldsdata_items([(1, "Stefan"), (99, "Bürozeiten")])
    merged = merge_fieldsdata(existing, {"Vorname": "Klaus"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(merged) == [(1, "Klaus"), (99, "Bürozeiten")]


def test_merge_updates_custom_field_via_field_id_form():
    existing = _encode_fieldsdata_items([(1, "Stefan"), (99, "alt")])
    merged = merge_fieldsdata(existing, {"field_99": "neu"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(merged) == [(1, "Stefan"), (99, "neu")]


def test_merge_appends_new_field():
    existing = _encode_fieldsdata_items([(1, "Stefan")])
    merged = merge_fieldsdata(existing, {"Telefon": "0561"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(merged) == [(1, "Stefan"), (3, "0561")]


def test_merge_into_empty():
    merged = merge_fieldsdata("", {"Vorname": "Stefan"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(merged) == [(1, "Stefan")]


def test_merge_umlaut_value_roundtrips():
    existing = _encode_fieldsdata_items([(1, "Stefan"), (2, "Meier")])
    merged = merge_fieldsdata(existing, {"Nachname": "Müller-Lüdenschöß"}, _NAME_TO_ID)
    assert _decode_fieldsdata_items(merged) == [(1, "Stefan"), (2, "Müller-Lüdenschöß")]


def test_encode_rejects_value_over_64k_bytes():
    with pytest.raises(ValueError):
        _encode_fieldsdata_items([(1, "x" * (0x10000))])


def test_decode_empty_is_empty():
    assert _decode_fieldsdata_items("") == []
    assert decode_fieldsdata("") == {}
