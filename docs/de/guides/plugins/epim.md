# EPIM Plugin

**Datei:** `aifred/plugins/tools/epim/`

CRUD-Operationen auf einer EssentialPIM Firebird-Datenbank. Ermöglicht dem LLM, Kontakte, Termine, Notizen, Aufgaben (Todos), Passwörter und weitere Entitäten zu verwalten.

Das Plugin ist erst verfügbar, sobald ein Datenbankpfad konfiguriert ist — ohne ihn liefert `is_available()` `False` und die Tools werden nicht registriert (siehe [Konfiguration](#konfiguration)).

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `epim_search` | Einträge suchen/lesen (tasks, contacts, notes, todos, passwords, categories, calendar_list, todolists, notetrees) | READONLY |
| `epim_create` | Neuen Eintrag anlegen (task, contact, note, todo, password) | WRITE_DATA |
| `epim_update` | Bestehenden Eintrag aktualisieren (task, contact, note, note_tab, todo, password) | WRITE_DATA |
| `epim_delete` | Eintrag soft-löschen (task, contact, note, todo, password) | WRITE_SYSTEM |

### Parameter von `epim_search`

| Parameter | Typ | Hinweis |
|-----------|-----|---------|
| `entity_type` | string | **Pflicht.** Einer der obigen Entity-Typen |
| `query` | string | Suchtext (Titel, Name, Betreff) |
| `date_from` | string | Startdatum-Filter `YYYY-MM-DD`, nur für Tasks |
| `date_to` | string | Enddatum-Filter `YYYY-MM-DD`, nur für Tasks |
| `completed` | boolean | Filter nach Erledigt-Status, nur für Todos |
| `limit` | integer | Max. Ergebnisse (Standard: 20) |

`epim_create` und `epim_update` erwarten einen `entity_type` sowie ein `data`-Objekt
mit den Feldern der Entität. `epim_update` und `epim_delete` benötigen zusätzlich die
`entity_id` aus den Ergebnissen von `epim_search`.

## Features

- **Name-zu-ID-Auflösung:** Natürliche Referenzen wie "Termin mit Max" werden auf die richtige DB-ID aufgelöst; Kategorie, Kalender, Todo-Liste und Notizbaum können per Name statt ID angegeben werden.
- **Entity-Aliase:** Englische und deutsche Entity-Namen (z. B. `tasks`/`termine`, `contacts`/`kontakte`) werden auf denselben kanonischen Typ aufgelöst.
- **7-Tage-Datumsreferenz:** Der System-Prompt injiziert die kommende Woche, damit relative Angaben ("morgen", "nächsten Montag") korrekt interpretiert werden.
- **Anti-Halluzination:** Strikte Prompt-Regeln verbieten das Erfinden von Einträgen; ein leeres Ergebnis wird ehrlich gemeldet.
- **Field-Mapping:** LLM-freundliche und englische Feldnamen werden auf die internen deutschen EPIM-Spaltennamen gemappt.
- **Große-ID-Sicherheit:** Große Integer-IDs werden als Strings zurückgegeben, damit das LLM sie nicht kürzt oder rundet.

## Konfiguration

Über das Credential-Feld `EPIM_DB_PATH` setzen (z. B. in `.env` oder im Plugin-Einstellungs-UI):

```
EPIM_DB_PATH=/path/to/database.epim
```

- `EPIM_ENABLED` wird automatisch abgeleitet — es ist genau dann true, wenn `EPIM_DB_PATH` gesetzt ist.
- Ohne gültige, lesbare Datenbank meldet sich das Plugin als nicht verfügbar und stellt keine Tools bereit.
- Passwörter werden niemals ungefragt angezeigt (durch die Prompt-Instruktionen erzwungen).
