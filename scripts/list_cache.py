#!/usr/bin/env python3
"""
List all entries in ChromaDB Vector Cache

Usage:
    ./venv/bin/python scripts/list_cache.py
    ./venv/bin/python scripts/list_cache.py --detailed
"""
import sys
import argparse
from datetime import datetime
import chromadb
from chromadb.config import Settings

def format_time_ago(timestamp_str):
    """Convert ISO timestamp to human-readable 'X ago' format"""
    try:
        ts = datetime.fromisoformat(timestamp_str)
        delta = datetime.now() - ts

        seconds = delta.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            return f"{int(seconds/60)}min ago"
        elif seconds < 86400:
            return f"{int(seconds/3600)}h ago"
        else:
            return f"{int(seconds/86400)}d ago"
    except:
        return timestamp_str

def main():
    parser = argparse.ArgumentParser(description='List ChromaDB cache entries')
    parser.add_argument('--detailed', '-d', action='store_true',
                       help='Show detailed answer preview')
    parser.add_argument('--limit', '-l', type=int, default=None,
                       help='Limit number of entries to show')
    args = parser.parse_args()

    # Connect to ChromaDB
    try:
        client = chromadb.HttpClient(
            host="localhost",
            port=8000,
            settings=Settings(anonymized_telemetry=False)
        )
        client.heartbeat()  # Test connection
    except Exception as e:
        print(f"❌ Could not connect to ChromaDB: {e}")
        print("   Make sure ChromaDB is running: cd docker && docker-compose up -d chromadb")
        sys.exit(1)

    # Get collection
    try:
        collection = client.get_collection("research_cache")
    except Exception as e:
        print(f"❌ Collection 'research_cache' not found: {e}")
        sys.exit(1)

    # Get all entries
    count = collection.count()
    print(f"\n{'='*80}")
    print(f"📊 ChromaDB Vector Cache - {count} entries")
    print(f"{'='*80}\n")

    if count == 0:
        print("Cache is empty.\n")
        return

    # Fetch all data
    all_data = collection.get(include=['documents', 'metadatas'])

    # Limit if specified
    limit = args.limit if args.limit else count
    entries = list(zip(all_data['documents'], all_data['metadatas']))[:limit]

    # Print each entry
    for i, (doc, meta) in enumerate(entries, 1):
        timestamp = meta.get('timestamp', 'N/A')
        time_ago = format_time_ago(timestamp) if timestamp != 'N/A' else 'N/A'
        num_sources = meta.get('num_sources', 0)
        mode = meta.get('mode', 'unknown')

        print(f"[{i}] {timestamp} ({time_ago})")
        print(f"    Mode: {mode} | Sources: {num_sources}")
        print(f"    Query: {doc[:100]}{'...' if len(doc) > 100 else ''}")

        if args.detailed:
            answer = meta.get('answer', '')
            answer_preview = answer[:200] + "..." if len(answer) > 200 else answer
            print(f"    Answer: {answer_preview}")

            source_urls = meta.get('source_urls', '')
            if source_urls:
                print(f"    URLs: {source_urls}")

        print()

    if args.limit and count > args.limit:
        print(f"... and {count - args.limit} more entries")
        print(f"Use --limit {count} to show all\n")

if __name__ == "__main__":
    main()
