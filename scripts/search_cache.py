#!/usr/bin/env python3
"""
Search ChromaDB Vector Cache by semantic similarity

Usage:
    ./venv/bin/python scripts/search_cache.py "Python libraries"
    ./venv/bin/python scripts/search_cache.py "Wetter" --limit 5
"""
import sys
import argparse
from datetime import datetime
import asyncio

# Add parent directory to path for imports
sys.path.insert(0, '/home/mp/Projekte/AIfred-Intelligence')

async def main():
    parser = argparse.ArgumentParser(description='Search ChromaDB cache by similarity')
    parser.add_argument('query', type=str, help='Search query')
    parser.add_argument('--limit', '-l', type=int, default=3,
                       help='Number of results (default: 3)')
    parser.add_argument('--detailed', '-d', action='store_true',
                       help='Show detailed answer preview')
    args = parser.parse_args()

    # Import after argument parsing
    from aifred.lib.vector_cache import get_cache

    print(f"\n{'='*80}")
    print(f"🔍 Searching cache for: '{args.query}'")
    print(f"{'='*80}\n")

    try:
        cache = get_cache()

        # Perform query
        result = await cache.query(args.query, n_results=args.limit)

        if result['source'] == 'CACHE_MISS':
            print(f"❌ No results found (distance={result.get('distance', 'N/A')})")
            print("   Try a different query or increase --limit\n")
            return

        # Get multiple results
        import chromadb
        from chromadb.config import Settings

        client = chromadb.HttpClient(
            host="localhost",
            port=8000,
            settings=Settings(anonymized_telemetry=False)
        )
        collection = client.get_collection("research_cache")

        # Query with multiple results
        results = collection.query(
            query_texts=[args.query],
            n_results=args.limit,
            include=['distances', 'documents', 'metadatas']
        )

        # Display results
        for i, (distance, doc, meta) in enumerate(zip(
            results['distances'][0],
            results['documents'][0],
            results['metadatas'][0]
        ), 1):

            # Determine confidence
            if distance < 0.5:
                confidence = "🟢 HIGH"
            elif distance < 0.85:
                confidence = "🟡 MEDIUM"
            else:
                confidence = "🔴 LOW"

            timestamp = meta.get('timestamp', 'N/A')
            if timestamp != 'N/A':
                ts = datetime.fromisoformat(timestamp)
                age = (datetime.now() - ts).total_seconds()
                time_ago = f"{int(age/60)}min ago" if age < 3600 else f"{int(age/3600)}h ago"
            else:
                time_ago = 'N/A'

            print(f"[{i}] Distance: {distance:.4f} | Confidence: {confidence}")
            print(f"    Cached: {timestamp} ({time_ago})")
            print(f"    Query: {doc[:100]}{'...' if len(doc) > 100 else ''}")

            if args.detailed:
                answer = meta.get('answer', '')
                answer_preview = answer[:300] + "..." if len(answer) > 300 else answer
                print(f"    Answer: {answer_preview}")

            print()

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
