"""
Vector DB Cache V2 - Thread-Safe Implementation with Dedicated Worker

FIXED IMPLEMENTATION:
- Uses dedicated background worker thread (NOT asyncio.to_thread)
- ChromaDB operations run in separate thread to avoid blocking event loop
- Queue-based communication between async code and worker thread
- Proper thread lifecycle management

This fixes the issue where ChromaDB (not thread-safe) was blocking
the asyncio event loop and causing request cancellations.
"""

import time
import chromadb
from chromadb.config import Settings
from typing import Dict, List, Optional
from pathlib import Path
from .logging_utils import log_message
import threading
import queue
import uuid
from datetime import datetime


class VectorCacheWorker:
    """
    Dedicated worker thread for ChromaDB operations

    Runs completely isolated from asyncio event loop.
    Communication via thread-safe queues.
    """

    def __init__(self, persist_directory: str):
        self.persist_dir = Path(persist_directory)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # Thread-safe queues for communication
        self.request_queue = queue.Queue()
        self.response_queues = {}  # request_id -> response_queue

        # Worker thread
        self.worker_thread = None
        self.running = False

        # ChromaDB client (initialized in worker thread)
        self.client = None
        self.collection = None

    def start(self):
        """Start the worker thread"""
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return  # Already running

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        log_message("✅ VectorCache Worker Thread started")

    def stop(self):
        """Stop the worker thread"""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5.0)
        log_message("🛑 VectorCache Worker Thread stopped")

    def _worker_loop(self):
        """Main worker loop - runs in separate thread"""
        try:
            # Initialize ChromaDB in worker thread
            log_message(f"🔧 Worker: Initializing ChromaDB at {self.persist_dir}")
            self.client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )

            self.collection = self.client.get_or_create_collection(
                name="research_cache",
                metadata={"description": "Web research results with auto-learning"}
            )

            log_message(f"✅ Worker: ChromaDB initialized: {self.collection.count()} entries")

            # Process requests
            while self.running:
                try:
                    # Wait for request (timeout to check self.running periodically)
                    request = self.request_queue.get(timeout=1.0)

                    request_id = request['id']
                    operation = request['operation']
                    args = request.get('args', {})

                    # Process operation
                    try:
                        if operation == 'query':
                            result = self._handle_query(**args)
                        elif operation == 'add':
                            result = self._handle_add(**args)
                        elif operation == 'get_stats':
                            result = self._handle_get_stats()
                        elif operation == 'clear':
                            result = self._handle_clear()
                        else:
                            result = {'error': f"Unknown operation: {operation}"}
                    except Exception as e:
                        result = {'error': str(e)}

                    # Send response
                    if request_id in self.response_queues:
                        self.response_queues[request_id].put(result)

                except queue.Empty:
                    continue  # Timeout, check self.running and continue
                except Exception as e:
                    log_message(f"⚠️ Worker error: {e}")

        except Exception as e:
            log_message(f"❌ Worker initialization failed: {e}")
        finally:
            log_message("🛑 Worker loop ended")

    def _handle_query(self, user_query: str, n_results: int = 3) -> Dict:
        """Handle query operation (runs in worker thread)"""
        start_time = time.time()

        if self.collection.count() == 0:
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0,
                'query_time_ms': (time.time() - start_time) * 1000
            }

        results = self.collection.query(
            query_texts=[user_query],
            n_results=n_results,
            include=['distances', 'documents', 'metadatas']
        )

        query_time = (time.time() - start_time) * 1000

        if not results['ids'][0]:
            return {
                'source': 'CACHE_MISS',
                'confidence': 'low',
                'distance': 1.0,
                'query_time_ms': query_time
            }

        distance = results['distances'][0][0]
        document = results['documents'][0][0]
        metadata = results['metadatas'][0][0]

        if distance < 0.5:
            confidence = 'high'
            source = 'CACHE'
        elif distance < 0.85:
            confidence = 'medium'
            source = 'CACHE'
        else:
            confidence = 'low'
            source = 'CACHE_MISS'

        return {
            'source': source,
            'confidence': confidence,
            'distance': distance,
            'answer': document if source == 'CACHE' else None,
            'metadata': metadata if source == 'CACHE' else None,
            'query_time_ms': query_time
        }

    def _handle_add(self, query: str, answer: str, sources: List[Dict], metadata: Optional[Dict] = None) -> Dict:
        """Handle add operation (runs in worker thread)"""
        source_urls = [s.get('url', 'N/A')[:100] for s in sources[:3]]
        cache_metadata = {
            'timestamp': datetime.now().isoformat(),
            'num_sources': len(sources),
            'source_urls': ', '.join(source_urls),
            **(metadata or {})
        }

        document = f"Q: {query}\nA: {answer[:500]}"

        try:
            self.collection.add(
                documents=[document],
                metadatas=[cache_metadata],
                ids=[str(uuid.uuid4())]
            )
            return {'success': True, 'total_entries': self.collection.count()}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _handle_get_stats(self) -> Dict:
        """Handle get_stats operation"""
        return {
            'total_entries': self.collection.count(),
            'persist_path': str(self.persist_dir)
        }

    def _handle_clear(self) -> Dict:
        """Handle clear operation"""
        try:
            self.client.delete_collection("research_cache")
            self.collection = self.client.get_or_create_collection(
                name="research_cache",
                metadata={"description": "Web research results with auto-learning"}
            )
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def submit_request(self, operation: str, **kwargs) -> Dict:
        """
        Submit request to worker thread and wait for response

        This is a BLOCKING call, but it only blocks while communicating
        with the worker thread via queues, NOT while ChromaDB is working.

        To use from async code: await asyncio.to_thread(worker.submit_request, ...)
        """
        request_id = str(uuid.uuid4())
        response_queue = queue.Queue()

        self.response_queues[request_id] = response_queue

        self.request_queue.put({
            'id': request_id,
            'operation': operation,
            'args': kwargs
        })

        # Wait for response (blocking, but fast - just queue communication)
        try:
            result = response_queue.get(timeout=10.0)  # 10s timeout
            return result
        except queue.Empty:
            return {'error': 'Request timeout'}
        finally:
            # Cleanup
            del self.response_queues[request_id]


# Global worker instance
_worker: Optional[VectorCacheWorker] = None
_worker_lock = threading.Lock()


def get_worker(persist_directory: str = "./aifred_vector_cache") -> VectorCacheWorker:
    """Get or create global worker instance (thread-safe)"""
    global _worker

    if _worker is not None:
        return _worker

    with _worker_lock:
        if _worker is None:
            _worker = VectorCacheWorker(persist_directory)
            _worker.start()
        return _worker


# Async-friendly API
async def query_cache_async(user_query: str, n_results: int = 1) -> Dict:
    """Async wrapper for cache query"""
    import asyncio
    worker = get_worker()
    return await asyncio.to_thread(worker.submit_request, 'query', user_query=user_query, n_results=n_results)


async def add_to_cache_async(query: str, answer: str, sources: List[Dict], metadata: Optional[Dict] = None) -> Dict:
    """Async wrapper for cache add"""
    import asyncio
    worker = get_worker()
    return await asyncio.to_thread(worker.submit_request, 'add', query=query, answer=answer, sources=sources, metadata=metadata)


async def get_cache_stats_async() -> Dict:
    """Async wrapper for cache stats"""
    import asyncio
    worker = get_worker()
    return await asyncio.to_thread(worker.submit_request, 'get_stats')


def shutdown_worker():
    """Shutdown the worker thread (call on application shutdown)"""
    global _worker
    if _worker:
        _worker.stop()
        _worker = None
