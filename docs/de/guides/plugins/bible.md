# Bibel-Plugin

**Datei:** `aifred/plugins/tools/bible/`

Lesezugriff auf die Bibel über ein einziges Tool. Es kombiniert zwei
Zugriffswege: einen exakten Stellen-Lookup für eine benannte Referenz und
eine thematische Vektorsuche für jede andere Anfrage. Der Modus wird
automatisch aus der Anfrage gewählt.

Das Plugin ist nur verfügbar, wenn unter `data/documents/bibel/` mindestens
eine Bibelübersetzung vorhanden ist.

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `search_bible` | Bibel-Lookup. Zwei Modi, automatisch aus der Anfrage gewählt: Eine benannte Stelle (z. B. `Psalm 5`, `Joh 3,16`, `1. Mose 1,1-5`) liefert den exakten Verstext; jede andere (thematische) Anfrage führt eine thematische Suche aus und liefert verwandte Verse. | READONLY |

### Parameter

- `query` (string, erforderlich) — eine Bibelstelle (z. B. `Psalm 5`,
  `Joh 3,16`) oder ein Thema (z. B. `Verse über Trost`).

## Modi

1. **Exakter Lookup** — wenn die Anfrage als benannte Referenz erkannt wird
   (`<Buch> <Kapitel>[,<Vers>[-<Vers>]]`), wird der exakte Verstext aus dem
   strukturierten JSON der aktiven Übersetzung gelesen. Das Ergebnis enthält
   die Referenz, das Buch, das Kapitel, den Übersetzungsnamen und die Liste
   der Verse. Eine Referenz ohne Vers liefert das ganze Kapitel; ein
   Versbereich liefert jeden Vers des Bereichs.
2. **Thematische Suche** — jede andere Anfrage führt eine Vektorsuche
   beschränkt auf den Ordner `bibel` aus (über den geteilten Dokumenten-
   Store). Das Ergebnis enthält die Anfrage und eine Liste passender
   Vers-Ausschnitte mit ihren Dateinamen.

Die Buch-Erkennung ist datengetrieben: Die kanonischen Buchnamen stammen aus
dem geladenen Bibel-JSON, die Abkürzungen aus einer sprachspezifischen
Alias-Tabelle (`book_aliases/<lang>.json`, mitgeliefert für `de` und `en`).

## Konfiguration

- **Übersetzung** — die Plugin-Einstellung `BIBLE_TRANSLATION` wählt, aus
  welchem Unterordner von `data/documents/bibel/` der exakte Lookup liest.
  Sie wird in den Plugin-Einstellungen als Dropdown angezeigt; die Optionen
  sind die verfügbaren Übersetzungsordner. Ist nichts gesetzt, wird die erste
  verfügbare Übersetzung verwendet.
- **Daten** — jede Übersetzung ist ein Unterordner von
  `data/documents/bibel/` mit einem strukturierten Buch/Kapitel/Vers-JSON.
  Ein solches JSON lässt sich mit `scripts/build_bible.py` erstellen. Jede
  66-Bücher-Bibel in jeder Sprache funktioniert, indem ihr JSON-Ordner
  abgelegt wird; eine neue Sprache braucht nur ihre eigene
  `book_aliases/<lang>.json`.
