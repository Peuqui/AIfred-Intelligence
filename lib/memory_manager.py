"""
Memory Manager - Simplified for Ollama Auto-Detect

Ollama handles all memory management automatically now:
- RAM checks
- Model loading/unloading
- Hybrid GPU/CPU splits

This module only provides manual unload functionality for edge cases.
"""

from .logging_utils import debug_print


def unload_all_models():
    """
    Entlädt alle geladenen Modelle aus dem RAM

    Nützlich für:
    - Manuelles Cleanup (z.B. Debugging, RAM freigeben)
    - Nicht für regulären Betrieb - Ollama managed Models selbst

    Returns:
        bool: True wenn erfolgreich, False bei Fehler
    """
    try:
        import requests

        # Hole Liste der geladenen Modelle
        ps_response = requests.get("http://localhost:11434/api/ps")
        if ps_response.status_code == 200:
            data = ps_response.json()
            if 'models' in data and data['models']:
                # Entlade jedes Modell einzeln
                for model in data['models']:
                    model_name = model.get('name', '')
                    if model_name:
                        requests.post(
                            "http://localhost:11434/api/generate",
                            json={"model": model_name, "keep_alive": 0}
                        )
                        debug_print(f"   🗑️ {model_name} entladen")

                debug_print("🧹 Alle Modelle aus RAM entladen")
                return True
            else:
                debug_print("✅ Keine Modelle geladen")
                return True
        else:
            debug_print("⚠️ Konnte geladene Modelle nicht abrufen")
            return False
    except Exception as e:
        debug_print(f"⚠️ Fehler beim Entladen: {e}")
        return False
