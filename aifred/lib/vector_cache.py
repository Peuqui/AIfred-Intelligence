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

import chromadb
from chromadb.config import Settings
from chromadb.api.types import Documents, Embeddings, EmbeddingFunction
from chromadb.errors import ChromaError
from .timer import Timer
import asyncio
from typing import Dict, List, Optional
import numpy as np
from .logging_utils import log_message
from .config import (
    CACHE_DISTANCE_HIGH,
    CACHE_DISTANCE_DUPLICATE,
    TTL_HOURS,
    DEFAULT_OLLAMA_URL
)
from datetime import datetime, timedelta
import uuid

# Ollama Embedding Configuration
OLLAMA_EMBEDDING_MODEL = "bge-m3"
OLLAMA_HOST = DEFAULT_OLLAMA_URL


class OllamaEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Ollama Embedding Function with configurable CPU/GPU inference.

    Based on nomic-embed-text-v2-moe (768 dimensions, multilingual).
    GPU mode: ~600MB VRAM, significantly faster for bulk embeddings.
    CPU mode: No VRAM usage, keeps GPU free for LLM inference.
    """

    def __init__(
        self,
        model_name: str = OLLAMA_EMBEDDING_MODEL,
        host: str = OLLAMA_HOST,
        timeout: Optional[int] = None,  # None = use dynamic timeout per call
        mode: str = "query",            # "index" → GPU + warm cache, "query" → CPU
    ):
        """
        Args:
            mode: "index" loads the model on GPU with a generous keep_alive
                  so a bulk-index run stays warm across many chunks. "query"
                  forces CPU (num_gpu=0) and keep_alive=0 — single-shot
                  queries do not warrant GPU residency that competes with
                  the active LLM. Default "query" is the safe fall-back.
        """
        try:
            from ollama import Client  # noqa: F401
        except ImportError:
            raise ValueError(
                "The ollama python package is not installed. "
                "Install with: pip install ollama"
            )

        self.model_name = model_name
        self.host = host
        self._timeout_override = timeout
        self.mode = mode

    def __call__(self, input: Documents) -> Embeddings:
        """Generate embeddings — GPU for index mode, CPU for query mode.

        Timeout scales with batch size — large docs (e.g. the Goldschmidt
        Talmud Sanhedrin at ~1.4 MB) easily push a single embed call past
        any fixed limit, especially on CPU. We allocate roughly 2 s per
        input chunk on CPU and 0.3 s on GPU, with a 120 s floor for tiny
        calls. A real hang still surfaces (the client raises ReadTimeout)
        but normal indexing runs through.
        """
        from ollama import Client
        from .config import EMBEDDING_USE_GPU

        # Index mode honours EMBEDDING_USE_GPU; query mode is always CPU.
        on_gpu = self.mode == "index" and EMBEDDING_USE_GPU
        options = {} if on_gpu else {"num_gpu": 0}

        if self._timeout_override is not None:
            dyn_timeout = self._timeout_override
        else:
            per_item = 0.3 if on_gpu else 2.0
            dyn_timeout = max(120, int(len(input) * per_item))

        # GPU index mode: 1 min keep_alive — long enough that the bulk-
        # index loop doesn't reload between chunks (chunks come back-to-
        # back), short enough that a finished bulk run releases VRAM
        # quickly so the LLM can reclaim it.
        # CPU query mode: 30 min keep_alive — RAM is cheap, model load
        # on CPU is comparatively slow (bge-m3 is ~1 GB on disk), and a
        # warm model means user queries get answered without re-load
        # latency between chats.
        keep_alive = 60 if on_gpu else 1800

        client = Client(host=self.host, timeout=dyn_timeout)
        response = client.embed(
            model=self.model_name,
            input=input,
            options=options,
            keep_alive=keep_alive,
        )
        return [
            np.array(embedding, dtype=np.float32)
            for embedding in response["embeddings"]
        ]


# Backward compatibility alias
OllamaCPUEmbeddingFunction = OllamaEmbeddingFunction


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

            # Embedding function (currently bge-m3: 1024 dim, multilingual,
            # 8192 token context). Query-mode → CPU + keep_alive=0; the web
            # research cache writes a single entry at a time, no bulk flow,
            # so GPU residency would only steal VRAM from the active LLM.
            self.embedding_function = OllamaEmbeddingFunction(
                model_name=OLLAMA_EMBEDDING_MODEL,
                host=OLLAMA_HOST,
                mode="query",
            )

            # Get or create collection with Ollama embeddings
            # ChromaDB 1.4+ stores embedding function config persistently.
            # If the collection was created with a different function, recreate it.
            try:
                self.collection = self.client.get_or_create_collection(
                    name="research_cache",
                    metadata={
                        "description": "AIfred web research results with semantic search",
                        "embedding_model": OLLAMA_EMBEDDING_MODEL
                    },
                    embedding_function=self.embedding_function  # type: ignore[arg-type]
                )
            except ValueError:
                # Embedding function conflict - recreate collection
                log_message("♻️ Embedding function changed, recreating collection...")
                self.client.delete_collection("research_cache")
                self.collection = self.client.create_collection(
                    name="research_cache",
                    metadata={
                        "description": "AIfred web research results with semantic search",
                        "embedding_model": OLLAMA_EMBEDDING_MODEL
                    },
                    embedding_function=self.embedding_function  # type: ignore[arg-type]
                )

            count = self.collection.count()
            log_message(f"✅ Vector Cache connected (Ollama/{OLLAMA_EMBEDDING_MODEL}): {count} entries")

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
        timer = Timer()

        # Run blocking HTTP call in thread pool
        # HttpClient calls are fast (typically <50ms), so to_thread overhead is acceptable
        result = await asyncio.to_thread(
            self._query_sync,
            user_query,
            n_results
        )

        result['query_time_ms'] = timer.elapsed_ms()
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
        assert results['distances'] is not None
        assert results['metadatas'] is not None
        distance = results['distances'][0][0]
        metadata = results['metadatas'][0][0]

        # Check if entry has expired (TTL-based expiry check)
        expires_at_str = metadata.get('expires_at')
        if expires_at_str and expires_at_str != 'None':
            try:
                expiry_time = datetime.fromisoformat(str(expires_at_str))
                if datetime.now() > expiry_time:
                    # EXPIRED! Delete entry and return cache miss
                    entry_id = metadata.get('id')
                    if entry_id:
                        self.collection.delete(ids=[str(entry_id)])
                        volatility = metadata.get('volatility', 'UNKNOWN')
                        log_message(f"🗑️ Cache expired ({volatility} TTL), deleted: {entry_id}")

                    return {
                        'source': 'CACHE_MISS',
                        'confidence': 'low',
                        'distance': 1.0
                    }
            except (ValueError, TypeError) as e:
                log_message(f"⚠️ Invalid expires_at format: {expires_at_str}, error: {e}")

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

            # Validate answer is not empty - treat empty answers as cache miss
            if not answer or (isinstance(answer, str) and not answer.strip()):
                log_message(f"⚠️ Vector Cache HIT but answer is empty (distance={distance:.3f}) → Treating as miss")
                return {
                    'source': 'CACHE_MISS',
                    'confidence': 'low',
                    'distance': distance
                }

        # Parse sources and failed_sources from JSON if available
        cached_sources = []
        failed_sources = []
        if source == 'CACHE' and metadata:
            import json
            # Parse successful sources
            sources_json = metadata.get('sources_json', '')
            if sources_json:
                try:
                    cached_sources = json.loads(str(sources_json))
                except (json.JSONDecodeError, TypeError) as e:
                    log_message(f"⚠️ Corrupt sources_json in cache: {e}")
                    cached_sources = []
            # Parse failed sources
            failed_json = metadata.get('failed_sources_json', '')
            if failed_json:
                try:
                    failed_sources = json.loads(str(failed_json))
                except (json.JSONDecodeError, TypeError) as e:
                    log_message(f"⚠️ Corrupt failed_sources_json in cache: {e}")
                    failed_sources = []

        return {
            'source': source,
            'confidence': confidence,
            'distance': distance,
            'answer': answer,
            'metadata': metadata if source == 'CACHE' else None,
            'cached_sources': cached_sources,  # Parsed list of successful source URLs
            'failed_sources': failed_sources  # Parsed list of failed URLs
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
        timer = Timer()

        result = await asyncio.to_thread(
            self._query_newest_sync,
            user_query,
            n_results
        )

        result['query_time_ms'] = timer.elapsed_ms()
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

        assert results['distances'] is not None
        assert results['metadatas'] is not None
        assert results['documents'] is not None
        for i, distance in enumerate(results['distances'][0]):
            # Only consider similar queries (distance < CACHE_DISTANCE_DUPLICATE from config)
            if distance < CACHE_DISTANCE_DUPLICATE:
                metadata = results['metadatas'][0][i]
                timestamp_str = metadata.get('timestamp')

                if timestamp_str:
                    timestamp = datetime.fromisoformat(str(timestamp_str))
                    if newest_time is None or timestamp > newest_time:
                        newest_time = timestamp
                        newest_entry = {
                            'distance': distance,
                            'document': results['documents'][0][i],
                            'metadata': metadata
                        }

        # If we found a near-duplicate, return the newest one
        if newest_entry:
            distance = float(newest_entry['distance'])  # type: ignore[arg-type]
            metadata = newest_entry['metadata']  # type: ignore[assignment]

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

            # Validate answer is not empty - treat empty answers as cache miss
            if source == 'CACHE' and (not answer or (isinstance(answer, str) and not answer.strip())):
                log_message(f"⚠️ Vector Cache HIT (newest) but answer is empty (distance={distance:.3f}) → Treating as miss")
                return {
                    'source': 'CACHE_MISS',
                    'confidence': 'low',
                    'distance': distance
                }

            # Parse sources and failed_sources from JSON if available
            cached_sources = []
            failed_sources = []
            if source == 'CACHE':
                import json
                # Parse successful sources
                sources_json = metadata.get('sources_json', '')
                if sources_json:
                    try:
                        cached_sources = json.loads(str(sources_json))
                    except (json.JSONDecodeError, TypeError) as e:
                        log_message(f"⚠️ Corrupt sources_json in cache: {e}")
                        cached_sources = []
                # Parse failed sources
                failed_json = metadata.get('failed_sources_json', '')
                if failed_json:
                    try:
                        failed_sources = json.loads(str(failed_json))
                    except (json.JSONDecodeError, TypeError) as e:
                        log_message(f"⚠️ Corrupt failed_sources_json in cache: {e}")
                        failed_sources = []

            return {
                'source': source,
                'confidence': confidence,
                'distance': distance,
                'answer': answer,
                'metadata': metadata if source == 'CACHE' else None,
                'cached_sources': cached_sources,  # Parsed list of successful source URLs
                'failed_sources': failed_sources  # Parsed list of failed URLs
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
        failed_sources: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Add new entry to cache (auto-learning from web search)

        Includes duplicate detection: If a semantically similar query already exists
        (distance < CACHE_DISTANCE_DUPLICATE), the old entry is replaced with the new one.
        This ensures the latest research results are always used.

        Args:
            query: User's question
            answer: Generated answer (full text, no truncation)
            sources: List of scraped sources with 'url' keys
            failed_sources: List of failed scraping attempts with 'url' and 'error' keys
            metadata: Additional metadata (optional)

        Returns:
            Dict with keys:
            - success: True if added/updated successfully
            - duplicate: True if updated existing entry
            - total_entries: Total cache entries after addition/update
            - error: Error message (if failed)
        """
        failed_sources = failed_sources or []

        # NOCACHE: Skip storage for volatile data (weather, live scores, etc.)
        volatility = (metadata or {}).get('volatility', 'PERMANENT')
        if volatility == 'NOCACHE':
            log_message(f"🚫 Vector Cache: Skipping storage (volatility=NOCACHE) for '{query[:50]}...'")
            return {
                'success': True,
                'skipped': True,
                'reason': 'NOCACHE volatility'
            }

        # Duplicate check: Query if very similar entry exists
        # Use query_newest to check for semantic duplicates (from config)
        existing = await self.query_newest(query, n_results=5)

        if existing['source'] == 'CACHE' and existing['distance'] < CACHE_DISTANCE_DUPLICATE:
            # Semantic duplicate found - ALWAYS update (replace old with new)
            # This ensures the latest research results are always used
            log_message(f"♻️ Duplicate detected (distance={existing['distance']:.4f}), updating with fresh data...")

            old_id = existing['metadata'].get('id')
            if old_id:
                # Delete old entry and save new one (ChromaDB has no "update" operation)
                return await asyncio.to_thread(
                    self._update_sync,
                    old_id, query, answer, sources, failed_sources, metadata
                )
            else:
                # No ID found, fallback to save as new entry
                log_message("⚠️ No ID in old entry, saving as new entry")
                # Fall through to normal save below

        # No duplicate, proceed with save
        return await asyncio.to_thread(
            self._add_sync,
            query, answer, sources, failed_sources, metadata
        )

    def _add_sync(
        self,
        query: str,
        answer: str,
        sources: List[Dict],
        failed_sources: List[Dict],
        metadata: Optional[Dict] = None
    ) -> Dict:
        """
        Synchronous add implementation (called in thread pool)
        """
        # Build metadata (ChromaDB only supports str, int, float, bool)
        # Store answer in metadata since it can be large
        source_urls = [s.get('url', 'N/A')[:100] for s in sources[:3]]  # Max 3 URLs (legacy format)

        # Generate unique ID
        entry_id = str(uuid.uuid4())

        # Calculate TTL expiry timestamp
        volatility = (metadata or {}).get('volatility', 'PERMANENT')
        ttl_hours = TTL_HOURS.get(volatility)

        if ttl_hours is not None:
            expires_at = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
        else:
            expires_at = None  # PERMANENT = no expiry

        # Build sources JSON (store full info for UI display)
        import json
        sources_data = [
            {'url': s.get('url', ''), 'title': s.get('title', '')}
            for s in sources[:10]  # Max 10 successful sources
        ]
        failed_sources_data = [
            {'url': f.get('url', ''), 'error': f.get('error', 'Unknown')}
            for f in failed_sources[:5]  # Max 5 failed sources
        ]

        cache_metadata = {
            'id': entry_id,  # Store ID in metadata for later updates
            'timestamp': datetime.now().isoformat(),
            'volatility': volatility,  # DAILY/WEEKLY/MONTHLY/PERMANENT
            'expires_at': expires_at or 'None',  # ISO timestamp or 'None'
            'num_sources': len(sources),
            'source_urls': ', '.join(source_urls),  # Legacy: comma-separated (truncated)
            'sources_json': json.dumps(sources_data) if sources_data else '',  # NEW: Full JSON
            'num_failed': len(failed_sources),
            'failed_sources_json': json.dumps(failed_sources_data) if failed_sources_data else '',
            'answer': answer,  # Store full answer in metadata
            'mode': (metadata or {}).get('mode', 'unknown')
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

        except (ChromaError, ConnectionError, OSError) as e:
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
        failed_sources: List[Dict],
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
            return self._add_sync(query, answer, sources, failed_sources, metadata)

        except (ChromaError, ConnectionError, OSError) as e:
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
        except (ChromaError, ConnectionError, OSError) as e:
            log_message(f"⚠️  Vector Cache clear failed: {e}")
            return {'success': False, 'error': str(e)}

    async def delete_expired_entries(self) -> int:
        """
        Delete all expired cache entries.
        Returns number of deleted entries.

        This method is called by:
        - Background cleanup task (every 12 hours)
        - Startup cleanup (on server initialization)
        """
        return await asyncio.to_thread(self._delete_expired_sync)

    def _delete_expired_sync(self) -> int:
        """
        Synchronous implementation of delete_expired_entries.

        Note: ChromaDB doesn't support WHERE clause with time comparison in get(),
        so we must fetch all entries and filter manually. Performance is O(n) but
        acceptable for <10,000 entries (~100-500ms).

        Future optimization: If ChromaDB adds indexed metadata queries, use:
        expired = self.collection.get(where={"expires_at": {"$lt": now.isoformat()}})
        """
        try:
            # Get all entries with metadata (only IDs + metadata, no embeddings/documents)
            all_results = self.collection.get(
                include=['metadatas']  # Minimal data transfer
            )

            if not all_results or not all_results['ids']:
                return 0

            # Find expired entries (linear scan)
            now = datetime.now()
            expired_ids = []

            assert all_results['metadatas'] is not None
            for entry_id, metadata in zip(all_results['ids'], all_results['metadatas']):
                expires_at_str = metadata.get('expires_at')

                # Skip entries with no expiry (PERMANENT) or invalid format
                if not expires_at_str or expires_at_str == 'None':
                    continue

                try:
                    expiry_time = datetime.fromisoformat(str(expires_at_str))
                    if now > expiry_time:
                        expired_ids.append(entry_id)
                except (ValueError, TypeError):
                    # Invalid format, skip
                    continue

            # Batch delete (efficient)
            if expired_ids:
                self.collection.delete(ids=expired_ids)
                log_message(f"🗑️ Deleted {len(expired_ids)} expired cache entries (scanned {len(all_results['ids'])} total)")

            return len(expired_ids)

        except (ChromaError, ConnectionError, OSError) as e:
            log_message(f"⚠️ Error deleting expired entries: {e}")
            return 0


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


def format_ttl_hours(hours: float) -> str:
    """
    Format TTL hours into a human-readable string

    Args:
        hours: Number of hours

    Returns:
        Formatted string (e.g., "24h", "7d", "30d")
    """
    if hours < 24:
        return f"{int(hours)}h"
    elif hours < 24 * 7:
        days = hours / 24
        return f"{days:.0f}d" if days == int(days) else f"{days:.1f}d"
    else:
        days = hours / 24
        return f"{int(days)}d"


# ============================================================
# CACHE INITIALIZATION AND CLEANUP TASKS
# ============================================================

async def cleanup_expired_cache_task():
    """
    Background task: Runs every CACHE_CLEANUP_INTERVAL_HOURS to delete expired cache entries.
    Uses AsyncIO (not threading) for Reflex compatibility.
    """
    from .config import CACHE_CLEANUP_INTERVAL_HOURS
    from datetime import datetime as dt

    log_message(f"🗑️ Cache cleanup task started (interval: {CACHE_CLEANUP_INTERVAL_HOURS}h)")

    while True:
        try:
            # Wait for interval
            await asyncio.sleep(CACHE_CLEANUP_INTERVAL_HOURS * 3600)

            # Run cleanup
            cache = get_cache()
            deleted_count = await cache.delete_expired_entries()

            if deleted_count > 0:
                log_message(f"🗑️ Cache cleanup: {deleted_count} expired entries deleted at {dt.now().strftime('%H:%M:%S')}")

        except Exception as e:
            log_message(f"⚠️ Cache cleanup task error: {e}")
            # Continue running despite errors


def initialize_vector_cache():
    """
    Initialize Vector Cache (Server Mode)

    Connects to ChromaDB Docker container via HTTP.
    Thread-safe by design - no worker threads needed.

    Also starts:
    - Startup cleanup (if enabled)
    - Background cleanup task

    Returns:
        VectorCache instance or None on failure
    """
    import os
    from .config import CACHE_STARTUP_CLEANUP, CACHE_CLEANUP_INTERVAL_HOURS

    try:
        log_message(f"🚀 Vector Cache: Connecting to ChromaDB server (PID: {os.getpid()})")
        cache = get_cache()
        log_message("✅ Vector Cache: Connected successfully")

        # Startup cleanup if enabled
        if CACHE_STARTUP_CLEANUP:
            async def startup_cleanup():
                deleted_count = await cache.delete_expired_entries()
                if deleted_count > 0:
                    log_message(f"🗑️ Startup cleanup: {deleted_count} expired entries deleted")

            asyncio.create_task(startup_cleanup())

        # Start background cleanup task
        asyncio.create_task(cleanup_expired_cache_task())
        log_message(f"🗑️ Background cleanup task started (every {CACHE_CLEANUP_INTERVAL_HOURS}h)")

        return cache
    except (ChromaError, ConnectionError, OSError, ValueError) as e:
        log_message(f"⚠️ Vector Cache connection failed: {e}")
        log_message("💡 Make sure ChromaDB is running: docker-compose up -d chromadb")
        return None
