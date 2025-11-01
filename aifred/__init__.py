"""AIfred Intelligence - Reflex Edition"""

# Load environment variables BEFORE importing any modules
from dotenv import load_dotenv
import os

load_dotenv()

# Startup Info: Check which APIs are available
brave_key = os.getenv('BRAVE_API_KEY')
tavily_key = os.getenv('TAVILY_API_KEY')

if brave_key:
    print(f"✅ Brave Search API Key geladen (Länge: {len(brave_key)})")
if tavily_key:
    print(f"✅ Tavily API Key geladen (Länge: {len(tavily_key)})")
if not brave_key and not tavily_key:
    print("⚠️ Keine API Keys gefunden - nur SearXNG wird verwendet")

from .aifred import app  # noqa: E402

__all__ = ["app"]
