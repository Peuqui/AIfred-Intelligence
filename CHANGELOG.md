# Changelog

All notable changes to AIfred Intelligence will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2025-11-14

### Fix: Research Mode Persistence

#### Fixed
- **Research Mode not persisted** ([aifred/state.py:1314](aifred/state.py#L1314)):
  - `set_research_mode_display()` changed the variable but didn't call `_save_settings()`
  - Settings infrastructure was complete (load + save to dict), only the trigger was missing
  - Inconsistent with other setters like `set_automatik_model()` which correctly save
  - Now calls `_save_settings()` after mode change to persist immediately

#### How it works
1. **Startup**: Loads `research_mode` from `settings.json` (Line 219) or falls back to `"automatik"` from `config.py`
2. **User changes mode**: UI calls `set_research_mode_display()` → `_save_settings()` → `settings.json` updated
3. **Next startup**: Saved mode is restored correctly

#### Impact
- **Before**: Research mode reset to default on every restart
- **After**: Research mode persists across sessions like all other settings

#### Files Modified
- [aifred/state.py](aifred/state.py): Line 1314 (added `_save_settings()` call)

---

### 🎯 Progress UI System Complete - MILESTONE

#### Added
- **Complete Progress Event Handling** ([aifred/state.py:942-960](aifred/state.py#L942-L960)):
  - Quick/Deep research modes now handle all progress events (`progress`, `history_update`, `thinking_warning`)
  - Identical event routing logic as Automatik mode
  - Shows Web-Scraping progress (1/3, 2/3, 3/3) and LLM generation phase
  - Visual feedback for all research pipeline stages

- **Pulsing Animation for All Modes** ([aifred/aifred.py:526,539,543](aifred/aifred.py#L526)):
  - Animation triggers on `progress_active | is_generating` (previously only `progress_active`)
  - "Generiere Antwort" now pulses in "none" mode (Eigenes Wissen) during LLM inference
  - Consistent visual feedback across all 4 research modes
  - Bold text weight during active phases

- **Dynamic Status Text** ([aifred/aifred.py:449-464](aifred/aifred.py#L449-L464)):
  - Status text shows "Generiere Antwort" when `is_generating=True` even if `progress_active=False`
  - Fixes idle state in "none" mode where status stayed on "Warte auf Eingabe"
  - Properly reflects system activity in all modes

#### Fixed
- **Progress Bar Visibility** ([assets/custom.css:90](assets/custom.css#L90)):
  - Removed `.rt-Box` from dark theme CSS selector
  - Orange progress bar fill (#e67700) was hidden by `!important` background override
  - Progress bar now visible in all research modes
  - Root cause: Commit d8e4d55 ("Force dark theme") introduced CSS specificity conflict

- **Missing Progress Events in Quick/Deep Modes**:
  - Quick/Deep modes had no progress event handling (only debug, content, result)
  - Web-Scraping and LLM phases were invisible to user
  - Now shows full pipeline: "Web-Scraping 1/7" → "Generiere Antwort"

#### Testing Results
- ✅ **Automatik Mode**: Progress bar + phases (Automatik → Scraping → LLM)
- ✅ **Quick Mode**: Progress bar + phases (Scraping 1/3 → LLM)
- ✅ **Deep Mode**: Progress bar + phases (Scraping 1/7 → LLM)
- ✅ **None Mode**: Pulsing "Generiere Antwort" during LLM

#### Technical Details
- Progress event flow: `scraper_orchestrator.py` → `orchestrator.py` → `state.py` → `aifred.py`
- Event types: `progress` (scraping, llm, compress), `debug`, `content`, `result`, `history_update`
- State variables: `progress_active`, `progress_phase`, `progress_current`, `progress_total`, `is_generating`
- Reflex reactive rendering: `rx.cond()` for conditional UI updates

#### Impact
- **Before**: Inconsistent progress feedback, Quick/Deep modes had no visual pipeline status
- **After**: Professional, consistent UI feedback across all modes. User always knows system status.
- **UX Improvement**: No more confusion about "is it working?" - clear visual feedback at every stage

#### Files Modified
- [assets/custom.css](assets/custom.css): Line 90 (removed `.rt-Box`)
- [aifred/state.py](aifred/state.py): Lines 942-960 (progress event handling)
- [aifred/aifred.py](aifred/aifred.py): Lines 449-464, 526, 539, 543 (status text, pulsing animation)
- [TODO.md](TODO.md): Comprehensive milestone documentation

---

### 🏷️ Source Label Consistency & Double Metadata Bug Fix

#### Fixed
- **Double Source Metadata Bug** ([aifred/lib/message_builder.py](aifred/lib/message_builder.py), [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py)):
  - Fixed issue where responses showed duplicate source labels (e.g., `( Inferenz: 62.3s, Quelle: Web-Recherche ) ( Inferenz: 53.2s, Quelle: LLM-Trainingsdaten )`)
  - Root cause: HTML metadata (`<span>` tags) and thinking collapsibles (`<details>` tags) were NOT removed from chat history before passing to LLM
  - LLM sometimes copied old metadata into new responses, resulting in duplicate sources
  - Solution: Enhanced `build_messages_from_history()` to strip ALL HTML tags and metadata using regex patterns

#### Changed
- **Message History Cleaning** ([aifred/lib/message_builder.py:86-100](aifred/lib/message_builder.py#L86-L100)):
  - Added `import re` for regex-based HTML tag removal
  - Four-step cleaning process for AI messages:
    1. Remove thinking collapsibles: `<details>...</details>`
    2. Remove metadata spans: `<span style="...">( Inferenz: ... )</span>`
    3. Fallback: Remove text-based metadata patterns
    4. Cleanup: Remove multiple newlines and excess whitespace
  - Prevents LLM from seeing or copying old metadata from history

- **Source Label Consistency** ([aifred/lib/conversation_handler.py:480-486](aifred/lib/conversation_handler.py#L480-L486)):
  - Replaced ambiguous `"LLM-Trainingsdaten"` label with context-aware labels:
    - `"Cache+LLM (RAG)"` - RAG context from Vector Cache
    - `"LLM (mit History)"` - Chat history available as context (NEW)
    - `"LLM"` - Pure LLM without additional context (NEW)
  - Consistent with existing labels: `"Vector Cache"` (direct hit), `"Web-Recherche"` (agent research)

#### Impact
- **Before**: Confusing duplicate source labels, inconsistent terminology
- **After**: Clean, single source label per response with clear context indication
- **User Experience**: Users can now clearly see where each response comes from:
  - First message: `( Inferenz: 2.5s, Quelle: LLM )`
  - Follow-up with history: `( Inferenz: 3.2s, Quelle: LLM (mit History) )`
  - RAG context: `( Inferenz: 4.1s, Quelle: Cache+LLM (RAG) )`
  - Web research: `( Inferenz: 62.3s, 35.2 tok/s, Quelle: Web-Recherche )`

#### Files Modified
- [aifred/lib/message_builder.py](aifred/lib/message_builder.py): Lines 11 (import re), 84-100 (HTML cleaning)
- [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py): Lines 480-486 (source labels)

---

### 🧹 Debug Console Separator Structure

#### Fixed
- **Separator Placement Logic** ([aifred/lib/research/context_builder.py](aifred/lib/research/context_builder.py), [aifred/state.py](aifred/state.py)):
  - Separators now appear AFTER each logical processing unit (not before)
  - Implemented "separator marks end of unit" principle across all code paths
  - Eliminated double separators after LLM completion
  - Three logical units with proper separation:
    1. LLM Response Generation → Separator
    2. Vector Cache Decision (web research only) → Separator
    3. History Compression Check → Separator

#### Changed
- **Web Research Path** ([aifred/lib/research/context_builder.py:204-207](aifred/lib/research/context_builder.py#L204-L207)):
  - Added separator after "✅ Haupt-LLM fertig" message
  - Added separator after Cache-Decision block (lines 329-332)
- **Normal Chat Path** ([aifred/state.py:866-869](aifred/state.py#L866-L869)):
  - Removed redundant separator (conversation_handler already emits one)
- **Compression Check** ([aifred/state.py:997-1016](aifred/state.py#L997-L1016)):
  - Separator now appears after compression completion message

#### Impact
- **Debug Console Output**: Clean, predictable structure with logical block boundaries
- **Before**: Inconsistent separator placement, double separators in some paths
- **After**: Each processing unit ends with exactly one separator line

#### Files Modified
- [aifred/lib/research/context_builder.py](aifred/lib/research/context_builder.py): Lines 204-207, 329-332
- [aifred/state.py](aifred/state.py): Lines 866-869 (commented), 997-1016

---

### 🔄 Auto-Reload Model List on Backend Restart

#### Added
- **Model List Auto-Refresh on Ollama Restart** ([aifred/state.py:1044-1092](aifred/state.py#L1044-L1092)):
  - After Ollama service restart via UI, automatically reload model list from `/api/tags`
  - Update both session state (`self.available_models`) and global state (`_global_backend_state`)
  - No need to restart AIfred just to see newly downloaded models
  - Shows immediate feedback: "🔄 Reloading model list..." and "✅ Model list updated: N models found"

#### Changed
- **`restart_backend()` Method Enhancement**:
  - Added automatic model list refresh after Ollama restart
  - Uses same curl-based API call as initial backend initialization
  - Preserves existing vLLM/TabbyAPI restart logic unchanged

#### Impact
- **User Experience**: Download new models → Restart Ollama → Models instantly available in dropdown
- **Before**: Had to restart entire AIfred service to refresh model list
- **After**: Just click "Restart Ollama" button in system control panel

#### Files Modified
- [aifred/state.py](aifred/state.py): Lines 1044-1092

---

### 🌍 Language Detection for All Prompts

#### Fixed
- **Language Not Passed to Prompt Loading Functions** (8 locations across 7 files):
  - Language was detected but not passed as `lang=` parameter to prompt functions
  - German user queries were receiving English prompts despite correct detection
  - Root cause: Missing `detected_user_language` parameter in function calls

#### Changed
- **Intent Detection** ([aifred/lib/intent_detector.py](aifred/lib/intent_detector.py)):
  - Line 62: Added `lang=detected_user_language` to `get_intent_detection_prompt()`
  - Lines 110-113: Added language detection for followup intent classification
- **Query Optimization** ([aifred/lib/query_optimizer.py:47](aifred/lib/query_optimizer.py#L47)):
  - Added `lang=detected_user_language` to `get_query_optimization_prompt()`
- **Decision Making** ([aifred/lib/conversation_handler.py:268](aifred/lib/conversation_handler.py#L268)):
  - Added `lang=detected_user_language` to `get_decision_making_prompt()`
- **Cache Hit Prompt** ([aifred/lib/research/cache_handler.py:77](aifred/lib/research/cache_handler.py#L77)):
  - Added `lang=detected_user_language` to `load_prompt('system_rag_cache_hit')`
- **System RAG Prompt** ([aifred/lib/research/context_builder.py:99](aifred/lib/research/context_builder.py#L99)):
  - Added `lang=detected_user_language` and `user_text` parameter
- **Cache Decision** ([aifred/lib/research/context_builder.py:241](aifred/lib/research/context_builder.py#L241)):
  - Added `lang=detected_user_language` to `load_prompt('cache_decision')`
- **RAG Relevance Check** ([aifred/lib/rag_context_builder.py:89](aifred/lib/rag_context_builder.py#L89)):
  - Added `lang=detected_user_language` to `load_prompt('rag_relevance_check')`
- **History Summarization** ([aifred/lib/context_manager.py:290](aifred/lib/context_manager.py#L290)):
  - Special case: Detects language from first user message in conversation history
  - Fallback to "de" if no messages available

#### Impact
- **User Experience**: German queries now receive German prompts, English queries receive English prompts
- **All Subsystems Affected**: Intent detection, query optimization, research mode, cache decisions, history compression
- **Consistent i18n**: All 8 prompt loading locations now respect detected language

#### Files Modified
- [aifred/lib/intent_detector.py](aifred/lib/intent_detector.py): Lines 62, 110-113
- [aifred/lib/query_optimizer.py](aifred/lib/query_optimizer.py): Line 47
- [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py): Lines 268-270
- [aifred/lib/research/cache_handler.py](aifred/lib/research/cache_handler.py): Lines 76-89
- [aifred/lib/research/context_builder.py](aifred/lib/research/context_builder.py): Lines 98-110, 244-251
- [aifred/lib/rag_context_builder.py](aifred/lib/rag_context_builder.py): Lines 86-100
- [aifred/lib/context_manager.py](aifred/lib/context_manager.py): Lines 289-301

---

### 🧠 Thinking Mode Support for Ollama and vLLM Backends

#### Added
- **Ollama Thinking Mode Implementation** ([aifred/backends/ollama.py](aifred/backends/ollama.py)):
  - Added `"think": true` API parameter support in `chat()` and `chat_stream()` methods
  - Parse separate `thinking` and `content` fields from Ollama response
  - Wrap thinking output in `<think>...</think>` tags for unified formatting
  - Streaming support: Properly handle thinking chunks before content chunks
- **vLLM Thinking Mode Enhancement** ([aifred/backends/vllm.py](aifred/backends/vllm.py)):
  - Improved type safety: Replaced `# type: ignore` with proper `Dict[str, Any]` annotations
  - Use `chat_template_kwargs: {"enable_thinking": bool}` in `extra_body` for Qwen3 models
  - Consistent implementation across `chat()` and `chat_stream()` methods
- **Unified Thinking Process Formatting** ([aifred/lib/formatting.py](aifred/lib/formatting.py)):
  - Created `format_metadata()` function for metadata display (color: `#bbb`, font-size: `0.85em`)
  - Enhanced `format_thinking_process()` with model name and inference time in collapsible header
  - Added line break reduction regex (`r'\n\n+'`) to reduce excessive blank lines while preserving paragraphs
  - Fallback logic for malformed `<think>` tags (missing opening tag)
  - Updated `build_debug_accordion()` with same improvements for agent research mode
- **UI Styling** ([aifred/theme.py](aifred/theme.py)):
  - Added `.thinking-compact` CSS class for proper paragraph spacing (`0.75em` margins)
  - Text color matches collapsible header (`#aaa`)
  - Compact spacing for first/last child elements
- **User Message HTML Rendering** ([aifred/aifred.py](aifred/aifred.py#L725)):
  - Changed from `rx.text()` to `rx.markdown()` to properly render HTML metadata spans
- **Settings Update** ([aifred/lib/settings.py](aifred/lib/settings.py#L74)):
  - Default `enable_thinking` set to `True` for optimal user experience

#### Changed
- **Type Safety Improvements**:
  - Import `Any` from typing in vLLM backend
  - Declare `extra_body: Dict[str, Any] = {}` instead of untyped dict with `# type: ignore`
  - Removed all type ignore comments in vLLM backend
- **Metadata Color**: Adjusted from `#999` → `#aaa` → `#bbb` for better readability
- **Collapsible Styling**: Removed complex inline div styling in favor of CSS class approach

#### Fixed
- **HTML Rendering in User Messages**: Metadata now properly displays as styled HTML instead of raw tags
- **Type Safety**: MyPy passes without warnings for vLLM `extra_body` dictionary assignments
- **Line Break Handling**: Regex correctly reduces 2+ consecutive newlines to exactly 2 (one blank line)
- **Paragraph Spacing**: Thinking process collapsibles now have proper spacing (not too cramped, not too spaced)

#### Technical Details
- **Ollama API**: Uses `"think": true` payload parameter (not `enable_thinking` in options)
- **Ollama Response Structure**: Provides separate `thinking` and `content` fields in message dict
- **vLLM API**: Uses `extra_body["chat_template_kwargs"] = {"enable_thinking": bool}`
- **Unified Format**: Both backends output `<think>...</think>` wrapped content for consistent parsing
- **Automatik-LLM**: Thinking mode intentionally DISABLED for all automatik tasks (8x faster decisions)

#### Files Modified
- [aifred/backends/ollama.py](aifred/backends/ollama.py): Lines 93-95, 116-125, 199-236
- [aifred/backends/vllm.py](aifred/backends/vllm.py): Lines 9, 79, 88-90, 163, 172-174
- [aifred/lib/formatting.py](aifred/lib/formatting.py): Lines 13-37, 64-152, 155-235
- [aifred/theme.py](aifred/theme.py): Lines 125-142
- [aifred/lib/settings.py](aifred/lib/settings.py): Line 74
- [aifred/aifred.py](aifred/aifred.py): Line 725
- [aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py): Lines 467-478

---

### 📱 Mobile Dropdown Optimization

#### Problem Solved
- **Fixed**: Select dropdowns disappeared below viewport on mobile devices
- **Impact**: Users couldn't select models/backends when list was too long or positioned at bottom
- **Root Cause**: Default "item-aligned" positioning opens downward without viewport awareness
- **Solution**: Intelligent positioning + scrollable dropdowns with max-height

#### Implementation
- **Intelligent Positioning**: Added `position="popper"` to all select dropdowns
  - Backend select, Model select, Automatik-Model select
  - Adapts to available viewport space (opens upward if no space below)
- **Scrollable Dropdowns**: CSS max-height with internal scroll
  - Max height: 300px or available viewport height (whichever is smaller)
  - Internal scrollbar when list exceeds height
  - Dropdown stays open while scrolling (no accidental close)
- **Mobile-Friendly**: Touch scroll works correctly inside dropdown

#### Files Modified
- [aifred/aifred.py](aifred/aifred.py): Added `position="popper"` to 3 select components
- [aifred/theme.py](aifred/theme.py): CSS for `.rt-SelectContent` and `.rt-SelectViewport`

---

### 🛡️ GPU Compatibility Backend Filter

#### Problem Solved
- **Fixed**: Incompatible backends (vLLM, TabbyAPI) were selectable on Pascal GPUs (P40, GTX 10 series)
- **Impact**: vLLM would crash with Triton compiler errors or be extremely slow (FP16 1:64 ratio)
- **Root Cause**: Backend selection showed all backends regardless of GPU capabilities
- **Solution**: Automatic backend filtering based on GPU detection

#### Implementation
- **GPU Detection**: Existing `GPUDetector` now filters UI backend options
  - Detects GPU compute capability and FP16 performance
  - Tesla P40 (Compute 6.1) → Only shows "ollama" backend
  - RTX 3060+ (Compute 8.6) → Shows "ollama", "vllm", "tabbyapi"
- **Auto-Switch**: If saved backend is incompatible, auto-switch to first available backend
- **UI Updates**:
  - Backend select dropdown shows only compatible backends
  - GPU info badge shows detected GPU and compute capability
  - Removed redundant GPU warning box (no longer needed)
- **Backend Requirements**:
  - **Ollama**: Works on ALL GPUs (Compute 3.5+, INT8/Q4/Q8 quantization)
  - **vLLM**: Requires Compute 7.0+, fast FP16 performance (Volta or newer)
  - **TabbyAPI**: Same as vLLM (Compute 7.0+, fast FP16)

#### GPU Compatibility Matrix
| GPU | Compute Cap | Available Backends | Reason |
|-----|-------------|-------------------|--------|
| Tesla P40 | 6.1 | ollama only | FP16 ratio 1:64 (extremely slow) |
| Tesla P100 | 6.0 | ollama only | Compute < 7.0 (Triton unsupported) |
| GTX 1080 Ti | 6.1 | ollama only | Pascal architecture (slow FP16) |
| RTX 2080 | 7.5 | ollama, vllm, tabbyapi | Turing (fast FP16, Tensor Cores) |
| RTX 3060 | 8.6 | ollama, vllm, tabbyapi | Ampere (fast FP16, AWQ Marlin) |
| RTX 4090 | 8.9 | ollama, vllm, tabbyapi | Ada Lovelace (excellent) |

#### Files Modified
- [aifred/state.py](aifred/state.py#L146): Added `available_backends` state field
- [aifred/state.py](aifred/state.py#L240-L251): Backend filtering logic on session load
- [aifred/aifred.py](aifred/aifred.py#L994): Dynamic backend select with `AIState.available_backends`
- [aifred/aifred.py](aifred/aifred.py#L1013-L1028): GPU info badge
- [aifred/lib/gpu_detection.py](aifred/lib/gpu_detection.py): Existing GPU detection (no changes)

---

### 📐 vLLM Context Auto-Detection (40K-128K+ Support)

#### Problem Solved
- **Fixed**: vLLM context limit was only 16K tokens
- **Impact**: Responses were cut off mid-sentence, chat history was severely limited
- **Root Cause**: Hardcoded context limits didn't match actual model capabilities
- **Solution**: Automatic detection from model's config.json

#### Implementation
- **Auto-Detection**: `get_model_max_position_embeddings()` reads `max_position_embeddings` from HuggingFace cache
  - Searches `~/.cache/huggingface/hub/models--{vendor}--{model}/snapshots/*/config.json`
  - Returns native context limit (e.g., 40,960 for Qwen3-8B-AWQ, 131,072 for Qwen2.5-Instruct)
  - No more hardcoded values - works with any model!
- **Enhanced**: `vLLMProcessManager.start()` uses auto-detected context
  - `max_model_len=None` triggers auto-detection
  - Falls back to vLLM default if config not found
- **YaRN Support**: Optional RoPE scaling for extending context beyond training limits
  - Accepts YaRN config dict: `{"rope_type": "yarn", "factor": 2.0, "original_max_position_embeddings": 40960}`
  - Can extend context (e.g., 40K → 80K with factor=2.0)
  - Not used by default - native context is sufficient
- **Error Diagnostics**: Improved vLLM startup error logging (captures stderr)
- **Memory Fix**: Increase GPU memory utilization to 95% (40K context needs ~5.6 GiB KV cache)
- **Compatibility**: Use vLLM v0.11.0 defaults (v1 engine with multiprocessing)

#### Context Limits
- **Before**: 16K tokens (hardcoded, responses cut off!)
- **After**: Hardware-constrained maximum:
  - **RTX 3060 (12GB, Ampere 8.6)**: **26,608 tokens** @ 90% GPU memory ✅ Tested
  - Native model support: 40,960 tokens (requires ~5.6 GiB KV cache)
- **Note**: Context size limited by VRAM + GPU architecture, not model capability
- **VRAM Usage**: ~12 GB (97.7% utilization) at 26K context - GPU fully utilized but stable
- **Future**: Can use YaRN for extension when more VRAM available (e.g., RTX 3090, A100, H100)

#### Files Modified
- [aifred/lib/vllm_manager.py](aifred/lib/vllm_manager.py#L19-L64): Added `get_model_max_position_embeddings()`
- [aifred/lib/vllm_manager.py](aifred/lib/vllm_manager.py#L134-L158): Auto-detect in `start()` method
- [aifred/state.py](aifred/state.py#L569-L580): Removed hardcoded values, use auto-detection

---

### ⚡ Backend Pre-Initialization (Page Reload Performance)

#### Problem Solved
- **Fixed**: Backend re-initialization on every page reload (F5)
- **Impact**: Eliminated 30-120s wait time on page refresh
- **Behavior**: Backend now initializes ONCE at server start, persists across page reloads

#### Implementation
- **Added**: Module-level `_global_backend_state` dict for persistent backend state
- **Added**: `_global_backend_initialized` flag to prevent redundant initialization
- **Refactored**: `on_load()` method with two-phase initialization:
  - **Global Phase** (runs once at server start):
    - Debug log initialization
    - Language settings
    - Vector Cache connection
    - GPU detection
  - **Session Phase** (runs per user/tab/reload):
    - Load user settings
    - Generate session ID
    - Restore backend state from global cache
- **Optimized**: `initialize_backend()` with fast/slow paths:
  - **Fast Path**: Backend already initialized → Restore from global state (instant!)
  - **Slow Path**: First initialization or backend switch → Full model loading
- **Optimized**: vLLM Process Manager persistence:
  - `_vllm_manager` stored in global state
  - vLLM server survives page reloads
  - Prevents unnecessary vLLM restarts (saves 70-120s)

#### State Persistence
- **Preserved on page reload**:
  - Chat history
  - Debug console messages
  - Backend configuration
  - Loaded models
  - vLLM server process
  - GPU detection results

#### Restart Buttons
- **Enhanced**: `restart_backend()` method:
  - Ollama: Restarts systemd service
  - vLLM: Stops and restarts vLLM server process
  - TabbyAPI: Shows info message (manual restart)
- **Preserved**: "AIfred Neustarten" button behavior:
  - Clears chat history, debug console, caches
  - Does NOT restart backend or vLLM
  - Soft restart for development (hot-reload mode)
  - Hard restart via systemd for production

#### Code Quality
- **Passed**: ruff linter checks (no errors)
- **Fixed**: Unused variable `research_result` in generate loop
- **Added**: Type annotations for `_global_backend_state: dict[str, Any]`
- **Tested**: Backend switching, page reload, restart buttons

---

### 🔍 GPU Detection & Compatibility Checking

#### GPU Detection System
- **Added**: `aifred/lib/gpu_detection.py` - Automatic GPU capability detection
- **Features**:
  - Detects GPU compute capability via nvidia-smi
  - Identifies compatible/incompatible backends per GPU
  - Warns about known GPU limitations (Tesla P40 FP16 issues, etc.)
- **State Integration**:
  - GPU info stored in AIState (gpu_name, gpu_compute_cap, gpu_warnings)
  - Detection runs on startup via `on_load()`
  - Debug console shows GPU capabilities and warnings

#### UI Warnings
- **Added**: Visual warning in Settings when vLLM selected on incompatible GPU
- **Shows**:
  - GPU name and compute capability
  - Backend requirements (Compute Cap 7.5+ for vLLM/AWQ)
  - Recommendation to switch to Ollama for better performance
- **Styling**: Red warning box with icon, auto-shows when vLLM + Pascal GPU

#### Download Scripts Enhanced
- **Enhanced**: `download_vllm_models.sh` with GPU compatibility check
- **Features**:
  - Automatic GPU detection before download
  - Detailed warning for incompatible GPUs (P40, GTX 10 series)
  - Explains why vLLM/AWQ won't work (Triton, FP16 ratio, Compute Cap)
  - Offers exit with recommendation to use Ollama
  - User can override with explicit confirmation

#### Documentation
- **Added**: `docs/GPU_COMPATIBILITY.md` - Comprehensive GPU compatibility guide
- **Covers**:
  - GPU compatibility matrix (Pascal, Turing, Ampere, Ada, Hopper)
  - Backend comparison (Ollama GGUF vs vLLM AWQ vs TabbyAPI EXL2)
  - Technical explanation of Pascal limitations
  - Performance benchmarks (P40 vs RTX 4090)
  - Recommendations by use case
  - Troubleshooting guide

#### Backend Compatibility Summary
- **Ollama (GGUF)**: ✅ Works on all GPUs (Compute Cap 3.5+)
- **vLLM (AWQ)**: ⚠️ Requires Compute Cap 7.5+ (Turing+), fast FP16
- **TabbyAPI (EXL2)**: ⚠️ Requires Compute Cap 7.0+ (Volta+), fast FP16

#### Known GPU Issues Documented
- **Tesla P40**: FP16 ratio 1:64 → vLLM/ExLlama ~1-5 tok/s (unusable)
- **Tesla P100**: FP16 ratio 1:2 → vLLM possible but slower than Ollama
- **GTX 10 Series**: Compute Cap 6.1 → vLLM not supported
- **Recommendation**: Use Ollama (GGUF) on Pascal GPUs for best performance

### 🚀 Performance - 8x Faster Automatik, 7s Saved per Web Research

#### vLLM Preloading Optimization
- **Skip unnecessary preloading** for vLLM/TabbyAPI (models stay in VRAM)
- **Result**: 7s faster web research, UI shows "ℹ️ Haupt-LLM bereits geladen"
- **Files**: `aifred/backends/vllm.py`, `aifred/backends/tabbyapi.py`, `aifred/lib/research/scraper_orchestrator.py`

#### Thinking Mode Disabled for Automatik Tasks
- **Problem**: Qwen3 Thinking Mode slowed decisions from 1-2s to 7-13s
- **Solution**: `enable_thinking=False` for all automatik tasks (decisions, intent, RAG)
- **Result**: 8x faster total flow (3s instead of 24s)
  - Automatik decision: 8.7s → 2.1s (4x faster)
  - Intent detection: 13.0s → 0.3s (43x faster)
- **Files**: Fixed parameter passing in `llm_client.py`, vLLM `chat_template_kwargs` structure, 9 LLM call sites

### 🎯 Context Window Improvements

#### Real Tokenizer instead of Estimation
- **Problem**: Token estimation (3.5 chars/token) was 25% too low → context overflow
- **Solution**: HuggingFace AutoTokenizer with local cache
- **Fallback**: 2.5 chars/token (conservative) when tokenizer unavailable
- **Files**: `aifred/lib/context_manager.py` + 5 call sites

#### vLLM Context Auto-Detection (16K → 40K for Qwen3)
- **Problem**: vLLM hardcoded to 16K despite Qwen3-8B supporting 40K
- **Solution**: Remove `--max-model-len` → auto-detect from model config
- **Benefits**:
  - Qwen3-8B: 40K context (matches Ollama)
  - Qwen2.5-32B: 128K context automatically
  - No hardcoding, each model uses native limit
- **Files**: `vllm_startup.py`, `aifred/lib/vllm_manager.py`

### 🐛 Bug Fixes

- **Backend switching**: Fixed AttributeError and wrong model selection
- **Dead code**: Removed 83 lines (77 unreachable + 6 unused variables)
- **UI**: Debug console limit 100 → 500 messages

### 🔄 Portability

- ✅ No absolute paths
- ✅ No system-specific dependencies
- ✅ HuggingFace tokenizer: offline-capable (local cache)
- ✅ vLLM auto-detection: works on any system
- ✅ Systemd services: Template-based with sed substitution
- ✅ **Fully portable to MiniPC**

### 📦 Model Configuration

#### Restructured Download Scripts with YaRN Support
- **Added**: Separate download scripts for better organization
  - `download_ollama_models.sh` - Ollama (GGUF) models
  - `download_vllm_models.sh` - vLLM (AWQ) models with YaRN docs
  - `download_all_models.sh` - Master script for both backends
- **Archived**: Old scripts renamed to `.old` (preserved for reference)

#### Qwen3 AWQ Models (Primary Recommendation)
- **Added**: Qwen3 AWQ series with YaRN context extension support
  - Qwen3-4B-AWQ (~2.5GB, 40K native, YaRN→128K)
  - Qwen3-8B-AWQ (~5GB, 40K native, YaRN→128K)
  - Qwen3-14B-AWQ (~8GB, 32K native, YaRN→128K)
- **Features**:
  - Optional Thinking Mode (enable_thinking parameter)
  - Newest generation (2025)
  - Flexible context: Native 32-40K, YaRN extendable to 64K/128K

#### Qwen2.5 Instruct-AWQ Models (Alternative)
- **Available**: As alternative option with native 128K context
  - Qwen2.5-7B-Instruct-AWQ (~4GB, 128K native)
  - Qwen2.5-14B-Instruct-AWQ (~8GB, 128K native)
  - Qwen2.5-32B-Instruct-AWQ (~18GB, 128K native)
- **Benefits**: No YaRN needed, older generation but proven stable

#### YaRN Context Extension Support
- **Documentation**: Added comprehensive YaRN configuration examples
- **Flexible Factors**:
  - factor=2.0 → 64K context (recommended for chat history)
  - factor=4.0 → 128K context (for long documents)
- **Implementation**: Command-line and Python examples in download scripts
- **Trade-offs**: Documented perplexity loss vs context gain

## [1.0.0] - 2025-11-10

### 🎉 Milestone: Vector Cache Production Ready

#### Added
- **ChromaDB Vector Cache**: Thread-safe semantic caching for web research results
  - Docker-based ChromaDB server mode (port 8000)
  - Automatic duplicate detection with configurable distance thresholds
  - Time-based cache invalidation (5-minute threshold for explicit research keywords)
  - Query-only embeddings for improved similarity matching (distance 0.000 for exact matches)
  - Auto-learning from web research results
- **Configurable Distance Thresholds** (`aifred/lib/config.py`):
  - `CACHE_DISTANCE_HIGH = 0.5` - High confidence cache hits
  - `CACHE_DISTANCE_MEDIUM = 0.85` - Medium confidence cache hits
  - `CACHE_DISTANCE_DUPLICATE = 0.3` - Duplicate detection for explicit keywords
  - `CACHE_TIME_THRESHOLD = 300` - 5 minutes for time-based invalidation
- **Enhanced Cache Logging**: Distance and confidence displayed for all cache operations (hits, misses, duplicates)
- **Docker Compose Consolidation**: Unified `docker/docker-compose.yml` with ChromaDB + optional SearXNG
- **Docker Documentation**: New `docker/README.md` with service management instructions

#### Changed
- **Vector Cache Architecture**: Migrated from PersistentClient to HttpClient (Docker server mode)
  - Fixes: File lock issues and deadlocks in async operations
  - Improvement: Thread-safe by design, no worker threads needed
- **Cache Query Strategy**: Implemented `query_newest()` method
  - Returns most recent match instead of best similarity match
  - Prevents outdated cache entries from being returned
- **Context Window Management**: Generous reserve strategy (8K-16K tokens) to prevent answer truncation
- **Project Structure**:
  - Moved `docker-compose.yml` from root to `docker/` directory
  - Consolidated separate ChromaDB and SearXNG compose files
- **Duplicate Detection**: Time-aware duplicate prevention
  - Skip save if similar entry exists and is < 5 minutes old
  - Allow new entry if existing entry is > 5 minutes old (allows updates)

#### Removed
- **Obsolete Implementations**: Deleted `archive/vector_cache_old/` directory
  - Old PersistentClient implementation (vector_cache.py)
  - Old worker thread implementation (vector_cache_v2.py)
  - Still available in git history if needed

#### Fixed
- **KeyError 'data'**: Fixed cache hit result format for explicit research keywords
- **Missing user_with_time**: Added timestamp generation for history entries
- **Cache Miss on Recent Entries**: Fixed `query_newest()` to find most recent duplicate
- **Duplicate Log Messages**: Proper distinction between saved entries and skipped duplicates
- **Distance Logging**: Distance now displayed for all cache operations (hits and misses)

#### Technical Details
- **ChromaDB Version**: Using latest ChromaDB Docker image
- **API Version**: ChromaDB v2 API (v1 deprecated)
- **Collection Management**:
  - Collection name: `research_cache`
  - Persistent storage: `./aifred_vector_cache/` (Docker volume)
  - Health checks: Automatic with 30s interval
- **Cache Statistics**: Available via `get_cache().get_stats()`

#### Migration Notes
- **Docker Commands Updated**: All docker-compose commands now require `-f docker/docker-compose.yml` or working from `docker/` directory
- **ChromaDB Reset**: Two methods documented in README
  - Option 1: Full restart (stop container, delete data, restart)
  - Option 2: API-based collection deletion (faster, no container restart)
- **Configuration**: All cache thresholds centralized in `aifred/lib/config.py`

---

## [1.1.0] - 2025-11-10

### 🧠 Intelligent Cache Decision System

#### Added
- **LLM-Based Cache Decision**: Automatic cache filtering with AI-powered override capability
  - Two-stage filter: Volatile keywords → LLM decision
  - Override logic: Concept questions (e.g., "Was ist Wetter?") are cached despite volatile keywords
  - Automatik-LLM makes decision (fast, deterministic with temperature=0.1)
- **Volatile Keywords List** (`aifred/lib/config.py`):
  - `CACHE_EXCLUDE_VOLATILE` - 40+ keywords for volatile data detection
  - Weather, finance, live sports, breaking news, time-specific queries
  - Triggers LLM decision for smart caching
- **Cache Decision Prompt** (`prompts/de/cache_decision.txt`):
  - Clear rules: Cache facts/concepts, don't cache live data
  - Examples for both cacheable and non-cacheable queries
  - Override examples for ambiguous cases
- **Source Attribution**: Transparent source labeling in chat history
  - "Quelle: LLM-Trainingsdaten" - Answer from model's training data
  - "Quelle: Vector Cache" - Answer from semantic cache
  - "Quelle: Session Cache" - Answer from session-based cache
  - "Quelle: Web-Recherche" - Answer from fresh web research
- **Cache Inspection Scripts**:
  - `scripts/list_cache.py` - List all cached entries with timestamps
  - `scripts/search_cache.py` - Semantic similarity search in cache

#### Changed
- **Cache Distance Thresholds**: Stricter matching for better quality
  - `CACHE_DISTANCE_MEDIUM` lowered from 0.85 to 0.5
  - Preparation for RAG mode (0.5-1.2 range)
- **Cache Auto-Learning Logic** (`context_builder.py`):
  - Intelligent filtering before saving to cache
  - LLM evaluates if content is timeless or volatile
  - Debug messages for cache decision transparency
- **Volatile Keywords**: Moved to external file for easier maintenance
  - Now loaded from `prompts/cache_volatile_keywords.txt`
  - Multilingual file (German + English keywords)
  - Easy to edit without code changes
  - 68 keywords covering weather, finance, sports, news, time references

#### Fixed
- **UnboundLocalError**: Fixed duplicate `load_prompt` import causing variable shadowing
  - Removed local imports at lines 231 and 261 in `context_builder.py`
  - Now uses module-level import correctly
- **Deadlock in Cache Decision**: Fixed LLM client resource conflict
  - Was creating new LLMClient instances for cache decision
  - Now uses existing `automatik_llm_client` parameter
  - Prevents deadlock when Haupt-LLM just finished generating
  - Removed unnecessary `from ..llm_client import LLMClient` import

#### Technical Details
- **Cache Decision Flow**:
  1. Check user query for volatile keywords
  2. If keyword found → Ask LLM (can override to "cacheable")
  3. If no keyword → Ask LLM (default decision)
  4. Save to cache only if LLM approves
- **Source Display Format**:
  - Appears at end of AI answer (not user question)
  - Format: `(Inferenz: 2.5s, Quelle: <source>)`
  - Consistent across all answer types

---

## [1.3.0] - 2025-11-11

### 🚀 Pure Semantic Deduplication + Smart Cache for Explicit Research

#### Added
- **Smart Cache-Check for Explicit Research Keywords** (`conversation_handler.py`):
  - Cache check BEFORE web research for keywords like "recherchiere", "google", "suche im internet"
  - Distance < 0.05 (practically identical) → Use cache (0.15s instead of 100s)
  - Distance ≥ 0.05 → Perform fresh research
  - Transparent display: Shows cache age (e.g., "Cache-Hit (681s old, d=0.0000)")
  - User can still force fresh research via UI mode selection (Web-Suche schnell/tief)
- **ChromaDB Maintenance Tool** (`chroma_maintenance.py`):
  - Display cache statistics (entries, age, size)
  - Find duplicates (text similarity-based)
  - Remove duplicates (keeps newest entry)
  - Delete old entries (by age threshold)
  - Clear entire database
  - Dry-run mode for safe testing

#### Changed
- **Pure Semantic Deduplication** (No Time Dependencies):
  - Removed: `CACHE_TIME_THRESHOLD` (5-minute logic)
  - New: Always update semantic duplicates (distance < 0.3) regardless of age
  - Benefit: Consistent behavior, no race conditions, latest data guaranteed
  - Affected files: `vector_cache.py`, `config.py`, `conversation_handler.py`
- **Automatik-LLM Default Model**:
  - Changed from `qwen3:8b` to `qwen2.5:3b`
  - Performance: 2.7x faster decisions (0.3s instead of 0.8s)
  - VRAM: ~63% reduction (~3GB instead of ~8GB)
  - Main LLM remains `qwen3:8b` for final answers

#### Fixed
- **LLMResponse AttributeError** (`context_builder.py`):
  - Fixed: `response.get('message', {}).get('content', '')` → `response.text`
  - Issue: LLMResponse is dataclass with `.text` attribute, not a dict
  - Affected: Cache decision logic (2 locations)
- **10x Python Duplicates**:
  - Root cause: Time-based logic allowed duplicates after 5 minutes
  - Fix: Pure semantic deduplication always updates duplicates
  - Result: No more duplicate cache entries

#### Performance
- **Identical Research Query**: ~667x faster (0.15s instead of 100s) ✅
- **Automatik Decision**: 2.7x faster (0.3s instead of 0.8s) ✅
- **VRAM Savings**: ~63% less for Automatik-LLM ✅

#### Breaking Changes
None - Fully backwards compatible

---

## [Unreleased]

### Planned Features
- RAG mode improvements: Better relevance detection
- Cache statistics dashboard in UI
- Export/import cache entries
- Multi-language support for cache queries
- Background cache cleanup scheduler

---

**Note**: This is the first formal release with changelog tracking. Previous development history is available in git commit history.
