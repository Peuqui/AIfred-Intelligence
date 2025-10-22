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

    # Clear at request end (automatic cleanup)
    clear_gpu_mode()
"""

import ollama
import requests
import subprocess
from .logging_utils import debug_print
from threading import local

# Thread-local storage für GPU-Einstellung und LLM-Parameter
_thread_local = local()

# Original ollama.chat function (wird beim ersten Import gespeichert)
_original_ollama_chat = ollama.chat



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
    return _original_ollama_chat(*args, **kwargs)


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


def clear_gpu_mode():
    """
    Räumt GPU-Einstellung und LLM-Parameter auf (nach Request)
    """
    if hasattr(_thread_local, 'enable_gpu'):
        was_gpu = _thread_local.enable_gpu
        del _thread_local.enable_gpu
        debug_print(f"🔧 [GPU Mode] Cleanup - {'GPU' if was_gpu else 'CPU'} Modus beendet")

    if hasattr(_thread_local, 'custom_options'):
        del _thread_local.custom_options


