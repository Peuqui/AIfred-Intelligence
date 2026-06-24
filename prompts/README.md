# AIfred Intelligence - Prompt Templates

This directory contains all AI prompts organized by language for easy maintenance and i18n support.

## Directory Structure

```
prompts/
├── de/                    # German prompts
│   ├── aifred/            # AIfred agent prompts
│   ├── sokrates/          # Sokrates agent prompts
│   ├── salomo/            # Salomo agent prompts
│   └── *.txt              # Shared prompts (German)
└── en/                    # English prompts
    ├── aifred/            # AIfred agent prompts
    ├── sokrates/          # Sokrates agent prompts
    ├── salomo/            # Salomo agent prompts
    └── *.txt              # Shared prompts (English)
```

## 3-Layer Prompt System

Each agent uses a layered prompt architecture:

| Layer | File | Purpose | Toggleable |
|-------|------|---------|------------|
| 1. Identity | `identity.txt` | WHO am I (name, role) | No (always loaded) |
| 2. Personality | `personality.txt` | HOW do I speak (style) | Yes (via settings) |
| 3. Task | `*.txt` | WHAT should I do | No (situational) |

## Agent Prompts

### AIfred (Main Agent)
| File | Purpose |
|------|---------|
| `identity.txt` | AIfred's identity and role definition |
| `personality.txt` | Butler personality and speech style |
| `system_minimal.txt` | Minimal system prompt for direct responses |
| `system_rag.txt` | RAG mode with research context |
| `system_rag_cache_hit.txt` | RAG mode using cached research |
| `direct.txt` | Direct response task instructions |
| `refinement.txt` | Response refinement after Sokrates critique |

### Sokrates (Critic Agent)
| File | Purpose |
|------|---------|
| `identity.txt` | Sokrates' identity and role |
| `personality.txt` | Socratic questioning style |
| `system_minimal.txt` | Minimal system prompt |
| `critic.txt` | Critique task for debate modes |
| `devils_advocate.txt` | Devil's advocate perspective |
| `direct.txt` | Direct response task |

### Salomo (Judge Agent)
| File | Purpose |
|------|---------|
| `identity.txt` | Salomo's identity and role |
| `personality.txt` | Wise, balanced speech style |
| `system_minimal.txt` | Minimal system prompt |
| `mediator.txt` | Mediation task for Auto-Consensus mode |
| `judge.txt` | Final verdict for Tribunal mode |
| `direct.txt` | Direct response task |

## Shared Prompts (by language)

| File | Purpose | Placeholders |
|------|---------|--------------|
| `decision_making.txt` | Auto-mode decision (search/cache/knowledge) | `{user_text}`, `{image_context}`, `{vision_json_context}` |
| `query_generation.txt` | Generate three web-search queries from user query (1× EN + 2× user-language) | `{user_text}`, `{vision_json_context}` |
| `intent_detection.txt` | Detect query intent for temperature tuning | `{user_query}` |
| `followup_intent_detection.txt` | Intent for follow-up queries | `{original_query}`, `{followup_query}` |
| `history_summarization.txt` | Compress conversation history | *(content passed separately)* |
| `cache_decision.txt` | Decide if cache hit is relevant | *(various)* |
| `cache_metadata.txt` | Generate cache entry summary | `{sources_preview}` |
| `vision_ocr.txt` | OCR extraction from images | *(none)* |
| `vision_templateless_*.txt` | Prompts for template-less vision models | *(none)* |

## Usage

```python
from aifred.lib.prompt_loader import (
    load_prompt,
    get_aifred_system_minimal,
    get_decision_making_prompt,
    set_language
)

# Set language (affects all subsequent calls)
set_language("en")  # or "de" or "auto"

# Load AIfred system prompt (with identity + personality)
system_prompt = get_aifred_system_minimal()

# Load decision-making prompt with placeholders
decision_prompt = get_decision_making_prompt(
    user_text="What's the weather in Berlin?",
    has_images=False
)

# Direct prompt loading with custom placeholders
prompt = load_prompt('intent_detection', lang='de', user_query="...")
```

## Language Detection

Language detection is handled by LLM-based Intent Detection in `intent_detector.py`.
The system prompt language is synced with `ui_language` from the UI settings.

```python
from aifred.lib.prompt_loader import set_language, get_language

set_language("de")  # Set German prompts
set_language("en")  # Set English prompts

current = get_language()  # Returns "de" or "en"
```

## Personality Toggle

Personality can be enabled/disabled per agent via settings:

```python
from aifred.lib.prompt_loader import set_personality_enabled, get_personality_enabled

# Disable AIfred's butler personality
set_personality_enabled("aifred", False)

# Check current state
if get_personality_enabled("sokrates"):
    print("Sokrates personality is active")
```

## Automatic Injections

Every prompt loaded via `load_prompt()` automatically receives:

1. **Current timestamp** (localized format)
2. **User name** (if set globally)

Example output:
```
CURRENT DATE AND TIME:
- Date: Wednesday, 2025-01-15
- Time: 14:30:45

USER NAME: Max

[Your prompt content here...]
```

## Adding New Prompts

1. Create `.txt` file in both `de/` and `en/` directories
2. Use `{placeholder}` syntax for dynamic values
3. Add convenience function in `prompt_loader.py` (optional)
4. Use `load_prompt('name', placeholder=value)` in code

## Best Practices

1. **Both languages required** - No fallback; prompts must exist in both `de/` and `en/`
2. **Use placeholders** - Never hardcode dynamic values (dates, user input)
3. **Clear structure** - Use headings and formatting for LLM readability
4. **Include examples** - Concrete examples improve LLM performance
5. **Format specifications** - For structured output (JSON, XML), be explicit

## Troubleshooting

### FileNotFoundError
```
Prompt file not found: .../prompts/en/xyz.txt
```
Ensure the prompt exists in both language directories.

### KeyError for placeholders
```
Missing placeholder in prompt 'decision_making': 'user_text'
```
Pass all required placeholders when calling `load_prompt()`.

### Prompt not reloading
Restart the service after modifying `.txt` files:
```bash
sudo systemctl restart aifred-intelligence.service
```
