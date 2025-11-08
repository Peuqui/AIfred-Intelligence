#!/usr/bin/env python3
"""
Vector Cache V2 - Manual Test Script

Tests the vector cache worker thread implementation
without running the full Reflex app.
"""

import asyncio
import sys
import time


async def test_vector_cache():
    """Test vector cache v2 functionality"""

    print("=" * 60)
    print("Vector Cache V2 - Test Script")
    print("=" * 60)
    print()

    # Test 1: Import
    print("Test 1: Importing vector_cache_v2...")
    try:
        from aifred.lib.vector_cache_v2 import (
            get_worker,
            query_cache_async,
            add_to_cache_async,
            get_cache_stats_async
        )
        print("✅ Import successful")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return

    print()

    # Test 2: Worker initialization
    print("Test 2: Initializing worker thread...")
    try:
        start = time.time()
        worker = get_worker(persist_directory="./aifred_vector_cache")
        init_time = (time.time() - start) * 1000
        print(f"✅ Worker initialized in {init_time:.1f}ms")

        # Give worker thread time to start
        await asyncio.sleep(0.5)

    except Exception as e:
        print(f"❌ Worker initialization failed: {e}")
        return

    print()

    # Test 3: Cache stats
    print("Test 3: Getting cache statistics...")
    try:
        start = time.time()
        stats = await get_cache_stats_async()
        stats_time = (time.time() - start) * 1000
        print(f"✅ Stats retrieved in {stats_time:.1f}ms")
        print(f"   Total entries: {stats['total_entries']}")
        print(f"   Path: {stats['persist_path']}")
    except Exception as e:
        print(f"❌ Stats failed: {e}")

    print()

    # Test 4: Cache query (should be empty or miss)
    print("Test 4: Querying cache (expecting MISS)...")
    try:
        start = time.time()
        result = await query_cache_async(
            user_query="What is the weather in Berlin today?",
            n_results=1
        )
        query_time = (time.time() - start) * 1000

        print(f"✅ Query completed in {query_time:.1f}ms")
        print(f"   Source: {result.get('source')}")
        print(f"   Confidence: {result.get('confidence')}")
        print(f"   Distance: {result.get('distance', 'N/A')}")

        if query_time > 100:
            print(f"⚠️  Warning: Query took > 100ms (blocking?)")

    except Exception as e:
        print(f"❌ Query failed: {e}")

    print()

    # Test 5: Add to cache
    print("Test 5: Adding entry to cache...")
    try:
        start = time.time()
        result = await add_to_cache_async(
            query="What is the weather in Berlin today?",
            answer="The weather in Berlin today is sunny with 18°C.",
            sources=[
                {'url': 'https://weather.com/berlin', 'title': 'Weather Berlin'},
                {'url': 'https://meteo.com/berlin', 'title': 'Berlin Weather'}
            ],
            metadata={'test': True, 'mode': 'test'}
        )
        add_time = (time.time() - start) * 1000

        if result.get('success'):
            print(f"✅ Entry added in {add_time:.1f}ms")
            print(f"   Total entries now: {result.get('total_entries')}")
        else:
            print(f"❌ Add failed: {result.get('error')}")

    except Exception as e:
        print(f"❌ Add failed: {e}")

    print()

    # Test 6: Query again (should HIT now)
    print("Test 6: Querying cache (expecting HIT)...")
    try:
        start = time.time()
        result = await query_cache_async(
            user_query="What is the weather in Berlin?",  # Similar query
            n_results=1
        )
        query_time = (time.time() - start) * 1000

        print(f"✅ Query completed in {query_time:.1f}ms")
        print(f"   Source: {result.get('source')}")
        print(f"   Confidence: {result.get('confidence')}")
        print(f"   Distance: {result.get('distance', 'N/A'):.3f}")

        if result.get('source') == 'CACHE':
            print(f"   Answer preview: {result.get('answer', '')[:100]}...")
            print()
            print("🎉 SUCCESS! Vector cache is working!")
        else:
            print("⚠️  Expected CACHE HIT but got MISS")
            print("   (This might be OK if distance threshold is strict)")

    except Exception as e:
        print(f"❌ Query failed: {e}")

    print()
    print("=" * 60)
    print("Test completed!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(test_vector_cache())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
