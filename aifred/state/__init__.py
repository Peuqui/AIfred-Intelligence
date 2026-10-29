# AIfred State Package - Refactored into mixins
# Re-export everything from _base for backward compatibility
from ._base import AIState
from ._streaming_state import StreamingState
from ._chat_history_state import ChatHistoryState
from ._base import _global_backend_initialized
from ._base import _global_backend_state
from ._base import _backend_init_lock
from ._base import ChatMessage
