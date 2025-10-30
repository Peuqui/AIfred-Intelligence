"""
AIfred Intelligence - Shared Libraries

Portiert von Gradio-Legacy für Reflex
"""

from .logging_utils import (
    initialize_debug_log,
    debug_print,
    debug_print_prompt,
    debug_print_messages,
    console_print,
    console_separator,
    get_console_messages,
    get_new_messages,
    clear_console,
    set_message_callback
)

from .prompt_loader import (
    load_prompt,
    get_url_rating_prompt,
    get_query_optimization_prompt,
    get_decision_making_prompt,
    get_intent_detection_prompt,
    get_followup_intent_prompt,
    get_system_rag_prompt
)

from .agent_tools import (
    search_web,
    scrape_webpage,
    build_context
)

from .agent_core import (
    perform_agent_research,
    chat_interactive_mode
)

from .cache_manager import (
    set_research_cache,
    get_cached_research
)

from .intent_detector import (
    detect_query_intent
)

__all__ = [
    # Logging
    "initialize_debug_log",
    "debug_print",
    "debug_print_prompt",
    "debug_print_messages",
    "console_print",
    "console_separator",
    "get_console_messages",
    "get_new_messages",
    "clear_console",
    "set_message_callback",
    # Prompts
    "load_prompt",
    "get_url_rating_prompt",
    "get_query_optimization_prompt",
    "get_decision_making_prompt",
    "get_intent_detection_prompt",
    "get_followup_intent_prompt",
    "get_system_rag_prompt",
    # Tools
    "search_web",
    "scrape_webpage",
    "build_context",
    # Agent Core
    "perform_agent_research",
    "chat_interactive_mode",
    # Cache Manager
    "set_research_cache",
    "get_cached_research",
    # Intent Detector
    "detect_query_intent",
]
