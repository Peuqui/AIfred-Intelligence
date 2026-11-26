"""All libxml2 work of the AIfred process runs in exactly one thread.

Why: on 26.11.2026 AIfred died with SIGSEGV. The core dump showed one to_thread
worker in ``xmlFreeNodeList`` (freeing a document) while another worker was in
``xmlXPathCompiledEval``; libxml2 runs parsing and XPath without the GIL. lxml
(``parser.pxi``) lets a thread that needs a libxml2 dictionary for the first time
take over the one it is handed with a shared parser or XPath object (module-level
parsers of trafilatura and htmldate). Two threads then use one dictionary at the
same time. The crash could not be reproduced on demand, so the cause is removed
structurally instead: one thread does all of it. Measured: 80 pages through
trafilatura take 15.0 s in sequence and 25.7 s in 8 threads, so parallelism gains
nothing here.

The rule for the function handed to :func:`run_in_lxml_thread`: it parses AND reads
the tree out completely and returns plain Python data only. No lxml object (element,
tree, parser result, python-docx / openpyxl / python-pptx object) leaves the lxml
thread, because freeing it later in another thread is the very crash above.

Network I/O does not belong in this thread, it would block every other caller; only
parsing and reading out. Reading a local file (docx and the like) is fine.
"""

import asyncio
import gc
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

_on_lxml_thread = threading.local()


def _mark_lxml_thread() -> None:
    _on_lxml_thread.is_it = True


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lxml", initializer=_mark_lxml_thread)


def run_in_lxml_thread[**P, T](func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run ``func`` in the lxml thread and wait for its result (synchronous).

    Both misuses are programming errors and raise instead of working around them:
    a call from the lxml thread itself would wait for itself, a call in a thread
    with a running event loop would block the loop.
    """
    if getattr(_on_lxml_thread, "is_it", False):
        raise RuntimeError("run_in_lxml_thread called from the lxml thread itself (would wait for itself)")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("run_in_lxml_thread called on a running event loop (would block it); use asyncio.to_thread")
    return _executor.submit(func, *args, **kwargs).result()


def run_in_lxml_thread_freeing_cycles[**P, T](func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Like :func:`run_in_lxml_thread`, for libraries whose objects hold their lxml trees in reference cycles.

    python-docx and python-pptx do (package <-> parts): returning only plain data does not free those trees, the
    cyclic garbage collector does, later and in whichever thread happens to run it — the crash pattern of
    26.11.2026 again. Measured: reading one .docx leaves 5 lxml documents in cyclic garbage, one .pptx 38; the
    scraper, mail HTML, DAV and openpyxl leave none. A full collection costs time on a large process, so only these
    callers pay for it.
    """

    def call_and_free_cycles() -> T:
        # While func runs, no other thread may start the automatic collection: it would free the cycles in its own
        # thread. Afterwards they are freed here; gc.collect works with the collection switched off.
        gc.disable()
        try:
            result = func(*args, **kwargs)
            gc.collect()
        finally:
            gc.enable()
        return result

    return run_in_lxml_thread(call_and_free_cycles)
