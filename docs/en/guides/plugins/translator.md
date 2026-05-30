# Translator Plugin (DeepL)

**File:** `aifred/plugins/tools/translator/`

Text translation via [DeepL API](https://www.deepl.com/docs-api). Supports 30 languages with automatic source language detection — per the plugin description, more precise than generic LLM translations.

## Setup

The plugin reads the DeepL key via the credential broker (key `deepl` / `api_key`). Two ways:

1. Create a free API key: [deepl.com/pro#developer](https://www.deepl.com/pro#developer)
2. Provide the key — either
   - in the plugin/credentials UI under **DeepL Translator**, or
   - as an `.env` variable:
     ```
     DEEPL_API_KEY=your-key-here
     ```
3. Free keys (ending in `:fx`) automatically use the free API (`api-free.deepl.com`); all other keys use the Pro API (`api.deepl.com`)

The plugin only becomes available once the key is set (`is_available()`).

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `translate` | Translate text to a target language | READONLY |

## Parameters

| Parameter | Required | Description |
|-----------|----------|------------|
| `text` | Yes | The text to translate |
| `target_lang` | Yes | Target language code (e.g. `EN`, `DE`, `FR`) — case-insensitive |
| `source_lang` | No | Source language code (auto-detected if omitted) |

Return value (JSON): `translated_text`, `source_language` (detected source), `target_language`.

## Supported Languages

AR (Arabic), BG (Bulgarian), CS (Czech), DA (Danish), DE (German), EL (Greek), EN (English), ES (Spanish), ET (Estonian), FI (Finnish), FR (French), HU (Hungarian), ID (Indonesian), IT (Italian), JA (Japanese), KO (Korean), LT (Lithuanian), LV (Latvian), NB (Norwegian), NL (Dutch), PL (Polish), PT (Portuguese), RO (Romanian), RU (Russian), SK (Slovak), SL (Slovenian), SV (Swedish), TR (Turkish), UK (Ukrainian), ZH (Chinese)

An unsupported `target_lang` returns an error listing the valid codes.

## Example Usage

> "Translate 'Good morning, how are you?' to German"

AIfred calls `translate(text="Good morning, how are you?", target_lang="DE")`.

## Limits (Free Tier)

- 500,000 characters per month
- No document translation (text only)
- Rate limits: No hard limits documented
