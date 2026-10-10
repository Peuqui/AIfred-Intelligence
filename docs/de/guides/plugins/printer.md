# Drucker-Plugin

> **English version:** [printer.md](../../../en/guides/plugins/printer.md)

**Datei:** `aifred/plugins/tools/printer/`

Druckt Dateien aus dem Dokumentenbereich und liest den Druckerzustand über **CUPS**.
Das Plugin spricht CUPS über dessen Kommandozeilenwerkzeuge an (`lp`, `lpstat`,
`ipptool`) — ein Python-Paket ist nicht nötig, die CUPS-Client-Werkzeuge müssen aber
installiert sein. Die Befehle laufen mit `LC_ALL=C` und 15 s Zeitlimit.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `print_file` | Datei aus dem Dokumentenbereich auf dem Standarddrucker drucken | COMMUNICATE |
| `printer_status` | Zustand, Warnungen, Auftragsschlange und Füllstände des Standarddruckers | READONLY |

### Parameter (`print_file`)

| Parameter | Typ | Pflicht | Beschreibung |
|-----------|-----|---------|--------------|
| `filename` | string | ja | Pfad im Dokumentenbereich, wie `list_files` ihn zeigt (z. B. `rechnungen/2026-10.pdf`) |
| `copies` | integer | nein | Anzahl Kopien (Standard 1; vom Plugin nach oben begrenzt) |
| `pages` | string | nein | Seitenauswahl, z. B. `1-3,5` (leer = alle Seiten) |
| `duplex` | boolean | nein | Beidseitig drucken (lange Kante); Standard einseitig |

Rückgabe (JSON): `printer`, `job` (CUPS-Auftrags-ID), `file`, `copies`. `printer_status`
hat keine Parameter und liefert `printer`, `state` (`idle`, `processing`, `stopped`),
`reasons` (CUPS-Zustandsgründe wie `media-empty-warning`), `message`, `queued_jobs`,
`supplies` (Name, Art, Füllstand in %, untere/obere Marke) sowie die abgeleiteten Listen
`low_supplies` (Verbrauchsmaterial an oder unter der unteren Marke) und
`full_waste_containers` (Resttonerbehälter an oder über der oberen Marke). Bei einem
Resttonerbehälter ist der Füllstand sein Füllgrad.

## Welcher Drucker

Die Drucker kommen aus CUPS selbst (`lpstat -e`), auch Netzwerkdrucker, die `cups-browsed`
findet — die gibt es nur, solange der Drucker erreichbar ist. Im Plugin ist keiner fest
eingetragen. Der Standarddrucker wird so bestimmt:

1. der in den Plugin-Einstellungen gewählte Drucker, wenn CUPS ihn gerade listet;
2. sonst der einzige Drucker, wenn genau einer gelistet ist;
3. sonst ein Fehler: kein Drucker erreichbar (CUPS listet keinen), der gewählte Drucker ist nicht erreichbar, oder mehrere Drucker und keiner gewählt. Es gibt keinen automatischen Ausweichdrucker.

## Konfiguration

Plugin-Einstellungen (im Plugin-Tab; gespeichert in
`aifred/plugins/tools/printer/settings.json`, keine Geheimnisse):

| Schlüssel | Standard | Beschreibung |
|-----------|----------|--------------|
| `PRINTER_DEFAULT` | leer | Standarddrucker; die Auswahl listet, was CUPS gerade erreicht. Bei nur einem Drucker wird er ohne Auswahl benutzt |
| `PRINTER_MAX_COPIES` | `10` | Höchstzahl Kopien pro Auftrag — Schutz gegen Vertipper des Modells; mehr lehnt `print_file` ab |

## Rechte und Grenzen

- `print_file` hat den Tier **COMMUNICATE**: Kanäle auf dieser Stufe (E-Mail, Discord, Telegram, FreeEcho.2) dürfen drucken; Quellen mit READONLY (Scheduler, AI-Connect, Webhook, sofern ein Job nicht sein `max_tier` anhebt) können nur `printer_status` lesen. Siehe [Security](../../architecture/security.md).
- Gedruckt werden nur Dateien **im lokalen Dokumentenbereich** (`safe_resolve`: kein Pfad außerhalb). Dateien aus anderen Dokumentenquellen (z. B. Google Drive) nicht.
- CUPS druckt diese Typen direkt: `.pdf`, `.txt`, `.png`, `.jpg`, `.jpeg`, `.ps`. Alles andere (Office, HTML, …) wird mit dem Hinweis abgelehnt, es zuerst in PDF umzuwandeln. Für eine Testseite genügt eine kurze `.txt`-Datei.
- Die CUPS-Aufträge laufen unter dem Benutzer, unter dem AIfred läuft; zusätzliche Rechte oder `sudo` sind nicht im Spiel.

## Ablauf

1. Das Modell prüft den Dateinamen (`list_files`) und ruft `print_file` auf — nur auf ausdrücklichen Wunsch und nur, was verlangt wurde.
2. Das Plugin löst die Datei im Dokumentenbereich auf, prüft Typ und Kopienzahl und wählt den Drucker.
3. `lp -d <Drucker> -n <Kopien> [-P <Seiten>] -o sides=… -- <Datei>` übergibt die Datei an CUPS; die CUPS-Auftrags-ID kommt zurück.
4. Das Modell sagt kurz, was auf welchem Drucker gedruckt wurde. Zustand und Füllstände liest `printer_status` (`ipptool` gegen `ipp://localhost/printers/<Drucker>`); bei einem Problem nennt das Modell die konkrete Handlung (Papier nachlegen, Toner wechseln).

Ein Scheduler-Job kann `printer_status` abfragen und auf jedem Kanal benachrichtigen, wenn Verbrauchsmaterial zur Neige geht.
