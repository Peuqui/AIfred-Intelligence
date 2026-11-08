"""AIfred Intelligence"""

import os

# Load environment variables BEFORE importing any modules
from dotenv import load_dotenv

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
