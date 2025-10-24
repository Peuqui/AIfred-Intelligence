"""
Ollama Wrapper - Centralized GPU control for all ollama.chat() calls

This wrapper patches ollama.chat() to automatically inject num_gpu parameter
based on the enable_gpu setting, without needing to modify every call site.

Features:
- Automatic hardware detection (GPU type, VRAM, vendor)
- Dynamic configuration based on available resources
- Portable across different systems (AMD iGPU, NVIDIA RTX, etc.)
- Fallback to CPU for problematic model+hardware combinations

Usage:
    # Set GPU mode at request start
    set_gpu_mode(enable_gpu=False)  # CPU only

    # All subsequent ollama.chat() calls will AUTOMATICALLY use this setting
    response = ollama.chat(model="qwen3:8b", messages=messages)
    # ↑ Internally becomes: ollama.chat(..., options={"num_gpu": 0})
"""

import ollama
from .logging_utils import debug_print, console_print
from threading import local

# Thread-local storage für GPU-Einstellung und LLM-Parameter
_thread_local = local()

# Original ollama.chat function (wird beim ersten Import gespeichert)
_original_ollama_chat = ollama.chat


def _log_ollama_performance(response) -> None:
    """
    Loggt Ollama Performance-Metriken aus der Response (zentral für alle ollama.chat() Aufrufe).

    Output:
    - Journal-Control (debug_print): Vollständige Metriken (Prompt t/s, Gen t/s, Zeit)
    - Browser Console (console_print): Nur Generation t/s (kompakt)

    Args:
        response: Ollama Response-Dict oder Pydantic-Objekt
    """
    try:
        # Konvertiere Pydantic zu Dict falls nötig
        if hasattr(response, 'model_dump'):
            data = response.model_dump()
        else:
            data = dict(response) if not isinstance(response, dict) else response

        # Extrahiere Metriken
        prompt_tokens = data.get('prompt_eval_count', 0)
        prompt_ns = data.get('prompt_eval_duration', 0)
        gen_tokens = data.get('eval_count', 0)
        gen_ns = data.get('eval_duration', 0)
        total_ns = data.get('total_duration', 0)

        # Berechne Tokens/Sekunde
        if prompt_ns > 0:
            prompt_tps = prompt_tokens / (prompt_ns / 1e9)
        else:
            prompt_tps = 0

        if gen_ns > 0:
            gen_tps = gen_tokens / (gen_ns / 1e9)
        else:
            gen_tps = 0

        total_s = total_ns / 1e9

        # Journal-Control: Vollständige Metriken
        if prompt_tps > 0 and gen_tps > 0:
            debug_print(f"   ⚡ {prompt_tps:.0f} t/s Prompt | {gen_tps:.0f} t/s Gen | {total_s:.1f}s")
        elif gen_tps > 0:
            debug_print(f"   ⚡ {gen_tps:.0f} t/s Gen | {total_s:.1f}s")
        else:
            debug_print(f"   ⚡ {total_s:.1f}s")

        # Browser Console: Nur Generation t/s (kompakt)
        if gen_tps > 0:
            console_print(f"⚡ {gen_tps:.0f} t/s")

    except Exception as e:
        debug_print(f"⚠️ Fehler beim Formatieren von Ollama Performance: {e}")



def _patched_ollama_chat(*args, **kwargs):
    """
    Patched ollama.chat() that injects num_gpu based on GPU toggle setting.

    Features:
    - CPU-only mode (num_gpu=0) when GPU toggle disabled
    - GPU Auto-Detect (no num_gpu) when GPU toggle enabled → Ollama optimizes automatically
    - Custom LLM parameters (temperature, top_p, top_k, etc.) from UI
    """
    # Hole aktuellen GPU-Modus und custom Parameter aus Thread-Local-Storage
    enable_gpu = getattr(_thread_local, 'enable_gpu', None)
    custom_options = getattr(_thread_local, 'custom_options', {})

    if enable_gpu is not None:
        if 'options' not in kwargs:
            kwargs['options'] = {}

        model_name = kwargs.get('model', '')

        # === VEREINFACHTE LOGIK: Nur CPU-Toggle, sonst Ollama Auto-Detect ===
        # num_gpu wird NUR noch gesetzt wenn User explizit CPU-only will
        # Alle Hardware-Checks (VRAM, Model-Size, Context) macht jetzt Ollama selbst!

        if not enable_gpu:
            # CPU-only: num_gpu=0 explizit setzen (User-Wahl via Toggle)
            if 'num_gpu' not in kwargs['options']:
                kwargs['options']['num_gpu'] = 0
                debug_print(f"🔧 [ollama.chat] CPU-only aktiviert (num_gpu=0) für {model_name}")
        else:
            # GPU aktiviert: Lass Ollama IMMER selbst entscheiden (Auto-Detect)
            # Ollama optimiert basierend auf: VRAM, Model-Size, Context-Größe
            debug_print(f"🔧 [ollama.chat] GPU Auto-Detect für {model_name} (Ollama optimiert Layer-Aufteilung)")

    # Merge custom LLM-Parameter (User-Eingaben überschreiben Hardware-Config!)
    if custom_options:
        if 'options' not in kwargs:
            kwargs['options'] = {}

        # User-Parameter haben PRIORITÄT (überschreiben Hardware-Config)
        for key, value in custom_options.items():
            if value is not None:
                # Spezial-Behandlung für num_ctx: User kann Hardware-Config überschreiben!
                if key == 'num_ctx' and key in kwargs['options']:
                    old_val = kwargs['options'][key]
                    kwargs['options'][key] = value
                    debug_print(f"👤 [ollama.chat] num_ctx überschrieben: {old_val} → {value} (User-Eingabe)")
                elif key not in kwargs['options']:
                    kwargs['options'][key] = value

        if custom_options:
            # Filtere None-Werte für sauberes Debug-Log
            relevant = {k: v for k, v in custom_options.items() if v is not None}
            if relevant:
                debug_print(f"🎨 [ollama.chat] Custom LLM-Parameter: {relevant}")

    # Rufe originale ollama.chat() Funktion auf
    response = _original_ollama_chat(*args, **kwargs)

    # Performance-Metriken loggen (zentral für alle ollama.chat() Aufrufe)
    _log_ollama_performance(response)

    return response


# Patche ollama.chat() global
ollama.chat = _patched_ollama_chat


def set_gpu_mode(enable_gpu=True, llm_options=None):
    """
    Setzt GPU-Modus und optionale LLM-Parameter für den aktuellen Request/Thread

    Args:
        enable_gpu: True = GPU aktiv, False = CPU only
        llm_options: dict mit LLM-Parametern (temperature, top_p, top_k, num_predict, etc.)

    WICHTIG: Muss am Anfang jeder Request-Funktion aufgerufen werden!
    Nach diesem Aufruf werden ALLE ollama.chat() Calls automatisch
    mit den korrekten Parametern versehen.

    Beispiel:
        set_gpu_mode(True, {"temperature": 0.8, "top_p": 0.9, "num_predict": 200})
    """
    _thread_local.enable_gpu = enable_gpu
    _thread_local.custom_options = llm_options or {}

    if enable_gpu:
        debug_print(f"✅ [GPU Mode] GPU-Beschleunigung aktiviert für diesen Request")
    else:
        debug_print(f"🖥️  [GPU Mode] CPU-only Modus aktiviert für diesen Request")

    if llm_options:
        # Filtere nur relevante Parameter für Debug-Ausgabe
        relevant = {k: v for k, v in llm_options.items() if v is not None}
        if relevant:
            debug_print(f"🎨 [LLM Options] Custom Parameter: {relevant}")
