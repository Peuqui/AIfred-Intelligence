"""Translator plugin — text translation via DeepL API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY
from ....lib.plugin_base import CredentialField, PluginContext, load_tool_description


# DeepL supported languages (subset of most common ones for description)
DEEPL_LANGUAGES: dict[str, str] = {
    "BG": "Bulgarian",
    "CS": "Czech",
    "DA": "Danish",
    "DE": "German",
    "EL": "Greek",
    "EN": "English",
    "ES": "Spanish",
    "ET": "Estonian",
    "FI": "Finnish",
    "FR": "French",
    "HU": "Hungarian",
    "ID": "Indonesian",
    "IT": "Italian",
    "JA": "Japanese",
    "KO": "Korean",
    "LT": "Lithuanian",
    "LV": "Latvian",
    "NB": "Norwegian",
    "NL": "Dutch",
    "PL": "Polish",
    "PT": "Portuguese",
    "RO": "Romanian",
    "RU": "Russian",
    "SK": "Slovak",
    "SL": "Slovenian",
    "SV": "Swedish",
    "TR": "Turkish",
    "UK": "Ukrainian",
    "ZH": "Chinese",
    "AR": "Arabic",
}


# DeepL-Satzsegmentierung. Der API-Default "1" teilt an Satzzeichen UND an
# Zeilenumbrüchen — bei hart umbrochenem Fließtext (Markdown-Docs, ~72
# Zeichen/Zeile) wird dadurch jede Zeile als eigener Satz übersetzt und der
# Kontext über den Umbruch hinweg geht verloren. Beobachtet 2026-07-18 beim
# deployment.md: "from a fresh / clone handles dependencies" wurde zu
# "aus einem neuen / Kümmer sich um Abhängigkeiten" — "clone" als Imperativ
# missverstanden und das Substantiv verschluckt. "nonewlines" segmentiert
# ausschließlich an Satzzeichen und behebt genau das.
DEEPL_SPLIT_SENTENCES = "nonewlines"

# Erlaubte Werte des DeepL-Parameters "formality" (Anrede/Tonfall). Das
# LLM wählt pro Aufruf passend zum Zieltext — Doku/Chat informell, Behörden-
# oder Geschäftskorrespondenz förmlich. Die "prefer_*"-Varianten sind
# robuster als "more"/"less": bei Sprachen ohne Formalitätsunterscheidung
# (z.B. EN) fallen sie still auf den Default zurück, statt einen API-Fehler
# zu werfen.
DEEPL_FORMALITY_VALUES = frozenset(
    {"default", "more", "less", "prefer_more", "prefer_less"}
)

# Fallback, wenn das LLM nichts angibt: informell. Begründung — der mit
# Abstand häufigste Fall in diesem Projekt ist deutschsprachige Doku und
# Chat, und die Hausordnung nutzt durchgängig "du" (README.de.md: 23x "du",
# 0x "Sie"). Für förmliche Texte setzt das LLM explizit "prefer_more".
DEEPL_FORMALITY_DEFAULT = "prefer_less"


def _get_api_url(api_key: str) -> str:
    """Return the correct DeepL API URL based on the key type."""
    if api_key.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


@dataclass
class TranslatorPlugin:
    name: str = "translator"
    display_name: str = "DeepL Translator"
    description: str = "Hochwertige Übersetzungen via DeepL-API zwischen vielen Sprachen — präziser als generische LLM-Übersetzungen."

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="DEEPL_API_KEY",
                label_key="deepl_cred_api_key",
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx",
                is_password=True,
            ),
        ]

    def is_available(self) -> bool:
        from ....lib.credential_broker import broker
        return broker.is_set("deepl", "api_key")

    def get_tools(self, ctx: PluginContext) -> list[Tool]:

        async def _translate(
            text: str,
            target_lang: str,
            source_lang: str = "",
            context: str = "",
            formality: str = "",
        ) -> str:
            """Translate text using the DeepL API."""
            import aiohttp
            from ....lib.credential_broker import broker
            from ....lib.logging_utils import log_message

            api_key = broker.get("deepl", "api_key")
            if not api_key:
                return json.dumps({"error": "DEEPL_API_KEY not configured"})

            target_lang = target_lang.upper()
            lang_codes = set(DEEPL_LANGUAGES.keys())
            if target_lang not in lang_codes:
                return json.dumps({
                    "error": f"Unsupported target language: {target_lang}",
                    "supported": sorted(lang_codes),
                })

            formality = (formality or DEEPL_FORMALITY_DEFAULT).lower()
            if formality not in DEEPL_FORMALITY_VALUES:
                return json.dumps({
                    "error": f"Unsupported formality: {formality}",
                    "supported": sorted(DEEPL_FORMALITY_VALUES),
                })

            log_message(
                f"🌐 translate: {len(text)} chars → {target_lang} ({formality})"
            )

            payload: dict[str, Any] = {
                "text": [text],
                "target_lang": target_lang,
                # Siehe Konstanten oben: Zeilenumbrüche dürfen keine
                # Satzgrenzen sein, sonst zerfällt umbrochener Fließtext.
                "split_sentences": DEEPL_SPLIT_SENTENCES,
                "formality": formality,
                # Absätze/Zeilenstruktur des Originals erhalten — bei
                # Markdown ist die Zeilenstruktur bedeutungstragend.
                "preserve_formatting": True,
            }
            if source_lang:
                payload["source_lang"] = source_lang.upper()
            if context:
                # Wird NICHT mitübersetzt und zählt nicht zum Zeichenkontingent,
                # verbessert aber Terminologie/Tonfall (DeepL-API "context").
                payload["context"] = context

            url = _get_api_url(api_key)
            headers = {
                "Authorization": f"DeepL-Auth-Key {api_key}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        log_message(f"❌ DeepL API error {resp.status}: {error_text}")
                        return json.dumps({
                            "error": f"DeepL API error {resp.status}",
                            "details": error_text[:200],
                        })

                    data = await resp.json()

            translations = data.get("translations", [])
            if not translations:
                return json.dumps({"error": "No translation returned"})

            result = translations[0]
            translated = result["text"]
            detected = result.get("detected_source_language", "")

            log_message(
                f"✅ translate: {detected} → {target_lang}, "
                f"{len(text)} → {len(translated)} chars"
            )

            return json.dumps({
                "translated_text": translated,
                "source_language": detected,
                "target_language": target_lang,
            })

        lang_list = ", ".join(f"{code} ({name})" for code, name in sorted(DEEPL_LANGUAGES.items()))

        return [
            Tool(
                name="translate",
                tier=TIER_READONLY,
                description=load_tool_description(__file__, "translate").format(
                    languages=lang_list
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The text to translate",
                        },
                        "target_lang": {
                            "type": "string",
                            "description": (
                                "Target language code (e.g. 'EN', 'DE', 'FR', 'ES', 'JA')"
                            ),
                        },
                        "source_lang": {
                            "type": "string",
                            "description": (
                                "Source language code (optional, auto-detected if omitted)"
                            ),
                        },
                        "context": {
                            "type": "string",
                            "description": (
                                "Optional surrounding context (not translated, not "
                                "billed) — improves terminology and tone. Useful when "
                                "translating a chunk of a larger document: pass the "
                                "neighbouring text or a short topic description."
                            ),
                        },
                        "formality": {
                            "type": "string",
                            "enum": sorted(DEEPL_FORMALITY_VALUES),
                            "description": (
                                "Form of address / tone, for languages that "
                                "distinguish it (DE, FR, ES, IT, NL, PL, PT, JA, RU). "
                                "'prefer_less' = informal (German 'du') — the default, "
                                "right for documentation, chat and private messages. "
                                "'prefer_more' = formal (German 'Sie') — use for "
                                "business or official correspondence, legal and "
                                "customer-facing texts. 'default' leaves the choice to "
                                "DeepL. Pick it from the target audience of the text; "
                                "if the user states a preference, follow that."
                            ),
                        },
                    },
                    "required": ["text", "target_lang"],
                },
                executor=_translate,
            ),
        ]

    def get_prompt_instructions(self, lang: str, granted_tools: "set[str] | None" = None) -> str:
        # Kein Hardcoding — atomare Fragmente in prompts/<de|en>/ beim Plugin.
        from ....lib.plugin_base import load_plugin_instructions
        return load_plugin_instructions(self, lang, granted_tools)

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "translate":
            target = tool_args.get("target_lang", "").upper()
            text = tool_args.get("text", "")
            preview = text[:40] + "..." if len(text) > 40 else text
            lang_name = DEEPL_LANGUAGES.get(target, target)
            return f"🌐 → {lang_name}: {preview}"
        return ""


plugin = TranslatorPlugin()
