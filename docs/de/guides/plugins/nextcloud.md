# Nextcloud Plugin

> **English version:** [nextcloud.md](../../../en/guides/plugins/nextcloud.md)

**Datei:** `aifred/plugins/tools/nextcloud/`

Termine, Aufgaben, Kontakte und Dateien aus Nextcloud über CalDAV, CardDAV und WebDAV. Das Plugin ist eigenständig — es kennt kein anderes Plugin, und ein anderes Plugin kennt es nicht.

## Tools

| Tool | Beschreibung | Tier | Angeboten |
|------|-------------|------|-----------|
| `nextcloud_search` | Termine im Zeitraum (Standard: die nächsten 30 Tage), Aufgaben, Kontakte; Listen der Kalender, Aufgabenlisten, Adressbücher | READONLY | für lesbare Module |
| `nextcloud_get` | Einen Termin, eine Aufgabe oder einen Kontakt mit allen Details | READONLY | für lesbare Module |
| `nextcloud_create` | Termin, Aufgabe oder Kontakt anlegen | WRITE_DATA | für beschreibbare Module |
| `nextcloud_update` | Termin, Aufgabe oder Kontakt ändern | WRITE_DATA | für beschreibbare Module |
| `nextcloud_delete` | Termin, Aufgabe oder Kontakt löschen | WRITE_SYSTEM | für beschreibbare Module |

| `nextcloud_file_list` | Agentenordner oder Unterordner auflisten, oder darin nach Namen suchen | WRITE_DATA | Modul Dateien lesbar |
| `nextcloud_file_read` | Textdatei lesen, bis 5 MB | WRITE_DATA | Modul Dateien lesbar |
| `nextcloud_file_write` | Textdatei anlegen oder mit ihrer ETag überschreiben | WRITE_DATA | Modul Dateien beschreibbar |
| `nextcloud_file_move` | Verschieben oder umbenennen, ohne etwas zu überschreiben | WRITE_DATA | Modul Dateien beschreibbar |
| `nextcloud_file_delete` | In den Papierkorb legen | WRITE_SYSTEM | Modul Dateien beschreibbar |

Die ID eines Eintrags ist `<sammlung>/<datei>` und stammt aus `nextcloud_search`.

Die Datei-Werkzeuge stehen bewusst für sich: Lesen trägt `WRITE_DATA` wie bei Google Drive, damit private Dateien nicht über externe Kanäle abfließen, und Dateien lassen sich einem Agenten getrennt von Kalender und Kontakten geben.

## Module und Rechte

Jedes Modul — **Termine**, **Aufgaben**, **Kontakte**, **Dateien** — steht in den Plugin-Einstellungen auf **Aus**, **Lesen** (Standard) oder **Lesen und Schreiben**. Werkzeuge, Entitätstypen und Anleitung enthalten nur die freigegebenen Module; die Dienstschicht prüft das bei jedem Aufruf noch einmal.

Geschrieben wird nur, wo **zusätzlich** Nextcloud dem eingetragenen Benutzer das Schreibrecht auf die Sammlung gibt. Empfohlen ist ein eigener Nextcloud-Benutzer für AIfred, dem man die eigenen Kalender und Adressbücher freigibt — eine nur lesende Freigabe setzt Nextcloud selbst durch, unabhängig von AIfred.

## Verhalten

- **Zeiten:** gelesen in Ortszeit; geschrieben in UTC, ganztägige Termine als Datum. Ganztägig gilt bei Ein- und Ausgabe das Ende als letzter Tag (einschließlich).
- **Serien:** Nextcloud liefert im Zeitraum die einzelnen Vorkommen (`recurring=true`). Ändern und Löschen von Serien bleibt der Kalender-App vorbehalten.
- **Verschieben:** Nur `start` zu schicken verschiebt einen Termin und behält die Dauer.
- **Bedingtes Schreiben:** Anlegen mit `If-None-Match`, Ändern und Löschen mit `If-Match` auf die ETag. Hat jemand den Eintrag inzwischen geändert, meldet das Tool das, statt zu überschreiben.
- **Erhalten bleibt beim Ändern alles Unbekannte:** Erinnerungen und Teilnehmer eines Termins, unbekannte vCard-Eigenschaften eines Kontakts.
- **Kontakte:** Telefonnummern nach Art (mobile, home, work), E-Mail (home, work), Anschriften (home, work), dazu Name, Organisation, Position, Geburtstag, Notiz. Ändert sich Vor- oder Nachname, folgt der Anzeigename.
- **Eingaben:** Datum nur als `YYYY-MM-DD` bzw. `YYYY-MM-DD HH:MM`; unbekannte Felder führen zu einer Fehlermeldung.

## Dateien

- **Agentenordner:** Agenten arbeiten nur in `NEXTCLOUD_AGENT_FOLDER`. Sie nennen Pfade relativ dazu, `..` und ähnliche Wege nach außen werden abgelehnt, bevor etwas den Server erreicht. Den Ordner selbst können sie füllen, aber weder verschieben noch löschen. Fehlt er, legt ihn der erste Schreibvorgang an.
- **Dokumentenfenster:** Nextcloud erscheint als weitere Quelle, solange das Modul Dateien nicht aus ist, live und ohne Spiegel. Dort sieht der Benutzer das ganze Konto des eingetragenen Benutzers samt Freigaben, nicht nur den Agentenordner. Hochladen geht nur auf Stufe Schreiben.
- **Überschreiben:** Eine neue Datei scheitert, wenn es sie schon gibt. Überschreiben braucht die ETag aus dem Lesen; hat jemand die Datei inzwischen geändert, schlägt es fehl. Die eine Lücke liegt beim Server: Eine Änderung in derselben Sekunde wie die vorige lässt die ETag gleich.
- **Lesen:** Nur UTF-8-Text bis 5 MB, der Inhalt geht in den Kontext des Modells und kommt als Daten gekennzeichnet zurück. Andere Dateien gibt es über das Dokumentenfenster.
- **Suchen:** nach dem Dateinamen, nicht im Inhalt.
- **Löschen:** Nextcloud legt die Datei in den Papierkorb des Benutzers.

## Konfiguration

| Feld | Speicherort | Bedeutung |
|------|------------|-----------|
| `NEXTCLOUD_URL` | Plugin-`settings.json` | Adresse, z. B. `https://cloud.example.org` oder `https://example.org/nextcloud` |
| `NEXTCLOUD_USER` | Plugin-`settings.json` | Benutzer (empfohlen: eigener Benutzer für AIfred) |
| `NEXTCLOUD_APP_PASSWORD` | `.env` (Geheimnis, über den Credential-Broker) | App-Passwort aus Nextcloud → Einstellungen → Sicherheit |
| `NEXTCLOUD_MODULE_CALENDAR`, `_TASKS`, `_CONTACTS`, `_FILES` | Plugin-`settings.json` | `off`, `read` (Standard) oder `write` |
| `NEXTCLOUD_AGENT_FOLDER` | Plugin-`settings.json` | Der Ordner, in dem Agenten arbeiten (Standard `AIfred-Intelligence`) |

Benötigte Pakete: `icalendar`, `vobject` (siehe `requirements.txt`).

## Tests

`tests/test_nextcloud_ical_vcard.py` läuft ohne Server. `tests/test_nextcloud_dav.py`, `tests/test_nextcloud_tools.py` und die Live-Teile von `tests/test_nextcloud_files.py` arbeiten live in den Testsammlungen `aifred-test` eines eigenen Testbenutzers, getrennt von dem Benutzer, unter dem AIfred läuft; der sieht nur, was ihm freigegeben ist. Die Zugangsdaten des Testbenutzers liegen lokal in `~/.config/aifred/nextcloud-test.env` (ohne die Datei werden die Tests übersprungen). Jeder Test entfernt, was er anlegt; Nextcloud hält Gelöschtes 30 Tage im Papierkorb.
