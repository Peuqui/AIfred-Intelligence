"""
Vector Cache - ChromaDB Server Mode (Thread-Safe)

ARCHITECTURE:
- ChromaDB runs as Docker container (docker-compose.yml)
- AIfred connects via HTTP (chromadb.HttpClient)
- NO worker threads needed - server handles everything
- Thread-safe by design (HTTP is stateless)

BENEFITS:
- ✅ Thread-safe (HTTP client is thread-safe)
- ✅ No file locks (server manages SQLite)
- ✅ Portable (data in ./aifred_vector_cache)
- ✅ Scalable (server can be moved to separate host)
- ✅ Simple (no complex worker thread management)

USAGE:
    # Start ChromaDB server:
    docker-compose up -d chromadb

    # In your code:
    from aifred.lib.vector_cache import get_cache

    cache = get_cache()
    result = await cache.query("What is the weather?")
"""

import time
import chromadb
from chromadb.config import Settings
import asyncio
from typing import Dict, List, Optional
from .logging_utils import log_message
from .config import (
    CACHE_DISTANCE_HIGH,
    CACHE_DISTANCE_MEDIUM,
    CACHE_DISTANCE_DUPLICATE,
    CACHE_DISTANCE_RAG,
    CACHE_TIME_THRESHOLD
)
from datetime import datetime
import uuid


class VectorCache:
    """
    Vector Cache using ChromaDB in Client-Server Mode

    Connects to ChromaDB Docker container via HTTP.
    All operations are thread-safe by design.

    Decision Thresholds (Cosine Distance):
    - < 0.5:     HIGH confidence   → Return cached answer
    - 0.5-0.85:  MEDIUM confidence → Return with warning
    - > 0.85:    LOW confidence    → Web search required
    """

    def __init__(self, host: str = "localhost", port: int = 8000):
        """
        Initialize Vector Cache with ChromaDB Server

        Args:
            host: ChromaDB server host (default: localhost for Docker)
            port: ChromaDB server port (default: 8000)

        Raises:
            ConnectionError: If ChromaDB server is not running
        """
        try:
            # HttpClient is thread-safe and can be used from async code
            self.client = chromadb.HttpClient(
                host=host,
                port=port,
                settings=Settings(anonymized_telemetry=False)
            )

            # Test connection with heartbeat
            self.client.heartbeat()

            # Get or create collection
            self.collection = self.client.get_or_create_collection(
                name="research_cache",
                metadata={"description": "AIfred web research results with semantic search"}
            )

            count = self.collection.count()
            log_message(f"✅ Vector Cache connected to ChromaDB server: {count} entries")

        except Exception as e:
            log_message(f"❌ ChromaDB Server connection failed: {e}")
            log_message("💡 Hint: Start ChromaDB with: docker-compose up -d chromadb")
            raise ConnectionError(
                f"Could not connect to ChromaDB server at {host}:{port}. "
                "Make sure Docker container is running: docker-compose up -d chromadb"
            ) from e

    async def query(self, user_query: str, n_results: int = 1) -> Dict:
        """
        Query cache with semantic similarity search

        Args:
            user_query: User's question
            n_results: Number of similar results to retrieve (default: 1)

        Returns:
            Dict with keys:
            - source: 'CACHE' or 'CACHE_MISS'
            - confidence: 'high', 'medium', or 'low'
            - distance: Cosine distance score (0.0 = identical, 2.0 = opposite)
            - answer: Cached answer (if found)
            - metadata: Source metadata (if found)
            - query_time_ms: Query execution time in milliseconds
        """
        start_time = time.time()

        # Run blocking HTTP call in thread pool
        # HttpClient calls are fast (typically <50ms), so to_thread overhead is acceptable
        result = await asyncio.to_thread(
            self._query_sync,
            user_query,
            n_results
        )

        result['query_time_ms'] = (time.time() - start_time) * 1000
        return result

    def _query_sync(self, user_query: str, n_results: int) -> Dict:
        """
        Synchronous query implementation (called in thread pool)

        This method runs in asyncio's thread pool, not in event loop.
        Safe to make blocking HTTP calls here.
        """
        # Check if cache is empty
        if self.collection.count() == 0:
            log_message("💾 Vector Cache empty → Web search required")
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0
            }

        # Perform semantic similarity search
        results = self.collection.query(
            query_texts=[user_query],
            n_results=n_results,
            include=['distances', 'documents', 'metadatas']
        )

        # No results found
        if not results['ids'][0]:
            log_message("❌ Vector Cache miss: No similar queries found")
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0
            }

        # Get best match
        distance = results['distances'][0][0]
        document = results['documents'][0][0]
        metadata = results['metadatas'][0][0]

        # Determine confidence based on distance thresholds (from config)
        if distance < CACHE_DISTANCE_HIGH:
            # Direct cache hit - use cached answer
            confidence = 'high'
            source = 'CACHE'
            log_message(f"✅ Vector Cache HIT: distance={distance:.3f} (HIGH confidence, < {CACHE_DISTANCE_HIGH})")
        else:
            # No direct hit - will trigger RAG check
            confidence = 'low'
            source = 'CACHE_MISS'
            log_message(f"❌ Vector Cache miss: distance={distance:.3f} (>= {CACHE_DISTANCE_HIGH}) → Will check RAG")

        # Extract answer from metadata (stored there to keep embedding focused on query)
        answer = None
        if source == 'CACHE' and metadata:
            answer = metadata.get('answer')

        return {
            'source': source,
            'confidence': confidence,
            'distance': distance,
            'answer': answer,
            'metadata': metadata if source == 'CACHE' else None
        }

    async def query_newest(self, user_query: str, n_results: int = 5) -> Dict:
        """
        Query cache and return the NEWEST match (by timestamp)

        This is useful for time-based cache checks where we want the most recent
        entry, not just the best similarity match.

        Args:
            user_query: User's question
            n_results: Number of similar results to check (default: 5)

        Returns:
            Dict with keys:
            - source: 'CACHE' or 'CACHE_MISS'
            - confidence: 'high', 'medium', or 'low'
            - distance: Cosine distance score of the newest match
            - answer: Cached answer (if found)
            - metadata: Source metadata (if found)
            - query_time_ms: Query execution time in milliseconds
        """
        start_time = time.time()

        result = await asyncio.to_thread(
            self._query_newest_sync,
            user_query,
            n_results
        )

        result['query_time_ms'] = (time.time() - start_time) * 1000
        return result

    def _query_newest_sync(self, user_query: str, n_results: int) -> Dict:
        """
        Synchronous query_newest implementation (called in thread pool)

        Retrieves multiple similar results and returns the newest one (by timestamp).
        """
        # Check if cache is empty
        if self.collection.count() == 0:
            log_message("💾 Vector Cache empty → Web search required")
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0
            }

        # Perform semantic similarity search (get multiple results)
        results = self.collection.query(
            query_texts=[user_query],
            n_results=min(n_results, self.collection.count()),
            include=['distances', 'documents', 'metadatas']
        )

        # No results found
        if not results['ids'][0]:
            log_message("❌ Vector Cache miss: No similar queries found")
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0
            }

        # Find newest entry among matches with configured distance threshold
        from datetime import datetime
        newest_entry = None
        newest_time = None

        for i, distance in enumerate(results['distances'][0]):
            # Only consider similar queries (distance < CACHE_DISTANCE_DUPLICATE from config)
            if distance < CACHE_DISTANCE_DUPLICATE:
                metadata = results['metadatas'][0][i]
                timestamp_str = metadata.get('timestamp')

                if timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if newest_time is None or timestamp > newest_time:
                        newest_time = timestamp
                        newest_entry = {
                            'distance': distance,
                            'document': results['documents'][0][i],
                            'metadata': metadata
                        }

        # If we found a near-duplicate, return the newest one
        if newest_entry:
            distance = newest_entry['distance']
            metadata = newest_entry['metadata']

            # Determine confidence based on distance thresholds
            if distance < 0.5:
                confidence = 'high'
                source = 'CACHE'
                log_message(f"✅ Vector Cache HIT (newest): distance={distance:.3f} (HIGH confidence)")
            elif distance < 0.85:
                confidence = 'medium'
                source = 'CACHE'
                log_message(f"⚠️  Vector Cache HIT (newest): distance={distance:.3f} (MEDIUM confidence)")
            else:
                confidence = 'low'
                source = 'CACHE_MISS'
                log_message(f"❌ Vector Cache miss: distance={distance:.3f} (too high)")

            # Extract answer from metadata
            answer = metadata.get('answer') if source == 'CACHE' else None

            return {
                'source': source,
                'confidence': confidence,
                'distance': distance,
                'answer': answer,
                'metadata': metadata if source == 'CACHE' else None
            }
        else:
            # No similar queries found (distance >= CACHE_DISTANCE_DUPLICATE)
            log_message(f"❌ Vector Cache: No similar queries found (all distances >= {CACHE_DISTANCE_DUPLICATE})")
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0
            }

    async def add(
        self,
        query: str,
        answer: str,
        sources: List[Dict],
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Add new entry to cache (auto-learning from web search)

        Includes duplicate detection: If a very similar query already exists
        (distance < 0.1), the entry is skipped to prevent duplicates.

        Args:
            query: User's question
            answer: Generated answer (full text, no truncation)
            sources: List of scraped sources with 'url' keys
            metadata: Additional metadata (optional)

        Returns:
            Dict with keys:
            - success: True if added successfully
            - duplicate: True if skipped due to duplicate
            - total_entries: Total cache entries after addition
            - error: Error message (if failed)
        """
        # Duplicate check: Query if very similar entry exists
        # Use query_newest to check for recent duplicates (from config)
        existing = await self.query_newest(query, n_results=5)

        if existing['source'] == 'CACHE' and existing['distance'] < CACHE_DISTANCE_DUPLICATE:
            # Check if duplicate is recent (time threshold from config)
            from datetime import datetime
            timestamp = existing['metadata'].get('timestamp')
            if timestamp:
                cache_time = datetime.fromisoformat(timestamp)
                age_seconds = (datetime.now() - cache_time).total_seconds()

                if age_seconds < CACHE_TIME_THRESHOLD:
                    # Recent duplicate - skip save
                    log_message(f"⚠️ Duplicate detected (distance={existing['distance']:.4f}, age={age_seconds:.0f}s), skipping save")
                    return {
                        'success': True,
                        'duplicate': True,
                        'total_entries': self.collection.count()
                    }
                else:
                    # Old duplicate (>5min) - update existing entry
                    log_message(f"✓ Old duplicate found (age={age_seconds/60:.1f}min), updating entry")
                    # Delete old entry and save new one (ChromaDB has no "update" operation)
                    old_id = existing['metadata'].get('id')
                    if old_id:
                        return await asyncio.to_thread(
                            self._update_sync,
                            old_id, query, answer, sources, metadata
                        )
                    else:
                        # No ID found, fallback to save as new entry
                        log_message(f"⚠️ No ID in old entry, saving as new entry")
            else:
                # No timestamp - treat as duplicate to be safe
                log_message(f"⚠️ Duplicate detected (distance={existing['distance']:.4f}, no timestamp), skipping save")
                return {
                    'success': True,
                    'duplicate': True,
                    'total_entries': self.collection.count()
                }

        # No duplicate, proceed with save
        return await asyncio.to_thread(
            self._add_sync,
            query, answer, sources, metadata
        )

    def _add_sync(
        self,
        query: str,
        answer: str,
        sources: List[Dict],
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Synchronous add implementation (called in thread pool)
        """
        # Build metadata (ChromaDB only supports str, int, float, bool)
        # Store answer in metadata since it can be large
        source_urls = [s.get('url', 'N/A')[:100] for s in sources[:3]]  # Max 3 URLs

        # Generate unique ID
        entry_id = str(uuid.uuid4())

        cache_metadata = {
            'id': entry_id,  # Store ID in metadata for later updates
            'timestamp': datetime.now().isoformat(),
            'num_sources': len(sources),
            'source_urls': ', '.join(source_urls),
            'answer': answer,  # Store full answer in metadata
            **(metadata or {})
        }

        # Store ONLY the query as document (for embedding/similarity search)
        # The answer is stored in metadata and retrieved later
        document = query

        try:
            # Add to ChromaDB
            self.collection.add(
                documents=[document],
                metadatas=[cache_metadata],
                ids=[entry_id]
            )

            total = self.collection.count()
            log_message(f"💾 Vector Cache: Added entry for '{query[:50]}...'")
            log_message(f"   Total entries: {total}")

            return {
                'success': True,
                'total_entries': total
            }

        except Exception as e:
            log_message(f"⚠️  Vector Cache add failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _update_sync(
        self,
        old_id: str,
        query: str,
        answer: str,
        sources: List[Dict],
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Update existing cache entry (delete old, add new with same query)
        """
        try:
            # Delete old entry
            self.collection.delete(ids=[old_id])
            log_message(f"🗑️ Deleted old cache entry (id={old_id})")

            # Add new entry with updated data
            return self._add_sync(query, answer, sources, metadata)

        except Exception as e:
            log_message(f"⚠️ Vector Cache update failed: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def get_stats(self) -> Dict:
        """
        Get cache statistics

        Returns:
            Dict with keys:
            - total_entries: Number of cached entries
            - server_url: ChromaDB server URL
        """
        return await asyncio.to_thread(self._get_stats_sync)

    def _get_stats_sync(self) -> Dict:
        """Synchronous stats implementation"""
        # Get settings via public API
        settings = self.client.get_settings()
        host = getattr(settings, 'chroma_server_host', 'localhost')
        port = getattr(settings, 'chroma_server_http_port', 8000)

        return {
            'total_entries': self.collection.count(),
            'server_url': f"http://{host}:{port}"
        }

    async def clear(self) -> Dict:
        """
        Clear all cache entries

        Returns:
            Dict with keys:
            - success: True if cleared successfully
            - error: Error message (if failed)
        """
        return await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> Dict:
        """Synchronous clear implementation"""
        try:
            self.client.delete_collection("research_cache")
            self.collection = self.client.get_or_create_collection(
                name="research_cache",
                metadata={"description": "AIfred web research results with semantic search"}
            )
            log_message("🗑️  Vector Cache cleared")
            return {'success': True}
        except Exception as e:
            log_message(f"⚠️  Vector Cache clear failed: {e}")
            return {'success': False, 'error': str(e)}

    async def query_for_rag(self, user_query: str, n_results: int = 5) -> List[Dict]:
        """
        Query cache for RAG (Retrieval-Augmented Generation) purposes.
        Returns multiple results in the RAG distance range (0.5 - 1.2) that might be
        relevant as context, not as direct answers.

        Args:
            user_query: User's current question
            n_results: Max number of potential context entries to return

        Returns:
            List of dicts with: {
                'query': original cached query,
                'answer': cached answer,
                'distance': semantic distance,
                'metadata': cache metadata
            }
        """
        start_time = time.time()

        # Run in thread pool
        results = await asyncio.to_thread(
            self._query_for_rag_sync,
            user_query,
            n_results
        )

        query_time_ms = (time.time() - start_time) * 1000
        log_message(f"📊 RAG query completed in {query_time_ms:.1f}ms, found {len(results)} candidates")

        return results

    def _query_for_rag_sync(self, user_query: str, n_results: int) -> List[Dict]:
        """
        Synchronous RAG query implementation (called in thread pool)
        """
        # Check if cache is empty
        if self.collection.count() == 0:
            return []

        # Perform semantic similarity search
        results = self.collection.query(
            query_texts=[user_query],
            n_results=n_results,
            include=['distances', 'documents', 'metadatas']
        )

        # No results found
        if not results['ids'][0]:
            return []

        # Filter results in RAG range (CACHE_DISTANCE_HIGH to CACHE_DISTANCE_RAG)
        # Start from CACHE_DISTANCE_HIGH because anything below that is a direct cache hit
        rag_candidates = []

        for i, (distance, document, metadata) in enumerate(zip(
            results['distances'][0],
            results['documents'][0],
            results['metadatas'][0]
        )):
            # Only include results in RAG range (not direct hits, but related)
            if CACHE_DISTANCE_HIGH <= distance < CACHE_DISTANCE_RAG:
                rag_candidates.append({
                    'query': document,  # Original cached query
                    'answer': metadata.get('answer', ''),
                    'distance': distance,
                    'metadata': metadata
                })

        if rag_candidates:
            log_message(f"🎯 Found {len(rag_candidates)} RAG candidates (d: {CACHE_DISTANCE_HIGH}-{CACHE_DISTANCE_RAG})")
        else:
            log_message(f"❌ No RAG candidates in range {CACHE_DISTANCE_HIGH}-{CACHE_DISTANCE_RAG}")

        return rag_candidates


# Global cache instance (singleton)
_cache_instance: Optional[VectorCache] = None


def get_cache(host: str = "localhost", port: int = 8000) -> VectorCache:
    """
    Get or create global cache instance (singleton pattern)

    Args:
        host: ChromaDB server host
        port: ChromaDB server port

    Returns:
        VectorCache instance

    Raises:
        ConnectionError: If ChromaDB server is not available
    """
    global _cache_instance

    if _cache_instance is None:
        _cache_instance = VectorCache(host=host, port=port)

    return _cache_instance


def reset_cache_instance():
    """
    Reset global cache instance (for testing/restart)
    """
    global _cache_instance
    _cache_instance = None
