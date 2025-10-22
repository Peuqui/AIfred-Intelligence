#!/usr/bin/env python3
"""
Test script for Playwright scraping implementation
Tests katiecouric.com to verify JavaScript content extraction
"""

import sys
import os

# Add project directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import after path is set
from agent_tools import WebScraperTool

def test_katiecouric_scraping():
    """Test scraping of katiecouric.com with Playwright fallback"""

    print("=" * 80)
    print("Testing Playwright Scraping Implementation")
    print("=" * 80)

    scraper = WebScraperTool()
    url = "https://katiecouric.com/entertainment/movies-tv/emmy-winners-2025-complete-list/"

    print(f"\n🎯 Testing URL: {url}\n")

    result = scraper.execute(url)

    print(f"✅ Success: {result.get('success')}")
    print(f"🔧 Method: {result.get('method', 'N/A')}")
    print(f"📝 Title: {result.get('title', 'N/A')}")
    print(f"📊 Word count: {result.get('word_count', 0)}")
    print(f"✂️  Truncated: {result.get('truncated', False)}")

    # Check if important names are now extracted
    content = result.get('content', '')

    print("\n" + "=" * 80)
    print("Checking for previously missing names:")
    print("=" * 80)

    test_names = [
        'The Pitt',
        'Noah Wyle',
        'Seth Rogen',
        'Nikki Glaser',
        'Anna Sawai',
        'Shogun'
    ]

    names_found = []
    names_missing = []

    for name in test_names:
        if name in content:
            names_found.append(name)
            print(f"✅ Found: {name}")
        else:
            names_missing.append(name)
            print(f"❌ Missing: {name}")

    print("\n" + "=" * 80)
    print(f"Summary: {len(names_found)}/{len(test_names)} names found")
    print("=" * 80)

    if names_found:
        print(f"\n✅ Gefunden: {', '.join(names_found)}")

    if names_missing:
        print(f"\n❌ Fehlend: {', '.join(names_missing)}")

    print("\n" + "=" * 80)
    print("First 800 characters of content:")
    print("=" * 80)
    print(content[:800])
    print("...")

    # Final verdict
    print("\n" + "=" * 80)
    if result.get('method') == 'playwright' and result.get('word_count', 0) > 1500:
        print("🎉 SUCCESS: Playwright triggered and extracted more content!")
    elif result.get('word_count', 0) > 1500:
        print("✅ SUCCESS: More content extracted (BeautifulSoup was sufficient)")
    else:
        print("⚠️ WARNING: Still only ~1308 words - Playwright may not have triggered")
    print("=" * 80)

if __name__ == "__main__":
    test_katiecouric_scraping()
