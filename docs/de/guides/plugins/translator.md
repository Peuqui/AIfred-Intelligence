# Translator Plugin (DeepL)

**Datei:** `aifred/plugins/tools/translator/`

Textubersetzung via [DeepL API](https://www.deepl.com/docs-api). Unterstuetzt 30+ Sprachen mit automatischer Quellsprach-Erkennung.

## Setup

1. Kostenlosen API-Key erstellen: [deepl.com/pro#developer](https://www.deepl.com/pro#developer)
2. Key in `.env` eintragen:
   ```
   DEEPL_API_KEY=your-key-here
   ```
3. Free Keys (enden auf `:fx`) nutzen automatisch die kostenlose API (500.000 Zeichen/Monat)

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `translate` | Text in eine Zielsprache uebersetzen | READONLY |

## Parameter

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `text` | Ja | Der zu uebersetzende Text |
| `target_lang` | Ja | Zielsprache als Code (z.B. `EN`, `DE`, `FR`) |
| `source_lang` | Nein | Quellsprache (automatisch erkannt wenn weggelassen) |

## Unterstuetzte Sprachen

AR (Arabisch), BG (Bulgarisch), CS (Tschechisch), DA (Daenisch), DE (Deutsch), EL (Griechisch), EN (Englisch), ES (Spanisch), ET (Estnisch), FI (Finnisch), FR (Franzoesisch), HU (Ungarisch), ID (Indonesisch), IT (Italienisch), JA (Japanisch), KO (Koreanisch), LT (Litauisch), LV (Lettisch), NB (Norwegisch), NL (Niederlaendisch), PL (Polnisch), PT (Portugiesisch), RO (Rumaenisch), RU (Russisch), SK (Slowakisch), SL (Slowenisch), SV (Schwedisch), TR (Tuerkisch), UK (Ukrainisch), ZH (Chinesisch)

## Beispiel-Nutzung

> "Uebersetze 'Guten Morgen, wie geht es Ihnen?' ins Englische"

AIfred ruft `translate(text="Guten Morgen, wie geht es Ihnen?", target_lang="EN")` auf.

## Limits (Free Tier)

- 500.000 Zeichen pro Monat
- Keine Dokument-Uebersetzung (nur Text)
- Rate Limit: Keine harten Limits dokumentiert
