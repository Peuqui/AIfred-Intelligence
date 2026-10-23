"""EPIM FIELDSDATA codec: byte-length headers, strict merge.

The length header counts UTF-8 bytes (verified on the real database). A
merge must refuse every stored string it does not understand completely —
re-encoding a partially decoded record is how fields get lost.
"""

import pytest

from aifred.plugins.tools.epim import codec


def test_length_header_counts_bytes():
    # "Anästhesie" has 10 characters and 11 UTF-8 bytes.
    raw = codec.encode([(1, "Anästhesie")])
    assert raw[8:12] == "000B"
    assert codec.decode(raw).items == [(1, "Anästhesie")]


def test_multibyte_value_does_not_shift_following_fields():
    raw = codec.encode([(1, "Jörg"), (2, "Meier"), (3, "0561")])
    decoded = codec.decode(raw)
    assert decoded.items == [(1, "Jörg"), (2, "Meier"), (3, "0561")]
    assert decoded.error is None


def test_character_length_record_is_reported_incomplete():
    # Written by AIfred's former codec: "Hauptstraße 111" is 15 characters
    # but 16 bytes, so everything behind it is misaligned.
    raw = "000000010006Muster00000006000FHauptstraße 111000000070009Musterort"
    decoded = codec.decode(raw)
    assert decoded.items[0] == (1, "Muster")
    assert decoded.error is not None


def test_merge_refuses_incomplete_record():
    raw = "000000010006Muster00000006000FHauptstraße 111000000070009Musterort"
    with pytest.raises(codec.MalformedFieldData):
        codec.merge(raw, {3: "0561-999"})


def test_merge_refuses_record_that_does_not_reencode_identically():
    # Lower-case hex decodes, but would come back upper-case.
    raw = "00000001000aabcdefghij"
    with pytest.raises(codec.MalformedFieldData):
        codec.merge(raw, {})


def test_merge_refuses_duplicate_field_ids():
    raw = codec.encode([(1, "A"), (1, "B")])
    with pytest.raises(codec.MalformedFieldData):
        codec.merge(raw, {2: "x"})


def test_merge_keeps_unknown_fields_and_order():
    raw = codec.encode([(1, "Stefan"), (99, "Bürozeiten"), (3, "0561")])
    merged = codec.merge(raw, {3: "0172", 5: "a@b.de"})
    assert codec.decode(merged).items == [(1, "Stefan"), (99, "Bürozeiten"), (3, "0172"), (5, "a@b.de")]


def test_merge_none_removes_a_field():
    raw = codec.encode([(1, "Stefan"), (3, "0561")])
    assert codec.decode(codec.merge(raw, {3: None})).items == [(1, "Stefan")]


def test_encode_refuses_value_longer_than_header_allows():
    with pytest.raises(ValueError):
        codec.encode([(1, "x" * 0x10000)])
