# EPIM Plugin

**Datei:** `aifred/plugins/tools/epim/`

CRUD-Operationen auf EssentialPIM Firebird-Datenbank. Ermöglicht dem LLM Kontakte, Termine, Notizen, Aufgaben, Passwörter und weitere Entitäten zu verwalten.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `epim_search` | Einträge suchen (Kontakte, Termine, Notizen, Aufgaben, Passwörter, Kategorien, Kalender, Todolisten, Notizbäume) | READONLY |
| `epim_create` | Neuen Eintrag anlegen | WRITE_DATA |
| `epim_update` | Bestehenden Eintrag aktualisieren | WRITE_DATA |
| `epim_delete` | Eintrag löschen | WRITE_SYSTEM |

## Features

- **Name-zu-ID-Auflösung:** Natürliche Referenzen wie "Termin mit Max" werden automatisch auf die richtige DB-ID aufgelöst
- **7-Tage-Datumsreferenz:** Relative Datumsangaben ("morgen", "nächsten Montag") werden korrekt interpretiert
- **Anti-Halluzination:** Strikte Validierung verhindert erfundene IDs oder Felder
- **Field-Mapping:** Interne DB-Feldnamen werden auf benutzerfreundliche Namen gemappt
