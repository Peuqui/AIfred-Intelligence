# Judaica Plugin

**Datei:** `aifred/plugins/tools/judaica/`

Zugriff auf den jüdischen Quellkorpus — Tanach, Talmud, Mischna, Midrasch,
Halacha und die klassischen Tora-Kommentare (Raschi, Ramban, Ibn Esra). Ein
Tool bündelt zwei Zugriffspfade: einen exakten Stellen-Lookup und eine
thematische Vektorsuche.

Das Plugin ist nur verfügbar, wenn der Quellordner
`data/documents/judaica/` existiert (`is_available()`).

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `search_judaica` | Durchsucht den Judaica-Korpus. Der Modus wird automatisch aus der Anfrage gewählt (siehe unten). | READONLY |

### Parameter

| Name | Typ | Pflicht | Beschreibung |
|------|-----|---------|--------------|
| `query` | string | ja | Eine Stellenangabe (z. B. `Berakhot 3`, `Pirkei Avot 1,1`) oder ein Thema (z. B. `Was sagt der Talmud über Umkehr?`) |

## Zwei Modi (automatisch gewählt)

`search_judaica` prüft die Anfrage und wählt einen der beiden Pfade:

1. **Stellen-Lookup** — nennt die Anfrage eine konkrete Stelle (`Berakhot 3`,
   `Pirkei Avot 1,1`, `Rashi zu Genesis 1,1`), wird der exakte Quelltext
   zurückgegeben (hebräisches Original + Übersetzung) aus dem strukturierten
   Judaica-JSON.
   - Eine Zahl = der gesamte Abschnitt (`Berakhot 3` → ganzer Daf 3).
   - Zwei Zahlen = Abschnitt + Eintrag (`Pirkei Avot 1,1`), optional ein Bereich
     (`Pirkei Avot 1,1-3`).
   - Talmud-Bavli-Zitate können das Vilna-Amud-Suffix nutzen (`Sanhedrin 97b`);
     es wird intern in den fortlaufenden Sefaria-Daf umgerechnet.
2. **Thematische Suche** — jede andere Anfrage löst eine Vektorsuche aus, die
   auf den `judaica`-Ordner beschränkt ist (rekursives Präfix-Matching, daher
   sind Unterordner wie `judaica/talmud`, `judaica/tanakh/tora` eingeschlossen).
   Sie nutzt die geteilte `file_manager.search_index` (`n_results=8`), also gibt
   es keine duplizierte Vektorlogik.

Fehlt der strukturierte Index, fallen Stellen-Lookups auf die thematische Suche
zurück.

## Einrichtung

Die Texte unter `data/documents/` sind nicht versioniert, daher müssen die
Daten nach dem Klonen (neu) erzeugt werden:

```bash
python scripts/download_judaica.py      # Sefaria-Quelltexte holen
python scripts/build_judaica_json.py    # *.json + _index.json Lookup-Daten bauen
```

`build_judaica_json.py` schreibt pro Werk ein strukturiertes JSON plus eine
`_index.json`, die jedes Werk mit den Namen auflistet, unter denen der
Stellen-Lookup es erkennt (`section_type`/`entry_type` unterscheiden sich je
Werk — Daf/Line beim Talmud, Chapter/Verse beim Tanach, …).

## Siehe auch

- `search_bible` für die christliche Bibel
- `search_documents` für sonstige Dokumente
