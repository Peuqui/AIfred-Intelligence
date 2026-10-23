"""vCard contacts for the Nextcloud plugin.

A contact is read into a flat form (name, phones and e-mails by type,
addresses, organization, …). Updates change only the named entries and keep
every other property of the card, including ones this plugin does not know.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import vobject

CONTENT_TYPE = "text/vcard; charset=utf-8"
# Kinds the plugin reads and writes, with the vCard TYPE they map to.
PHONE_KINDS = {"mobile": "CELL", "home": "HOME", "work": "WORK"}
EMAIL_KINDS = {"home": "HOME", "work": "WORK"}
ADDRESS_KINDS = {"home": "HOME", "work": "WORK"}
ADDRESS_PARTS = ("street", "city", "postal_code", "region", "country")


def _kind(entry: Any, kinds: dict[str, str]) -> str:
    types = {t.upper() for t in entry.params.get("TYPE", [])}
    for kind, vcard_type in kinds.items():
        if vcard_type in types:
            return kind
    return "other"


def _address_dict(adr: Any) -> dict[str, str]:
    value = adr.value
    parts = {"street": value.street, "city": value.city, "postal_code": value.code,
             "region": value.region, "country": value.country}
    return {k: v for k, v in parts.items() if v}


def _birthday(card: Any) -> str | None:
    """BDAY as YYYY-MM-DD; vCard also allows the basic form YYYYMMDD and,
    without a year, --MMDD (returned as written)."""
    if "bday" not in card.contents:
        return None
    raw = str(card.bday.value)
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw[:10]


@dataclass(frozen=True)
class Contact:
    uid: str
    fields: dict[str, Any]


def parse_contact(data: str) -> Contact:
    card = vobject.readOne(data)
    first = last = None
    if "n" in card.contents:
        first, last = card.n.value.given or None, card.n.value.family or None
    fields: dict[str, Any] = {
        "name": card.fn.value if "fn" in card.contents else None,
        "first_name": first,
        "last_name": last,
        "organization": " ".join(card.org.value) if "org" in card.contents and card.org.value else None,
        "title": card.title.value if "title" in card.contents else None,
        "birthday": _birthday(card),
        "note": card.note.value if "note" in card.contents else None,
        "phones": {},
        "emails": {},
        "addresses": {},
    }
    for tel in card.contents.get("tel", []):
        fields["phones"].setdefault(_kind(tel, PHONE_KINDS), tel.value)
    for mail in card.contents.get("email", []):
        fields["emails"].setdefault(_kind(mail, EMAIL_KINDS), mail.value)
    for adr in card.contents.get("adr", []):
        fields["addresses"].setdefault(_kind(adr, ADDRESS_KINDS), _address_dict(adr))
    return Contact(uid=card.uid.value if "uid" in card.contents else "", fields=fields)


def _display_name(card: Any) -> str:
    given = card.n.value.given if "n" in card.contents else ""
    family = card.n.value.family if "n" in card.contents else ""
    return " ".join(p for p in (given, family) if p)


def _remove(card: Any, name: str) -> None:
    for entry in list(card.contents.get(name, [])):
        card.remove(entry)


def _set_single(card: Any, name: str, value: Any) -> None:
    _remove(card, name)
    if value is not None:
        card.add(name).value = value


def _set_typed(card: Any, name: str, kinds: dict[str, str], kind: str, value: Any) -> None:
    """Replace the first entry of ``kind`` (or add one); ``None`` removes it."""
    vcard_type = kinds[kind]
    existing = [e for e in card.contents.get(name, []) if _kind(e, kinds) == kind]
    if value is None:
        for entry in existing[:1]:
            card.remove(entry)
        return
    entry = existing[0] if existing else card.add(name)
    entry.value = value
    if not existing:
        entry.params["TYPE"] = [vcard_type]


def _address(parts: dict[str, str]) -> Any:
    unknown = set(parts) - set(ADDRESS_PARTS)
    if unknown:
        raise ValueError(f"unknown address part(s) {', '.join(sorted(unknown))}; use {', '.join(ADDRESS_PARTS)}")
    return vobject.vcard.Address(street=parts.get("street", ""), city=parts.get("city", ""),
                                 code=parts.get("postal_code", ""), region=parts.get("region", ""),
                                 country=parts.get("country", ""))


def apply_changes(card: Any, changes: dict[str, Any]) -> None:
    """``changes`` keys: name, first_name, last_name, organization, title,
    birthday (date), note, phones/emails/addresses ({kind: value or None})."""
    if "first_name" in changes or "last_name" in changes:
        n = card.n.value if "n" in card.contents else vobject.vcard.Name()
        given = changes.get("first_name", n.given) or ""
        family = changes.get("last_name", n.family) or ""
        _set_single(card, "n", vobject.vcard.Name(family=family, given=given))
        if "name" not in changes:
            _set_single(card, "fn", _display_name(card) or (card.fn.value if "fn" in card.contents else ""))
    if "name" in changes:
        _set_single(card, "fn", changes["name"])
    if "organization" in changes:
        _set_single(card, "org", [changes["organization"]] if changes["organization"] else None)
    for key in ("title", "note"):
        if key in changes:
            _set_single(card, key, changes[key])
    if "birthday" in changes:
        bday: date | None = changes["birthday"]
        _set_single(card, "bday", bday.isoformat() if bday else None)
    for key, name, kinds in (("phones", "tel", PHONE_KINDS), ("emails", "email", EMAIL_KINDS)):
        for kind, value in changes.get(key, {}).items():
            _set_typed(card, name, kinds, kind, value)
    for kind, parts in changes.get("addresses", {}).items():
        _set_typed(card, "adr", ADDRESS_KINDS, kind, _address(parts) if parts is not None else None)


def build_contact(uid: str, changes: dict[str, Any]) -> str:
    card = vobject.vCard()
    card.add("uid").value = uid
    apply_changes(card, changes)
    if "fn" not in card.contents or not card.fn.value:
        raise ValueError("a contact needs a name or a first/last name")
    if "n" not in card.contents:
        # vCard 3.0 requires N; a name given only as display name becomes the family name.
        _set_single(card, "n", vobject.vcard.Name(family=card.fn.value))
    result: str = card.serialize()
    return result


def update_contact(data: str, changes: dict[str, Any]) -> str:
    card = vobject.readOne(data)
    apply_changes(card, changes)
    result: str = card.serialize()
    return result
