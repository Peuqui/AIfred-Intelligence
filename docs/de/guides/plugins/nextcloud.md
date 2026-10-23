# Nextcloud Plugin

> **English version:** [nextcloud.md](../../../en/guides/plugins/nextcloud.md)

**Datei:** `aifred/plugins/tools/nextcloud/`

Termine, Aufgaben und Kontakte aus Nextcloud über CalDAV und CardDAV. Das Plugin ist eigenständig — es kennt kein anderes Plugin, und ein anderes Plugin kennt es nicht.

## Tools

| Tool | Beschreibung | Tier | Angeboten |
|------|-------------|------|-----------|
| `nextcloud_search` | Termine im Zeitraum (Standard: die nächsten 30 Tage), Aufgaben, Kontakte; Listen der Kalender, Aufgabenlisten, Adressbücher | READONLY | für lesbare Module |
| `nextcloud_get` | Einen Termin, eine Aufgabe oder einen Kontakt mit allen Details | READONLY | für lesbare Module |
| `nextcloud_create` | Termin, Aufgabe oder Kontakt anlegen | WRITE_DATA | für beschreibbare Module |
| `nextcloud_update` | Termin, Aufgabe oder Kontakt ändern | WRITE_DATA | für beschreibbare Module |
| `nextcloud_delete` | Termin, Aufgabe oder Kontakt löschen | WRITE_SYSTEM | für beschreibbare Module |

Die ID eines Eintrags ist `<sammlung>/<datei>` und stammt aus `nextcloud_search`.

## Module und Rechte

Jedes Modul — **Termine**, **Aufgaben**, **Kontakte** — steht in den Plugin-Einstellungen auf **Aus**, **Lesen** (Standard) oder **Lesen und Schreiben**. Werkzeuge, Entitätstypen und Anleitung enthalten nur die freigegebenen Module; die Dienstschicht prüft das bei jedem Aufruf noch einmal.

Geschrieben wird nur, wo **zusätzlich** Nextcloud dem eingetragenen Benutzer das Schreibrecht auf die Sammlung gibt. Empfohlen ist ein eigener Nextcloud-Benutzer für AIfred, dem man die eigenen Kalender und Adressbücher freigibt — eine nur lesende Freigabe setzt Nextcloud selbst durch, unabhängig von AIfred.

## Verhalten

- **Zeiten:** gelesen in Ortszeit; geschrieben in UTC, ganztägige Termine als Datum. Ganztägig gilt bei Ein- und Ausgabe das Ende als letzter Tag (einschließlich).
- **Serien:** Nextcloud liefert im Zeitraum die einzelnen Vorkommen (`recurring=true`). Ändern und Löschen von Serien bleibt der Kalender-App vorbehalten.
- **Verschieben:** Nur `start` zu schicken verschiebt einen Termin und behält die Dauer.
- **Bedingtes Schreiben:** Anlegen mit `If-None-Match`, Ändern und Löschen mit `If-Match` auf die ETag. Hat jemand den Eintrag inzwischen geändert, meldet das Tool das, statt zu überschreiben.
- **Erhalten bleibt beim Ändern alles Unbekannte:** Erinnerungen und Teilnehmer eines Termins, unbekannte vCard-Eigenschaften eines Kontakts.
- **Kontakte:** Telefonnummern nach Art (mobile, home, work), E-Mail (home, work), Anschriften (home, work), dazu Name, Organisation, Position, Geburtstag, Notiz. Ändert sich Vor- oder Nachname, folgt der Anzeigename.
- **Eingaben:** Datum nur als `YYYY-MM-DD` bzw. `YYYY-MM-DD HH:MM`; unbekannte Felder führen zu einer Fehlermeldung.

## Konfiguration

| Feld | Speicherort | Bedeutung |
|------|------------|-----------|
| `NEXTCLOUD_URL` | Plugin-`settings.json` | Adresse, z. B. `https://cloud.example.org` oder `https://example.org/nextcloud` |
| `NEXTCLOUD_USER` | Plugin-`settings.json` | Benutzer (empfohlen: eigener Benutzer für AIfred) |
| `NEXTCLOUD_APP_PASSWORD` | `.env` (Geheimnis, über den Credential-Broker) | App-Passwort aus Nextcloud → Einstellungen → Sicherheit |
| `NEXTCLOUD_MODULE_CALENDAR`, `_TASKS`, `_CONTACTS` | Plugin-`settings.json` | `off`, `read` (Standard) oder `write` |

Benötigte Pakete: `icalendar`, `vobject` (siehe `requirements.txt`).

## Tests

`tests/test_nextcloud_ical_vcard.py` läuft ohne Server. `tests/test_nextcloud_dav.py` und `tests/test_nextcloud_tools.py` arbeiten live in den Testsammlungen `aifred-test` eines eigenen Benutzers; die Zugangsdaten liegen lokal in `~/.config/aifred/nextcloud-aifred.env` (ohne die Datei werden sie übersprungen). Jeder Test entfernt, was er anlegt.
