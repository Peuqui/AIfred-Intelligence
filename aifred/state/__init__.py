# AIfred State Package - Refactored into mixins
# Re-export everything from _base for backward compatibility
from ._base import (
    AIState,  # noqa: F401
    ChatMessage,  # noqa: F401
    _backend_init_lock,  # noqa: F401
    _global_backend_initialized,  # noqa: F401
    _global_backend_state,  # noqa: F401
)
from ._chat_history_state import ChatHistoryState  # noqa: F401
from ._streaming_state import StreamingState  # noqa: F401
