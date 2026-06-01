"""AIfred Intelligence - Reflex Edition"""

import sys

# Disable .pyc writing process-wide BEFORE importing any app module. Reflex'
# Granian dev-backend spawns its reload-workers with a curated env that drops
# PYTHONDONTWRITEBYTECODE, so the env-var route (.env / systemd) never reaches
# the process where the app imports modules. This package __init__ runs first
# in every process that touches the app (relative imports force the package
# context), so the flag reliably stops .pyc creation — exactly what the
# dev-mode file-watcher reacts to and what drove the reload-crash-loop.
# See reference_reflex_pyc_reload_loop.
sys.dont_write_bytecode = True

# Load environment variables BEFORE importing any modules
from dotenv import load_dotenv  # noqa: E402
import os  # noqa: E402

load_dotenv()

# Startup Info: Check which APIs are available
brave_key = os.getenv('BRAVE_API_KEY')
tavily_key = os.getenv('TAVILY_API_KEY')

if brave_key:
    print(f"✅ Brave Search API key loaded (length: {len(brave_key)})")
if tavily_key:
    print(f"✅ Tavily API key loaded (length: {len(tavily_key)})")
if not brave_key and not tavily_key:
    print("⚠️ No API keys found - only SearXNG will be used")

from .aifred import app  # noqa: E402

__all__ = ["app"]
