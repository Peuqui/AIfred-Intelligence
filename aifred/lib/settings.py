"""
Settings Persistence

Saves and loads user settings from ~/.config/aifred/settings.json
Falls back to config.py defaults if no settings file exists.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional


SETTINGS_DIR = Path.home() / ".config" / "aifred"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


def load_settings() -> Optional[Dict[str, Any]]:
    """
    Load settings from file

    Returns:
        Dict with settings or None if file doesn't exist
    """
    if not SETTINGS_FILE.exists():
        return None

    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data: Dict[str, Any] = json.load(f)
            return data
    except Exception as e:
        print(f"⚠️ Failed to load settings: {e}")
        return None


def save_settings(settings: Dict[str, Any]) -> bool:
    """
    Save settings to file

    Args:
        settings: Dict with settings to save

    Returns:
        True if successful, False otherwise
    """
    try:
        # Create directory if it doesn't exist
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)

        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)

        print(f"✅ Settings saved to {SETTINGS_FILE}")
        return True

    except Exception as e:
        print(f"❌ Failed to save settings: {e}")
        return False


def get_default_settings() -> Dict[str, Any]:
    """
    Get default settings from config.py with per-backend model defaults

    Returns:
        Dict with default settings
    """
    from ..lib.config import BACKEND_DEFAULT_MODELS

    return {
        "backend_type": "ollama",
        "research_mode": "automatik",
        "temperature": 0.2,
        "enable_thinking": True,
        "backend_models": BACKEND_DEFAULT_MODELS,  # Backend-spezifische Modelle
        # vLLM YaRN & Context Settings (0 = auto-detect on first run)
        "enable_yarn": False,
        "yarn_factor": 1.0,
        "vllm_max_tokens": 0,
        "vllm_native_context": 0,
    }
