"""Labels of EPIM's standard contact fields.

The database stores no names for standard fields (EPIM names them in its UI
language); this table is the only place that names them. It lists only
fields whose meaning is established: by EPIM's own field order
(CONTACTFIELDS.DEFFIELDINDEX) together with the values found in 399 live
contacts (2026-10-23: field 5 holds e-mail addresses, 21 company names,
22 job titles, 15 day numbers …). Standard fields not listed here are read
as ``field_<id>`` and cannot be written.
"""

from datetime import date

CONTACT_FIELD_LABELS: dict[int, str] = {
    1: "Vorname",
    2: "Nachname",
    3: "Telefon",
    5: "E-Mail",
    6: "Straße",
    7: "Ort",
    9: "PLZ",
    10: "Land",
    15: "Geburtstag",
    21: "Firma",
    22: "Position",
    27: "Straße geschäftlich",
    28: "Ort geschäftlich",
    30: "PLZ geschäftlich",
    31: "Land geschäftlich",
}

# CONTACTFIELDS.FIELDTYPE of date fields; their values are Delphi day numbers.
DATE_FIELD_TYPE = 1
DELPHI_EPOCH = date(1899, 12, 30)
