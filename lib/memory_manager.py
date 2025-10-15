"""
Memory Manager - Smart Model Loading & RAM Management

This module handles intelligent loading and unloading of Ollama models
based on available system RAM to prevent swapping.
"""

import subprocess
import time
import signal
import sys
from .logging_utils import debug_print


def get_model_size(model_name):
    """
    Holt Modell-Größe von Ollama (via 'ollama list')

    Args:
        model_name: Name des Modells (z.B. "qwen3:8b")

    Returns:
        int: Modell-Größe in Bytes, oder 0 falls nicht gefunden
    """
    try:
        result = subprocess.run(
            ['ollama', 'list'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return 0

        # Parse Output: Suche nach model_name in der Liste
        for line in result.stdout.split('\n'):
            if model_name in line:
                # Format: "qwen3:8b  500a1f067a9f  5.2 GB  42 hours ago"
                parts = line.split()
                if len(parts) >= 3:
                    size_str = parts[2]  # "5.2"
                    unit = parts[3]      # "GB" oder "MB"

                    size_float = float(size_str)

                    # Konvertiere zu Bytes
                    if unit == "GB":
                        return int(size_float * 1024 * 1024 * 1024)
                    elif unit == "MB":
                        return int(size_float * 1024 * 1024)

        return 0

    except Exception as e:
        debug_print(f"⚠️ Fehler beim Abrufen der Modellgröße: {e}")
        return 0


def get_available_memory():
    """
    Gibt verfügbaren RAM in Bytes zurück

    Returns:
        int: Verfügbarer RAM in Bytes
    """
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    # Format: "MemAvailable:   25532336 kB"
                    kb = int(line.split()[1])
                    return kb * 1024  # Konvertiere zu Bytes
    except Exception as e:
        debug_print(f"⚠️ Fehler beim RAM-Check: {e}")
        return 0


def get_loaded_models_size():
    """
    Gibt Größe aller aktuell geladenen Modelle zurück

    Returns:
        int: Gesamt-Größe in Bytes
    """
    try:
        import requests
        response = requests.get("http://localhost:11434/api/ps")
        if response.status_code == 200:
            data = response.json()
            total_size = 0
            loaded_models = []

            if 'models' in data:
                for model in data['models']:
                    size = model.get('size', 0)
                    total_size += size
                    loaded_models.append(f"{model['name']} ({size // (1024**3):.1f} GB)")

            if loaded_models:
                debug_print(f"📊 Geladene Modelle: {', '.join(loaded_models)}")

            return total_size
    except Exception as e:
        debug_print(f"⚠️ Fehler beim Model-Size-Check: {e}")
        return 0


def unload_all_models():
    """
    Entlädt alle geladenen Modelle aus dem RAM

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


def cleanup_on_exit(signum, frame):
    """
    Signal Handler für sauberen Service-Stop

    Wird ausgelöst bei:
    - systemctl stop aifred-intelligence
    - systemctl restart aifred-intelligence
    - SIGTERM / SIGINT

    Nicht ausgelöst bei:
    - Browser Reload (Backend läuft weiter)
    """
    signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    print(f"\n🛑 {signal_name} empfangen - Service wird gestoppt", flush=True)
    print("🧹 Entlade alle Modelle aus RAM...", flush=True)

    try:
        unload_all_models()
        print("✅ Cleanup abgeschlossen", flush=True)
    except Exception as e:
        print(f"⚠️ Fehler beim Cleanup: {e}", flush=True)

    sys.exit(0)


def register_signal_handlers():
    """
    Registriert Signal Handler für sauberen Shutdown

    Muss einmal beim Programm-Start aufgerufen werden
    """
    signal.signal(signal.SIGTERM, cleanup_on_exit)  # systemctl stop/restart
    signal.signal(signal.SIGINT, cleanup_on_exit)   # Ctrl+C


def smart_model_load(model_name):
    """
    Intelligentes Model Loading mit RAM-Check und automatischem Memory Management

    Logic:
    1. Prüft, ob das Modell bereits geladen ist → Nichts tun
    2. Holt Modellgröße dynamisch von Ollama (via 'ollama list')
    3. Checkt verfügbaren RAM und geladene Modelle
    4. Entscheidet:
       - Wenn neues Modell NICHT passt → Entlade ALLE Modelle
       - Wenn neues Modell passt UND große ungenutzte Modelle (>10GB) geladen sind → Entlade diese
       - Sonst → Lade neues Modell ohne Entladen

    Args:
        model_name: Name des zu ladenden Modells

    Returns:
        bool: True wenn erfolgreich geladen
    """
    import requests

    # 1. Prüfe ob Modell bereits geladen ist
    try:
        response = requests.get("http://localhost:11434/api/ps")
        if response.status_code == 200:
            data = response.json()
            if 'models' in data:
                loaded_model_names = [model.get('name', '') for model in data['models']]
                if model_name in loaded_model_names:
                    debug_print(f"✅ Modell {model_name} bereits geladen - nichts zu tun")
                    return True
    except Exception as e:
        debug_print(f"⚠️ Fehler beim Prüfen geladener Modelle: {e}")

    # 2. Hole Modellgröße dynamisch von Ollama
    model_size = get_model_size(model_name)

    # Wenn Modell nicht gefunden (z.B. noch nicht gepullt)
    if model_size == 0:
        debug_print(f"⚠️ Modell {model_name} nicht in 'ollama list' gefunden - wird beim ersten Aufruf gepullt")
        return True

    # 3. RAM-Check
    available_ram = get_available_memory()
    loaded_size = get_loaded_models_size()

    available_gb = available_ram / (1024**3)
    model_gb = model_size / (1024**3)
    loaded_gb = loaded_size / (1024**3)

    debug_print(f"📊 Memory Check:")
    debug_print(f"   Verfügbar: {available_gb:.1f} GB")
    debug_print(f"   Geladen: {loaded_gb:.1f} GB")
    debug_print(f"   Neues Modell: {model_name} ({model_gb:.1f} GB)")

    # 4. Entscheidungslogik: Brauchen wir 20% Safety Margin
    safety_margin = 0.20  # 20% Reserve für KV Cache, Context Buffer, Temp Tensors
    required_ram = model_size * (1 + safety_margin)
    required_gb = required_ram / (1024**3)

    # Check: Passt Modell + Safety Margin in verfügbaren RAM?
    if available_ram >= required_ram:
        debug_print(f"✅ Genug RAM! {available_gb:.1f} GB >= {required_gb:.1f} GB (mit 20% Reserve)")

        # ABER: Prüfe ob große ungenutzte Modelle (>10GB) geladen sind
        try:
            response = requests.get("http://localhost:11434/api/ps")
            if response.status_code == 200:
                data = response.json()
                if 'models' in data:
                    large_models = []
                    for model in data['models']:
                        m_name = model.get('name', '')
                        m_size = model.get('size', 0)
                        m_size_gb = m_size / (1024**3)
                        # Große Modelle (>10GB) die nicht das neue Modell sind
                        if m_size_gb > 10 and m_name != model_name:
                            large_models.append((m_name, m_size_gb))

                    if large_models:
                        debug_print(f"⚠️ Große ungenutzte Modelle gefunden: {', '.join([f'{n} ({s:.1f}GB)' for n, s in large_models])}")
                        debug_print(f"🧹 Entlade diese Modelle um Swap zu vermeiden...")
                        unload_all_models()
                        time.sleep(0.5)
                        new_available = get_available_memory() / (1024**3)
                        debug_print(f"✅ RAM nach Entladen: {new_available:.1f} GB verfügbar")
                    else:
                        debug_print(f"   Kein Entladen nötig - Modell passt rein!")
        except Exception as e:
            debug_print(f"⚠️ Fehler beim Prüfen großer Modelle: {e}")

        return True
    else:
        debug_print(f"⚠️ Zu wenig RAM! {available_gb:.1f} GB < {required_gb:.1f} GB (mit 20% Reserve)")
        debug_print(f"🔄 Modell {model_name} ({model_gb:.1f} GB) benötigt mehr RAM")
        debug_print(f"🧹 Entlade aktuell geladene Modelle ({loaded_gb:.1f} GB)...")
        unload_all_models()
        time.sleep(0.5)  # Kurze Pause für sauberes Entladen

        # Nochmal checken nach Entladen
        new_available = get_available_memory() / (1024**3)
        debug_print(f"✅ RAM nach Entladen: {new_available:.1f} GB verfügbar")
        return True
