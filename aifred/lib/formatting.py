"""
Formatting Utilities - UI text formatting functions

This module provides formatting functions for displaying AI responses
and thinking processes in the Gradio UI.
"""

import re
from .logging_utils import log_message
from datetime import datetime


def get_timestamp() -> str:
    """
    Gibt aktuellen Timestamp im Format HH:MM:SS zurück (wie Legacy-Version).

    Returns:
        Formatted timestamp string (z.B. "18:32:33")
    """
    return datetime.now().strftime("%H:%M:%S")


def format_debug_message(message: str) -> str:
    """
    Formatiert Debug-Message mit Timestamp (wie Legacy-Version).

    Args:
        message: Debug message text

    Returns:
        Formatted message mit Timestamp (z.B. "18:32:33 | 📨 User Request empfangen")
    """
    timestamp = get_timestamp()
    return f"{timestamp} | {message}"


def format_thinking_process(ai_response, model_name=None, inference_time=None):
    """
    Formatiert <think> Tags als Collapsible Accordion für den Chat.

    Args:
        ai_response: Die AI-Antwort mit optionalen <think> Tags
        model_name: Name des verwendeten Modells (z.B. "qwen3:1.7b")
        inference_time: Inferenz-Zeit in Sekunden

    Returns:
        Formatted string mit Collapsible für Denkprozess (inkl. Modell-Name)

    Example:
        Input: "Some text <think>thinking process</think> More text"
        Output: HTML mit collapsible <think> section + clean response
    """

    # DEBUG: Logge KOMPLETTE RAW Response
    log_message("=" * 80)
    log_message("🔍 RAW AI RESPONSE (KOMPLETT):")
    log_message(ai_response)
    log_message("=" * 80)

    # Suche nach <think>...</think> Tags (normaler Fall)
    think_pattern = r'<think>(.*?)</think>'
    matches = re.findall(think_pattern, ai_response, re.DOTALL)

    # FALLBACK: Prüfe auf fehlendes öffnendes Tag (qwen3:4b Bug)
    # Wenn nur </think> vorhanden ist, aber kein <think>
    if not matches and '</think>' in ai_response:
        log_message("⚠️ Fehlendes <think> Tag erkannt - verwende Fallback-Logik")
        # Alles VOR dem ersten </think> ist Denkprozess
        parts = ai_response.split('</think>', 1)
        if len(parts) == 2:
            thinking = parts[0].strip()
            thinking = re.sub(r'\n\n+', '\n', thinking)
            clean_response = parts[1].strip()

            # Baue Summary mit Modell-Name und Inferenz-Zeit
            summary_parts = ["💭 Denkprozess"]
            if model_name:
                summary_parts.append(f"({model_name})")
            if inference_time:
                summary_parts.append(f"• {inference_time:.1f}s")
            summary_text = " ".join(summary_parts)

            formatted = f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 1em; margin-top: 0.2em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">{summary_text}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{thinking}</div>
</details>

{clean_response}"""

            return formatted

    if matches:
        # Normaler Fall: Ein <think> Block gefunden
        thinking = matches[0].strip()
        thinking = re.sub(r'\n\n+', '\n', thinking)  # Kompakt
        clean_response = re.sub(think_pattern, '', ai_response, flags=re.DOTALL).strip()

        # Baue Summary mit Modell-Name und Inferenz-Zeit
        summary_parts = ["💭 Denkprozess"]
        if model_name:
            summary_parts.append(f"({model_name})")
        if inference_time:
            summary_parts.append(f"• {inference_time:.1f}s")
        summary_text = " ".join(summary_parts)

        # Formatiere mit HTML Details/Summary (Gradio unterstützt HTML in Markdown)
        formatted = f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 1em; margin-top: 0.2em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">{summary_text}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{thinking}</div>
</details>

{clean_response}"""

        return formatted
    else:
        # Keine <think> Tags gefunden, gebe Original zurück
        return ai_response


def build_debug_accordion(query_reasoning, ai_text, automatik_model, main_model, query_time=None, final_time=None):
    """
    Baut Debug-Accordion für Agent-Recherche mit allen KI-Denkprozessen.

    Args:
        query_reasoning: <think> Content from Query Optimization
        ai_text: Final AI response with optional <think> tags
        automatik_model: Name des Automatik-Modells (für Query-Opt)
        main_model: Name des Haupt-Modells (für finale Antwort)
        query_time: Inferenz-Zeit für Query Optimization (optional)
        final_time: Inferenz-Zeit für finale Antwort (optional)

    Returns:
        Formatted AI response with debug accordion prepended
    """

    # DEBUG: Logge KOMPLETTE RAW Response
    log_message("=" * 80)
    log_message("🔍 RAW AI RESPONSE (KOMPLETT):")
    log_message(ai_text)
    log_message("=" * 80)

    debug_sections = []

    # 1. Query Optimization Reasoning (falls vorhanden)
    if query_reasoning:
        query_think = re.sub(r'\n\n+', '\n', query_reasoning)  # Kompakt
        time_suffix = f" • {query_time:.1f}s" if query_time else ""
        debug_sections.append(f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">🔍 Query-Optimierung ({automatik_model}){time_suffix}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{query_think}</div>
</details>""")

    # 2. Final Answer <think> process (extract but don't remove yet)
    think_match = re.search(r'<think>(.*?)</think>', ai_text, re.DOTALL)

    # FALLBACK: Prüfe auf fehlendes öffnendes Tag (qwen3:4b Bug)
    if not think_match and '</think>' in ai_text:
        log_message("⚠️ Fehlendes <think> Tag in Agent Response erkannt")
        # Alles VOR dem ersten </think> ist Denkprozess
        parts = ai_text.split('</think>', 1)
        if len(parts) == 2:
            final_think = parts[0].strip()
            final_think = re.sub(r'\n\n+', '\n', final_think)
            time_suffix = f" • {final_time:.1f}s" if final_time else ""
            debug_sections.append(f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">💭 Finale Antwort Denkprozess ({main_model}){time_suffix}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{final_think}</div>
</details>""")
    elif think_match:
        final_think = think_match.group(1).strip()
        final_think = re.sub(r'\n\n+', '\n', final_think)  # Kompakt
        time_suffix = f" • {final_time:.1f}s" if final_time else ""
        debug_sections.append(f"""<details style="font-size: 0.85em; color: #888; margin-bottom: 0.5em;">
<summary style="cursor: pointer; font-weight: bold; color: #aaa;">💭 Finale Antwort Denkprozess ({main_model}){time_suffix}</summary>
<div style="margin: 0; padding: 0.3em 0.8em; background: #3a3a3a; border-left: 3px solid #666; font-size: 0.9em; color: #e8e8e8; white-space: pre-wrap; word-wrap: break-word; overflow-wrap: break-word; max-width: 100%; overflow-x: hidden;">{final_think}</div>
</details>""")

    # Kombiniere alle Debug-Sections
    debug_accordion = "\n".join(debug_sections)

    # Entferne <think> Tags aus ai_text (clean response)
    # FALLBACK: Wenn nur </think> vorhanden (qwen3:4b Bug)
    if '</think>' in ai_text and '<think>' not in ai_text:
        clean_response = ai_text.split('</think>', 1)[1].strip() if '</think>' in ai_text else ai_text
    else:
        clean_response = re.sub(r'<think>.*?</think>', '', ai_text, flags=re.DOTALL).strip()

    # Return: Debug Accordion + Clean Response
    if debug_accordion:
        return f"{debug_accordion}\n\n{clean_response}"
    else:
        return clean_response
