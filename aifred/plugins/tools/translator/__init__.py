"""Translator plugin — text translation via DeepL API."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ....lib.function_calling import Tool
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA
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

# Maximale Zeichen pro DeepL-Request bei translate_file. Bewusst klein:
# Bei sehr langen Eingaben driftet DeepLs Zeilen-Ausrichtung — verifiziert
# 2026-07-18 mit 24 KB am Stück, dort landete ein verwaistes "###" als
# eigene Zeile im Fließtext. Mit ~5 KB pro Request tritt das nicht auf.
# Geschnitten wird ausschließlich an Absatzgrenzen (Leerzeilen), nie im
# Satz — ein zeilenweise übersetzter Absatz verliert den Satzkontext.
DEEPL_CHUNK_LIMIT = 5_000


def _get_api_url(api_key: str) -> str:
    """Return the correct DeepL API URL based on the key type."""
    if api_key.endswith(":fx"):
        return "https://api-free.deepl.com/v2/translate"
    return "https://api.deepl.com/v2/translate"


# Fenced Code-Blöcke (```…```). DeepL normalisiert Einrückungen INNERHALB
# solcher Blöcke — verifiziert 2026-07-18: aus 8 Leerzeichen vor
# "proxy_pass" wurde eines. Deshalb werden Code-Blöcke vor dem Senden
# durch Platzhalter ersetzt und danach unverändert zurückgeschrieben.
# Nebeneffekt: Code zählt nicht mehr zum DeepL-Zeichenkontingent.
_FENCE_RE = re.compile(r"^(?P<fence>```|~~~).*$")

# Platzhalter-Muster. Bewusst ohne Sonderzeichen, die DeepL umformatieren
# könnte, und mit Ziffern-ID, damit die Reihenfolge eindeutig bleibt.
_PLACEHOLDER = "CODEBLOCKPLACEHOLDER{n}X"
_PLACEHOLDER_RE = re.compile(r"CODEBLOCKPLACEHOLDER(\d+)X")


def _protect_code_blocks(text: str) -> tuple[str, list[str]]:
    """Fenced Code-Blöcke durch Platzhalter ersetzen.

    Returns ``(text_mit_platzhaltern, blöcke)``. Der Platzhalter steht
    allein auf seiner Zeile, damit DeepL ihn als eigenes Segment behandelt
    und nicht in den umgebenden Satz einbaut.
    """
    out: list[str] = []
    blocks: list[str] = []
    current: list[str] | None = None
    fence: str = ""
    for line in text.split("\n"):
        m = _FENCE_RE.match(line)
        if current is None:
            if m:
                current = [line]
                fence = m.group("fence")
            else:
                out.append(line)
        else:
            current.append(line)
            # Schließender Fence: gleiche Zeichenart, keine Sprachangabe
            if line.strip() == fence:
                blocks.append("\n".join(current))
                out.append(_PLACEHOLDER.format(n=len(blocks) - 1))
                current = None
    if current is not None:
        # Unbalancierter Block (kein schließender Fence) — unverändert
        # durchreichen statt Inhalt zu verlieren.
        blocks.append("\n".join(current))
        out.append(_PLACEHOLDER.format(n=len(blocks) - 1))
    return "\n".join(out), blocks


def _restore_code_blocks(text: str, blocks: list[str]) -> tuple[str, int]:
    """Platzhalter wieder durch die Original-Code-Blöcke ersetzen.

    Returns ``(text, anzahl_wiederhergestellt)``. Fehlt ein Platzhalter in
    der Übersetzung (DeepL hat ihn verschluckt), wird das über die Anzahl
    sichtbar — der Aufrufer meldet es, statt es still zu übergehen.
    """
    restored = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal restored
        idx = int(m.group(1))
        if 0 <= idx < len(blocks):
            restored += 1
            return blocks[idx]
        return m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text), restored


def _split_chunks(text: str, limit: int) -> list[str]:
    """Text an Absatzgrenzen (Leerzeilen) in Stücke <= ``limit`` teilen.

    NIE mitten im Satz oder pro Zeile schneiden — ein hart umbrochener
    Absatz, der zeilenweise übersetzt wird, verliert den Satzkontext
    (beobachtet 2026-07-18: "from a fresh / clone handles …" wurde zu
    "aus einem neuen / Kümmer sich um …"). Ein einzelner Absatz, der
    allein schon zu groß ist, geht ungeteilt raus.
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = f"{buf}\n\n{para}" if buf else para
        if buf and len(candidate) > limit:
            chunks.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        chunks.append(buf)
    return chunks


async def _deepl_request(
    session: Any,
    api_key: str,
    text: str,
    target_lang: str,
    source_lang: str,
    formality: str,
    context: str,
) -> tuple[str, str]:
    """Einen Text an DeepL schicken. SSOT für beide Tools.

    Returns ``(übersetzter_text, erkannte_quellsprache)``.
    Wirft ``RuntimeError`` mit der API-Fehlermeldung.
    """
    payload: dict[str, Any] = {
        "text": [text],
        "target_lang": target_lang,
        # Zeilenumbrüche sind KEINE Satzgrenzen — sonst zerfällt hart
        # umbrochener Fließtext (Markdown-Doku, ~72 Zeichen/Zeile).
        "split_sentences": DEEPL_SPLIT_SENTENCES,
        "formality": formality,
        # Zeilenstruktur des Originals erhalten (bedeutungstragend in Markdown)
        "preserve_formatting": True,
    }
    if source_lang:
        payload["source_lang"] = source_lang.upper()
    if context:
        # Wird NICHT mitübersetzt und zählt nicht zum Zeichenkontingent.
        payload["context"] = context

    headers = {
        "Authorization": f"DeepL-Auth-Key {api_key}",
        "Content-Type": "application/json",
    }
    async with session.post(_get_api_url(api_key), json=payload, headers=headers) as resp:
        if resp.status != 200:
            detail = (await resp.text())[:200]
            raise RuntimeError(f"DeepL API error {resp.status}: {detail}")
        data = await resp.json()

    translations = data.get("translations", [])
    if not translations:
        raise RuntimeError("No translation returned")
    return translations[0]["text"], translations[0].get("detected_source_language", "")


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

            try:
                async with aiohttp.ClientSession() as session:
                    translated, detected = await _deepl_request(
                        session, api_key, text, target_lang,
                        source_lang, formality, context,
                    )
            except RuntimeError as e:
                log_message(f"❌ {e}")
                return json.dumps({"error": str(e)})

            log_message(
                f"✅ translate: {detected} → {target_lang}, "
                f"{len(text)} → {len(translated)} chars"
            )

            return json.dumps({
                "translated_text": translated,
                "source_language": detected,
                "target_language": target_lang,
            })

        async def _translate_file(
            filename: str,
            target_lang: str,
            output_filename: str = "",
            source_lang: str = "",
            formality: str = "",
        ) -> str:
            """Translate a document in place — content never enters the LLM."""
            import aiohttp
            from ....lib import file_manager as fm
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

            read = fm.read_file(filename)
            if not read.success:
                return json.dumps({"error": read.detail})
            source_text = str(read.metadata.get("content", ""))

            if not output_filename:
                # <stamm>-<LANG>.<ext> im selben Verzeichnis
                p = PurePosixPath(filename)
                output_filename = str(
                    p.with_name(f"{p.stem}-{target_lang}{p.suffix}")
                )

            # Code-Blöcke rausnehmen (DeepL zerstört deren Einrückung) und
            # an Absatzgrenzen stückeln (nie mitten im Satz).
            masked, blocks = _protect_code_blocks(source_text)
            chunks = _split_chunks(masked, DEEPL_CHUNK_LIMIT)

            log_message(
                f"🌐 translate_file: {filename} → {output_filename} "
                f"({len(source_text)} chars, {len(chunks)} chunk(s), "
                f"{len(blocks)} code block(s) protected, {formality})"
            )

            detected = ""
            out_parts: list[str] = []
            try:
                async with aiohttp.ClientSession() as session:
                    for chunk in chunks:
                        translated, det = await _deepl_request(
                            session, api_key, chunk, target_lang,
                            source_lang, formality, "",
                        )
                        detected = detected or det
                        out_parts.append(translated)
            except RuntimeError as e:
                log_message(f"❌ {e}")
                return json.dumps({"error": str(e)})

            result_text, restored = _restore_code_blocks(
                "\n\n".join(out_parts), blocks
            )
            if restored != len(blocks):
                # Platzhalter verschluckt — lieber melden als still ein
                # Dokument mit fehlendem Code-Block schreiben.
                return json.dumps({
                    "error": (
                        f"Code block placeholders lost in translation "
                        f"({restored}/{len(blocks)} restored) — file not written"
                    ),
                })

            written = fm.write_file(output_filename, result_text)
            if not written.success:
                return json.dumps({"error": written.detail})

            log_message(
                f"✅ translate_file: {detected} → {target_lang}, "
                f"{len(source_text)} → {len(result_text)} chars → {output_filename}"
            )
            return json.dumps({
                "written": output_filename,
                "source_file": filename,
                "source_language": detected,
                "target_language": target_lang,
                "formality": formality,
                "chars": len(result_text),
                "code_blocks_preserved": len(blocks),
                "chunks": len(chunks),
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
            Tool(
                name="translate_file",
                # Schreibt eine Datei im Dokumentenbaum → gleiche Stufe wie
                # write_file, nicht READONLY.
                tier=TIER_WRITE_DATA,
                description=load_tool_description(__file__, "translate_file").format(
                    languages=lang_list
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": (
                                "Source file, relative to the documents root "
                                "(e.g. 'documents/deployment.md'). Use list_files "
                                "first if you are unsure of the path."
                            ),
                        },
                        "target_lang": {
                            "type": "string",
                            "description": (
                                "Target language code (e.g. 'EN', 'DE', 'FR', 'ES', 'JA')"
                            ),
                        },
                        "output_filename": {
                            "type": "string",
                            "description": (
                                "Optional output path. Default: same folder, "
                                "'<name>-<LANG>.<ext>' (e.g. 'deployment-DE.md')."
                            ),
                        },
                        "source_lang": {
                            "type": "string",
                            "description": (
                                "Source language code (optional, auto-detected if omitted)"
                            ),
                        },
                        "formality": {
                            "type": "string",
                            "enum": sorted(DEEPL_FORMALITY_VALUES),
                            "description": (
                                "Same as for 'translate': 'prefer_less' = informal "
                                "(German 'du', the default, for documentation and "
                                "chat), 'prefer_more' = formal (German 'Sie')."
                            ),
                        },
                    },
                    "required": ["filename", "target_lang"],
                },
                executor=_translate_file,
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
        if tool_name == "translate_file":
            target = tool_args.get("target_lang", "").upper()
            lang_name = DEEPL_LANGUAGES.get(target, target)
            return f"🌐 → {lang_name}: {tool_args.get('filename', '')}"
        return ""


plugin = TranslatorPlugin()
