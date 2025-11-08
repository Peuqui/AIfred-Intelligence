"""
AIfred Intelligence - Shared Libraries

Portiert von Gradio-Legacy für Reflex
"""

from .logging_utils import (
    initialize_debug_log,
    log_message,
    debug_print_prompt,
    debug_print_messages,
    console_separator,
    clear_console
)

from .prompt_loader import (
    load_prompt,
    set_language,
    get_language,
    detect_language,
    get_query_optimization_prompt,
    get_decision_making_prompt,
    # get_cache_decision_addon removed - cache system deprecated
    get_intent_detection_prompt,
    get_followup_intent_prompt,
    get_system_rag_prompt
)

from .i18n import (
    TranslationManager,
    t
)

from .agent_tools import (
    search_web,
    scrape_webpage,
    build_context
)

from .research import perform_agent_research
from .conversation_handler import chat_interactive_mode

# Vector Cache (replacement for old cache system)
from .vector_cache import VectorCache

from .intent_detector import (
    detect_query_intent,
    get_temperature_label
)

__all__ = [
    # Logging
    "initialize_debug_log",
    "log_message",
    "debug_print_prompt",
    "debug_print_messages",
    "console_separator",
    "clear_console",
    # Prompts
    "load_prompt",
    "set_language",
    "get_language",
    "detect_language",
    "get_query_optimization_prompt",
    "get_decision_making_prompt",
    # "get_cache_decision_addon",  # removed
    "get_intent_detection_prompt",
    "get_followup_intent_prompt",
    "get_system_rag_prompt",
    # i18n
    "TranslationManager",
    "t",
    # Tools
    "search_web",
    "scrape_webpage",
    "build_context",
    # Agent Core
    "perform_agent_research",
    "chat_interactive_mode",
    # Vector Cache (NEW - replaces old cache system)
    "VectorCache",
    # Intent Detector
    "detect_query_intent",
    "get_temperature_label",
]
