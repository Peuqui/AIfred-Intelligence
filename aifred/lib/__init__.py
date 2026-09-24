"""AIfred Intelligence - shared libraries.

No re-exports: importing any ``aifred.lib`` module must not load the
others (the multi-agent layer imports the backends, which import
``aifred.lib.config`` — eager imports here made that a cycle).
"""
