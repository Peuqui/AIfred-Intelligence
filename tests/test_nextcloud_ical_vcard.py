"""iCalendar and vCard handling of the Nextcloud plugin (no server needed)."""

# Local wall-clock time is the plugin's input and output form.

from datetime import date, datetime

import pytest

from aifred.plugins.tools.nextcloud import ical, vcard


def test_timed_event_roundtrip_in_local_time():
    data = ical.build_event("u", "Zahnarzt; Kontrolle", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11),
                            False, "Kassel", None)
    event = ical.parse_events(data)[0].as_dict()
    assert (event["title"], event["start"], event["end"]) == ("Zahnarzt; Kontrolle", datetime(2026, 11, 3, 10),
                                                              datetime(2026, 11, 3, 11))
    assert "SUMMARY:Zahnarzt\\; Kontrolle" in data  # escaped on the wire


def test_all_day_end_is_reported_as_the_last_day():
    data = ical.build_event("u", "Urlaub", date(2026, 11, 5), date(2026, 11, 8), True, None, None)
    event = ical.parse_events(data)[0].as_dict()
    assert (event["start"], event["end"], event["all_day"]) == (date(2026, 11, 5), date(2026, 11, 7), True)


def test_update_keeps_alarms_and_bumps_sequence():
    data = ical.build_event("u", "Termin", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11), False, "Ort", None)
    data = data.replace("END:VEVENT", "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:x\r\n"
                                      "END:VALARM\r\nEND:VEVENT")
    updated = ical.update_event(data, title="Neu", clear=frozenset({"location"}))
    event = ical.parse_events(updated)[0].as_dict()
    assert event["title"] == "Neu" and event["location"] is None
    assert "BEGIN:VALARM" in updated and "SEQUENCE:1" in updated


def test_recurring_events_are_refused_for_updates():
    data = ical.build_event("u", "Serie", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11), False, None, None)
    with pytest.raises(ical.RecurringRefused):
        ical.update_event(data.replace("END:VEVENT", "RRULE:FREQ=WEEKLY\r\nEND:VEVENT"), title="x")


def test_task_done_and_priority():
    data = ical.build_task("t", "Steuer", date(2026, 11, 7), "high", None)
    assert ical.parse_tasks(data)[0].as_dict()["priority"] == "high"
    done = ical.parse_tasks(ical.update_task(data, done=True))[0]
    assert done.done
    assert not ical.parse_tasks(ical.update_task(ical.update_task(data, done=True), done=False))[0].done


def test_contact_build_and_update_keep_unknown_properties():
    data = vcard.build_contact("c", {"first_name": "Jörg", "last_name": "Meier", "phones": {"mobile": "0172 1"},
                                     "birthday": date(1969, 7, 12),
                                     "addresses": {"home": {"street": "Hauptstraße 1", "city": "Musterort"}}})
    fields = vcard.parse_contact(data).fields
    assert fields["name"] == "Jörg Meier" and fields["birthday"] == "1969-07-12"
    assert fields["addresses"]["home"] == {"street": "Hauptstraße 1", "city": "Musterort"}
    updated = vcard.update_contact(data.replace("END:VCARD", "X-CUSTOM:bleibt\r\nEND:VCARD"),
                                   {"last_name": "Müller", "phones": {"mobile": None, "work": "0561 9"}})
    fields = vcard.parse_contact(updated).fields
    assert fields["name"] == "Jörg Müller" and fields["phones"] == {"work": "0561 9"}
    assert "X-CUSTOM:bleibt" in updated


def test_contact_needs_a_name():
    with pytest.raises(ValueError):
        vcard.build_contact("c", {"phones": {"mobile": "0172"}})
