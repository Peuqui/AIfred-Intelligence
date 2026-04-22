# Translator Plugin (DeepL)

**File:** `aifred/plugins/tools/translator/`

Text translation via [DeepL API](https://www.deepl.com/docs-api). Supports 30+ languages with automatic source language detection.

## Setup

1. Create a free API key: [deepl.com/pro#developer](https://www.deepl.com/pro#developer)
2. Add the key to `.env`:
   ```
   DEEPL_API_KEY=your-key-here
   ```
3. Free keys (ending in `:fx`) automatically use the free API (500,000 characters/month)

## Tools

| Tool | Description | Tier |
|------|------------|------|
| `translate` | Translate text to a target language | READONLY |

## Parameters

| Parameter | Required | Description |
|-----------|----------|------------|
| `text` | Yes | The text to translate |
| `target_lang` | Yes | Target language code (e.g. `EN`, `DE`, `FR`) |
| `source_lang` | No | Source language (auto-detected if omitted) |

## Supported Languages

AR (Arabic), BG (Bulgarian), CS (Czech), DA (Danish), DE (German), EL (Greek), EN (English), ES (Spanish), ET (Estonian), FI (Finnish), FR (French), HU (Hungarian), ID (Indonesian), IT (Italian), JA (Japanese), KO (Korean), LT (Lithuanian), LV (Latvian), NB (Norwegian), NL (Dutch), PL (Polish), PT (Portuguese), RO (Romanian), RU (Russian), SK (Slovak), SL (Slovenian), SV (Swedish), TR (Turkish), UK (Ukrainian), ZH (Chinese)

## Example Usage

> "Translate 'Good morning, how are you?' to German"

AIfred calls `translate(text="Good morning, how are you?", target_lang="DE")`.

## Limits (Free Tier)

- 500,000 characters per month
- No document translation (text only)
- Rate limits: No hard limits documented
