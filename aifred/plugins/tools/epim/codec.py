"""EPIM FIELDSDATA codec (contacts, groups, password entries).

EPIM stores all fields of a record concatenated in ONE column:
``[field_id: 8 hex][length: 4 hex][value]…`` — e.g. ``000000010006Stefan``.

``length`` counts the UTF-8 *bytes* of the value, not its characters
(verified on the real database: byte lengths reproduce every EPIM-written
contact; values with umlauts have more bytes than characters). The header is
pure ASCII, so the codec works on the UTF-8 byte stream.

Writes go through :func:`merge` only. It refuses a record whose stored data
does not decode completely or does not re-encode to the identical string —
re-encoding a partially understood record is how fields get lost.
"""

from dataclasses import dataclass

_HEADER_LEN = 12  # 8 hex digits field id + 4 hex digits byte length
_MAX_VALUE_BYTES = 0xFFFF  # the length header has four hex digits


class MalformedFieldData(ValueError):
    """The stored FIELDSDATA does not follow EPIM's format."""


@dataclass(frozen=True)
class DecodedFields:
    items: list[tuple[int, str]]
    # None when the whole string decoded; otherwise where and why it stopped.
    error: str | None


def decode(raw: str) -> DecodedFields:
    """Decode ``raw`` as far as it is well-formed (for reading).

    The error names the byte offset where decoding stopped, so a reader can
    say that the record is incomplete instead of presenting a truncated
    record as complete."""
    data = raw.encode("utf-8")
    items: list[tuple[int, str]] = []
    pos = 0
    while pos < len(data):
        header = data[pos:pos + _HEADER_LEN]
        if len(header) < _HEADER_LEN:
            return DecodedFields(items, f"truncated field header at byte {pos}")
        try:
            field_id = int(header[:8].decode("ascii"), 16)
            length = int(header[8:].decode("ascii"), 16)
        except (UnicodeDecodeError, ValueError):
            return DecodedFields(items, f"invalid field header at byte {pos}")
        start = pos + _HEADER_LEN
        end = start + length
        if end > len(data):
            return DecodedFields(items, f"field {field_id} exceeds the data at byte {pos}")
        try:
            value = data[start:end].decode("utf-8")
        except UnicodeDecodeError:
            return DecodedFields(items, f"field {field_id} splits a character at byte {pos}")
        items.append((field_id, value))
        pos = end
    return DecodedFields(items, None)


def encode(items: list[tuple[int, str]]) -> str:
    parts: list[str] = []
    for field_id, value in items:
        byte_len = len(value.encode("utf-8"))
        if byte_len > _MAX_VALUE_BYTES:
            raise ValueError(f"field {field_id} too long ({byte_len} bytes, max {_MAX_VALUE_BYTES})")
        parts.append(f"{field_id:08X}{byte_len:04X}{value}")
    return "".join(parts)


def merge(existing_raw: str, updates: dict[int, str | None]) -> str:
    """Read-modify-write: keep every stored field, apply ``updates``.

    ``None`` removes a field. New fields are appended in the given order.
    Raises :class:`MalformedFieldData` unless the stored string decodes
    completely and re-encodes byte-identically."""
    decoded = decode(existing_raw)
    if decoded.error is not None:
        raise MalformedFieldData(decoded.error)
    if encode(decoded.items) != existing_raw:
        raise MalformedFieldData("stored field data does not re-encode identically")
    ids = [field_id for field_id, _ in decoded.items]
    if len(set(ids)) != len(ids):
        raise MalformedFieldData("stored field data contains a field id twice")
    merged = dict(decoded.items)
    for field_id, value in updates.items():
        if value is None:
            merged.pop(field_id, None)
        else:
            merged[field_id] = value
    return encode(list(merged.items()))
