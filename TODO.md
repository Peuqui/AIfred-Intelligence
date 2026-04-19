# AIfred TODO

## Grundprinzip: Security im Framework, nicht in Plugins

Security wird im System-Code erzwungen (plugin_registry, function_calling,
message_processor). Plugins koennen die Sicherheitsmechanismen NICHT umgehen.

Pipeline: Inbound Sanitization → Tier-Check → Tool-Aufruf → Output-Sanitization → Audit-Log

---

## Audio-Player Plugin

- [ ] Live-Test im Browser (AIfred soll WAV abspielen)
- [ ] Integration mit Scheduler: Weckerton zu bestimmter Uhrzeit
- [ ] Netzwerk-Playback (PulseAudio remote oder eigener Audio-Server fuer Pucks)
- [ ] **Musik-Streaming an Puck** — eigener Streaming-Kanal (nicht ueber TTS-Protokoll),
  Playlisten, Pause/Skip/Resume, setzt Bluetooth-Speaker am Puck voraus

---

## FreeEcho.2 Puck — AIfred Voice Interface

Separates Projekt: github.com/Peuqui/FreeEcho.2
Firmware-TODOs dort in TODO.md, hier nur AIfred-seitige Punkte.

---

## Security-Verfeinerung

### Guard-LLM (S8)
- [ ] Kleines Modell als Vorfilter (safe/suspicious/malicious)
- [ ] Bei suspicious: Tier runterstufen
- [ ] Bei malicious: Block + Audit-Log
- [ ] Konfigurierbar pro Channel

### Action Confirmation Verfeinerung
- [ ] Rueckfrage an Sender statt hartem Block (Telegram Inline Keyboards etc.)
- [ ] Review-Queue fuer Cron-Jobs
- [ ] Optional: PIN/zweiter Faktor

### Erweiterte Output-Sanitization
- [ ] Entropy-Detection: Hochentropie-Strings flaggen
- [ ] Pattern-Liste konfigurierbar/erweiterbar

### Information Flow Control (S10)
- [ ] Taint-Tracking: Labels propagieren durch alle Schritte
- [ ] Cross-Channel-Exfiltration verhindern
- [ ] Network Egress Control (Default-Deny)

### RAG/Vector-DB Haertung
- [ ] Untrusted-Channel-Daten bekommen niedrigen Trust-Score
- [ ] Periodisches Auditing/Purging des Vector-Cache
- [ ] Namespace-Isolation pro Channel/Sender

---

## Plugins (spaeter)

### Community & Forum Plugins
- [ ] Reddit Plugin: lesen, posten, kommentieren, Subreddit-Monitoring
- [ ] Hacker News Plugin: lesen, suchen (read-only)
- [ ] Discourse Plugin (optional)

### Neue Kanaele
- [ ] Signal Plugin (signal-cli-rest-api Docker)

### Neue Tool Plugins
- [ ] **Settings Control Plugin**: Puck-Fernsteuerung fuer Browser-Einstellungen via Tool-Use
  - `set_discussion_mode()` — Standard, Kritische Pruefung, Tribunal, Symposion
  - `set_research_mode()` — Automatik, Wissen, Web3, Web7
  - `set_active_agent()` — AIfred, Sokrates, Codi, etc.
  - Aendert Settings ueber API, Browser uebernimmt per Update-Flag
  - Muss als Tool-Use laufen (nicht Automatik), weil z.B. Web7-Modus die Automatik umgeht
- [ ] RSS/News Feed Plugin: Nachrichten-Quellen ueberwachen und zusammenfassen
- [ ] Home Assistant Plugin: Smart Home Geraete steuern (REST API)
- [ ] **Audio-Transkription Plugin**: Watchfolder fuer Audio-Dateien (z.B. Memos,
      Meeting-Recordings). Whisper ist schon installiert → neu hereingelegte
      Files automatisch transkribieren und als Text ablegen / in RAG indexieren.
- [ ] Kalender-Sync Plugin: Google Calendar / CalDAV (unabhaengig von EPIM)

### Google Suite (OAuth-basierte Plugins)
- [ ] **Google Calendar Plugin**: Termine lesen, erstellen, aendern, loeschen;
      Konfliktpruefung, Recurrence-Support. Wakeup vor Terminen an Puck.
- [ ] **Google Contacts Plugin**: Kontakte durchsuchen, fuer Email/Telegram/
      Discord-Empfaenger-Aufloesung ("schreib Max" → findet Email-Adresse).
      Bidirektional: Neue Kontakte anlegen lassen.
- [ ] **Google Drive Plugin** (optional): Dokumente durchsuchen/lesen in
      Research-Pipeline; Upload von generierten Artefakten (Reports, Code).
- [ ] **Google Tasks Plugin** (optional): Sync mit AIfred-Scheduler / Todo-Listen.
- [ ] Gemeinsamer OAuth-Broker fuer alle Google-Plugins
      (ein Login, Scopes pro Plugin angefordert)

### KI-gestuetzte Kalibrierung
- [ ] LLM-basierte Schaetzung der optimalen Kalibrierungsparameter (Proof of Concept abgeschlossen)
- [ ] Implementierung als Alternative zum Binary-Search-Algorithmus
- [ ] Cloud-API oder CPU-only Modell fuer Schaetzung (kein VRAM-Verbrauch)

---

### UI Verbesserungen
- [ ] Tages-Separatoren bei Datumswechsel in Chat (Messenger-Stil)
- [ ] Clickable Tooltips: Hilfe-Modale fuer alle UI-Bereiche (Agenten-Editor, etc.)
- [ ] **Scheduler UI: Benutzerfreundliche Zeiteingabe** (statt rohem Cron-Textfeld)
  - Cron: 5 separate Inputs (Minute, Stunde, Tag, Monat, Wochentag) mit Presets
    ("Jeden Tag um 8", "Werktags um 9", "Jede Stunde", "Jeden Montag")
  - Interval: Zahleneingabe + Einheit-Dropdown (Minuten/Stunden/Tage)
  - Once: Datum+Uhrzeit-Picker
  - Bug: Schedule-Feld aendert sich nicht beim Typ-Wechsel (cron/interval/once)

## Offene Verbesserungen

- [ ] Multi-Email-Konto Support (mehrere IMAP/SMTP Accounts, Rechte pro Konto)
- [ ] Kanaluebergreifendes Routing (eine Session ueber mehrere Kanaele — Telegram/Discord/Email/Browser)
- [ ] Research-Pipeline State-Abhaengigkeit weiter reduzieren
- [ ] Inbound-Sanitization Strictness pro Channel konfigurierbar
- [ ] Session Memory Sanitization nach Job-Ende
- [ ] Audit-Log UI Filter (Channel, Tool, Zeitraum)

---

## Backlog

- [ ] Structured Output / Data Extraction
- [ ] READMEs weiter refaktorieren
