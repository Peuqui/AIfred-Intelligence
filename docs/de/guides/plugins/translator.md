# Translator Plugin (DeepL)

**Datei:** `aifred/plugins/tools/translator/`

Textübersetzung via [DeepL API](https://www.deepl.com/docs-api). Unterstützt 30 Sprachen mit automatischer Quellsprach-Erkennung — laut Plugin-Beschreibung präziser als generische LLM-Übersetzungen.

## Setup

Der Plugin liest den DeepL-Key über den Credential-Broker (Schlüssel `deepl` / `api_key`). Zwei Wege:

1. Kostenlosen API-Key erstellen: [deepl.com/pro#developer](https://www.deepl.com/pro#developer)
2. Key bereitstellen — entweder
   - in der Plugin-/Credentials-UI im Feld **DeepL Translator** eintragen, oder
   - als `.env`-Variable:
     ```
     DEEPL_API_KEY=your-key-here
     ```
3. Free Keys (enden auf `:fx`) nutzen automatisch die kostenlose API (`api-free.deepl.com`); alle anderen Keys die Pro-API (`api.deepl.com`)

Das Plugin ist erst verfügbar, wenn der Key gesetzt ist (`is_available()`).

## Tools

| Tool | Beschreibung | Tier |
|------|-------------|------|
| `translate` | Text in eine Zielsprache übersetzen | READONLY |

## Parameter

| Parameter | Pflicht | Beschreibung |
|-----------|---------|-------------|
| `text` | Ja | Der zu übersetzende Text |
| `target_lang` | Ja | Zielsprache als Code (z.B. `EN`, `DE`, `FR`) — Groß-/Kleinschreibung egal |
| `source_lang` | Nein | Quellsprache als Code (automatisch erkannt, wenn weggelassen) |

Rückgabe (JSON): `translated_text`, `source_language` (erkannte Quellsprache), `target_language`.

## Unterstützte Sprachen

AR (Arabisch), BG (Bulgarisch), CS (Tschechisch), DA (Dänisch), DE (Deutsch), EL (Griechisch), EN (Englisch), ES (Spanisch), ET (Estnisch), FI (Finnisch), FR (Französisch), HU (Ungarisch), ID (Indonesisch), IT (Italienisch), JA (Japanisch), KO (Koreanisch), LT (Litauisch), LV (Lettisch), NB (Norwegisch), NL (Niederländisch), PL (Polnisch), PT (Portugiesisch), RO (Rumänisch), RU (Russisch), SK (Slowakisch), SL (Slowenisch), SV (Schwedisch), TR (Türkisch), UK (Ukrainisch), ZH (Chinesisch)

Ein nicht unterstützter `target_lang` führt zu einem Fehler mit Liste der gültigen Codes.

## Beispiel-Nutzung

> "Übersetze 'Guten Morgen, wie geht es Ihnen?' ins Englische"

AIfred ruft `translate(text="Guten Morgen, wie geht es Ihnen?", target_lang="EN")` auf.

## Limits (Free Tier)

- 500.000 Zeichen pro Monat
- Keine Dokument-Übersetzung (nur Text)
- Rate Limit: Keine harten Limits dokumentiert
