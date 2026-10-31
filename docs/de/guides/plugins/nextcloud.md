# Nextcloud Plugin

> **English version:** [nextcloud.md](../../../en/guides/plugins/nextcloud.md)

**Datei:** `aifred/plugins/tools/nextcloud/`

Termine, Aufgaben, Kontakte, Notizen und Dateien aus Nextcloud über CalDAV, CardDAV und WebDAV. Das Plugin ist eigenständig — es kennt kein anderes Plugin, und ein anderes Plugin kennt es nicht.

## Tools

| Tool | Beschreibung | Tier | Angeboten |
|------|-------------|------|-----------|
| `nextcloud_search` | Termine im Zeitraum (Standard: die nächsten 30 Tage), Aufgaben, Kontakte; Listen der Kalender, Aufgabenlisten, Adressbücher | WRITE_DATA | für lesbare Module |
| `nextcloud_get` | Einen Termin, eine Aufgabe, einen Kontakt oder eine Notiz mit allen Details | WRITE_DATA | für lesbare Module |
| `nextcloud_create` | Termin, Aufgabe, Kontakt oder Notiz anlegen | WRITE_DATA | für beschreibbare Module |
| `nextcloud_update` | Termin, Aufgabe, Kontakt oder Notiz ändern | WRITE_DATA | für beschreibbare Module |
| `nextcloud_delete` | Termin, Aufgabe, Kontakt oder Notiz löschen | WRITE_SYSTEM | für beschreibbare Module |

| `nextcloud_file_list` | Agentenordner oder Unterordner auflisten, oder darin nach Namen suchen | WRITE_DATA | Modul Dateien lesbar |
| `nextcloud_file_read` | Textdatei lesen, auch abschnittsweise nach Zeilen | WRITE_DATA | Modul Dateien lesbar |
| `nextcloud_file_write` | Textdatei anlegen oder mit ihrer ETag überschreiben | WRITE_DATA | Modul Dateien beschreibbar |
| `nextcloud_file_move` | Verschieben oder umbenennen, ohne etwas zu überschreiben | WRITE_DATA | Modul Dateien beschreibbar |
| `nextcloud_file_delete` | In den Papierkorb legen | WRITE_SYSTEM | Modul Dateien beschreibbar |

Die ID eines Eintrags ist `<sammlung>/<datei>` und stammt aus `nextcloud_search`.

Auch Lesen trägt `WRITE_DATA`: Termine, Kontakte und Dateien sind privat und sollen nicht über externe Kanäle abfließen. Auf einem Kanal unterhalb dieser Stufe und in geplanten Jobs mit `max_tier` 1 steht also auch das Lesen nicht zur Verfügung.

Die Datei-Werkzeuge stehen bewusst für sich: Dateien lassen sich einem Agenten getrennt von Kalender und Kontakten geben.

## Module und Rechte

Jedes Modul — **Termine**, **Aufgaben**, **Kontakte**, **Notizen**, **Dateien** — steht in den Plugin-Einstellungen auf **Aus**, **Lesen** (Standard) oder **Lesen und Schreiben**. Werkzeuge, Entitätstypen und Anleitung enthalten nur die freigegebenen Module; die Dienstschicht prüft das bei jedem Aufruf noch einmal.

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
- **Dokumentenfenster:** Nextcloud erscheint als weitere Quelle, solange das Modul Dateien nicht aus ist, live und ohne Spiegel. Dort sieht der Benutzer das ganze Konto des eingetragenen Benutzers samt Freigaben, nicht nur den Agentenordner. Hochladen geht nur auf Stufe Schreiben. Dateien lassen sich dort einzeln oder als ganzer Ordner für die Dokumentensuche indexieren, siehe [Workspace](workspace.md).
- **Überschreiben:** Eine neue Datei scheitert, wenn es sie schon gibt. Überschreiben braucht die ETag aus dem Lesen; hat jemand die Datei inzwischen geändert, schlägt es fehl. Die eine Lücke liegt beim Server: Eine Änderung in derselben Sekunde wie die vorige lässt die ETag gleich.
- **Lesen:** Nur UTF-8-Text, ohne Größengrenze: Die Datei läuft als Strom durch, im Speicher bleibt nur der gewählte Zeilenbereich (`lib/line_window.py`), eine Binärdatei fällt beim ersten ungültigen Zeichen heraus. Der Inhalt geht in den Kontext des Modells und kommt als Daten gekennzeichnet zurück; was nicht hineinpasst, kürzt die allgemeine Begrenzung der Werkzeug-Ausgaben von hinten. Lange Dateien liest ein Agent deshalb abschnittsweise mit `line_start` und `line_end`; die Antwort nennt `total_lines`. Die ETag gilt immer für die ganze Datei. Andere Dateien gibt es über das Dokumentenfenster.
- **Suchen:** nach dem Dateinamen, nicht im Inhalt.
- **Löschen:** Nextcloud legt die Datei in den Papierkorb des Benutzers.

## Notizen

Die Notizen-App speichert jede Notiz als Textdatei: Der Titel ist der Dateiname, die Kategorie der Unterordner. Agenten arbeiten über `nextcloud_search`, `nextcloud_get`, `nextcloud_create`, `nextcloud_update` und `nextcloud_delete` mit dem Typ `note` nur in `NEXTCLOUD_NOTES_FOLDER`, App und Agenten sehen denselben Stand.

- **Freigabe:** Die Notizen des Benutzers liegen in dessen Konto. Der Ordner `Notes` muss mit dem Nextcloud-Benutzer von AIfred geteilt sein, zum Schreiben mit Schreibrecht. Fehlt er, wird das Anlegen abgelehnt, statt einen unsichtbaren eigenen Ordner zu erzeugen.
- **Suchen:** nach dem Titel, nicht im Text.
- **Ändern:** `content` ersetzt den ganzen Text. Ein neuer Titel oder eine neue Kategorie verschiebt die Datei, das Ergebnis nennt die neue ID.
- **Löschen:** Die Notiz kommt in den Papierkorb.

## Löschungen aus EssentialPIM

EPIMs Option „Gelöschte Einträge automatisch auf Gegenseite löschen“ bleibt beim Abgleich mit Nextcloud aus: In rund jedem vierten Lauf verliert EPIM beim Lesen Einträge, hält sie für auf dem Server gelöscht und löscht sie mit der Option bei sich. Der Server liefert dabei vollständig.

Ohne die Option erreicht eine Löschung in EPIM Nextcloud nicht. Das übernimmt `scripts/epim_deletions_to_nextcloud.py`: Es liest die neueste von EPIM selbst geschriebene Sicherung, nimmt Einträge aus dem Papierkorb, die noch eine CalDAV-Zuordnung haben, und löscht sie in Nextcloud, wenn genau ein nicht wiederkehrender Termin mit gleichem Titel und Beginn existiert und EPIM keinen aktiven gleichen Eintrag hat. Serien und mehrdeutige Treffer werden nur gemeldet. Kontakte und Aufgaben sind nicht abgedeckt, ebenso wenig Löschungen in Nextcloud, die bleiben in EPIM stehen.

Einstellungen stehen in `data/epim/deletion_sync.json` (`ssh_host`, `ssh_port`, `ssh_user`, `backup_dir`, `backup_glob`, `work_dir`, `max_deletions`). Ohne `--apply` zeigt das Skript nur den Plan; über `max_deletions` löscht es nichts und endet mit Fehlercode. Der Benutzer-Timer `systemd/aifred-epim-deletions.timer` startet es stündlich: Ist der EPIM-Rechner aus, endet der Lauf sofort ohne Fehler, ist die Sicherung schon verarbeitet, passiert nichts. Eine Löschung erreicht Nextcloud also mit der nächsten Sicherung, die EPIM schreibt.

## Konfiguration

| Feld | Speicherort | Bedeutung |
|------|------------|-----------|
| `NEXTCLOUD_URL` | Plugin-`settings.json` | Adresse, z. B. `https://cloud.example.org` oder `https://example.org/nextcloud` |
| `NEXTCLOUD_USER` | Plugin-`settings.json` | Benutzer (empfohlen: eigener Benutzer für AIfred) |
| `NEXTCLOUD_NOTES_FOLDER` | Plugin-`settings.json` | Ordner der Notizen-App (Standard `Notes`) |
| `NEXTCLOUD_APP_PASSWORD` | `.env` (Geheimnis, über den Credential-Broker) | App-Passwort aus Nextcloud → Einstellungen → Sicherheit |
| `NEXTCLOUD_MODULE_CALENDAR`, `_TASKS`, `_CONTACTS`, `_FILES` | Plugin-`settings.json` | `off`, `read` (Standard) oder `write` |
| `NEXTCLOUD_AGENT_FOLDER` | Plugin-`settings.json` | Der Ordner, in dem Agenten arbeiten (Standard `AIfred-Intelligence`) |

Benötigte Pakete: `icalendar`, `vobject` (siehe `requirements.txt`).

## Tests

`tests/test_nextcloud_ical_vcard.py` läuft ohne Server. `tests/test_nextcloud_dav.py`, `tests/test_nextcloud_tools.py` und die Live-Teile von `tests/test_nextcloud_files.py` arbeiten live in den Testsammlungen `aifred-test` eines eigenen Testbenutzers, getrennt von dem Benutzer, unter dem AIfred läuft; der sieht nur, was ihm freigegeben ist. Die Zugangsdaten des Testbenutzers liegen lokal in `~/.config/aifred/nextcloud-test.env` (ohne die Datei werden die Tests übersprungen). Jeder Test entfernt, was er anlegt; Nextcloud hält Gelöschtes 30 Tage im Papierkorb.
