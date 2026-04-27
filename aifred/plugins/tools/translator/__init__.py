"""Translator plugin — text translation via DeepL API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY
from ....lib.plugin_base import CredentialField, PluginContext


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

        async def _translate(text: str, target_lang: str, source_lang: str = "") -> str:
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

            log_message(f"🌐 translate: {len(text)} chars → {target_lang}")

            payload: dict[str, Any] = {
                "text": [text],
                "target_lang": target_lang,
            }
            if source_lang:
                payload["source_lang"] = source_lang.upper()

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
                description=(
                    "Translate text from one language to another using DeepL. "
                    "The source language is auto-detected if not specified. "
                    f"Supported languages: {lang_list}."
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
                    },
                    "required": ["text", "target_lang"],
                },
                executor=_translate,
            ),
        ]

    def get_prompt_instructions(self, lang: str) -> str:
        return ""

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "translate":
            target = tool_args.get("target_lang", "").upper()
            text = tool_args.get("text", "")
            preview = text[:40] + "..." if len(text) > 40 else text
            lang_name = DEEPL_LANGUAGES.get(target, target)
            return f"🌐 → {lang_name}: {preview}"
        return ""


plugin = TranslatorPlugin()
