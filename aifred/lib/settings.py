"""
Settings Persistence

Saves and loads user settings from data/settings.json
Falls back to config.py defaults if no settings file exists.
"""

import json
import os
from typing import Dict, Any, Optional

from .config import DATA_DIR

# Settings directory is the centralized data directory
SETTINGS_DIR = DATA_DIR
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
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️ Failed to load settings: {e}")
        return None


_persisted_cache: tuple[int, Dict[str, Any]] | None = None


def persisted_settings() -> Dict[str, Any]:
    """Settings as persisted: settings.json over the config defaults.

    SSOT for every reader outside the browser state (prompt layers for
    browser, scheduler and hub alike). Re-read only when the file changed —
    get_language() runs on every UI translation. Callers must not mutate
    the returned dict.
    """
    global _persisted_cache
    if not SETTINGS_FILE.exists():
        return get_default_settings()
    mtime = SETTINGS_FILE.stat().st_mtime_ns
    if _persisted_cache is None or _persisted_cache[0] != mtime:
        loaded = load_settings()
        if loaded is None:  # unreadable file: load_settings already reported it
            return get_default_settings()
        _persisted_cache = (mtime, {**get_default_settings(), **loaded})
    return _persisted_cache[1]


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

        # Atomic replace: persisted_settings() readers never see a half-written file
        tmp_file = SETTINGS_FILE.with_suffix(".json.tmp")
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, SETTINGS_FILE)

        print(f"✅ Settings saved to {SETTINGS_FILE}")
        return True

    except (OSError, json.JSONDecodeError) as e:
        print(f"❌ Failed to save settings: {e}")
        return False


def get_default_settings() -> Dict[str, Any]:
    """
    Get default settings from config.py DEFAULT_SETTINGS

    Returns:
        Dict with default settings from config.py
    """
    from .config import DEFAULT_SETTINGS, BACKEND_DEFAULT_MODELS

    # Start with DEFAULT_SETTINGS (includes backend_type from config.py)
    defaults = DEFAULT_SETTINGS.copy()

    # Full per-backend models dict — the single source of truth for
    # model fields (no flat "model"/"automatik_model" keys anymore)
    defaults["backend_models"] = BACKEND_DEFAULT_MODELS

    # Qwen3 Thinking Mode (already in DEFAULT_SETTINGS, but ensure it's present)
    # This is here for clarity and to match the state definition
    if "enable_thinking" not in defaults:
        defaults["enable_thinking"] = True

    # vLLM YaRN Settings
    defaults["enable_yarn"] = False
    defaults["yarn_factor"] = 1.0
    # NOTE: vllm_max_tokens and vllm_native_context are NEVER in defaults!
    # They are calculated dynamically on every vLLM startup based on VRAM

    return defaults


def reset_to_defaults() -> bool:
    """
    Reset all settings to defaults from config.py

    Returns:
        True if successful, False otherwise
    """
    defaults = get_default_settings()
    return save_settings(defaults)
