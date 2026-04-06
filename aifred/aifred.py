"""
AIfred Intelligence - Reflex Edition

Full Gradio-Style UI with Single Column Layout
"""

import reflex as rx
from .state import AIState, ChatHistoryState
from .theme import COLORS
from .lib.config import (
    UI_CHAT_HISTORY_MAX_HEIGHT_DESKTOP,
    UI_CHAT_HISTORY_MAX_HEIGHT_MOBILE,
    UI_THINKING_MAX_HEIGHT_DESKTOP,
    UI_THINKING_MAX_HEIGHT_MOBILE,
    UI_DEBUG_CONSOLE_MAX_HEIGHT,
    UI_SANDBOX_MAX_HEIGHT,
    UI_MOBILE_BREAKPOINT,
)

# UI modules (extracted from this file)
from .ui.helpers import t, left_column  # noqa: F401
from .ui.modals import (  # noqa: F401
    multi_agent_help_modal, research_help_modal, reasoning_thinking_help_modal,
    login_dialog, crop_modal, image_lightbox_modal,
    document_manager_modal, channel_credentials_modal, audit_log_modal,
)
from .ui.chat_display import (  # noqa: F401
    session_list_display, chat_history_display,
)
from .ui.input_sections import debug_console  # noqa: F401
from .ui.settings_accordion import settings_accordion  # noqa: F401
from .ui.agent_editor import agent_editor_modal  # noqa: F401


# ============================================================
# MAIN PAGE
# ============================================================

@rx.page(route="/", on_load=AIState.on_load, title="AIfred Intelligence")
def index() -> rx.Component:
    """Main page with single column layout for mobile optimization"""

    # Inline JavaScript for auto-scroll (must be inline to ensure execution)
    autoscroll_js = """
console.log('🔧 Autoscroll script loaded');

// Make all external links open in new tab
function makeLinksOpenInNewTab() {
    const links = document.querySelectorAll('a[href^="http"]');
    links.forEach(link => {
        if (!link.hasAttribute('target')) {
            link.setAttribute('target', '_blank');
            link.setAttribute('rel', 'noopener noreferrer');
        }
    });
}

// Force-scroll chat and debug to bottom (called after Hub updates)
function forceScrollToBottom() {
    if (!isAutoScrollEnabled()) return;
    requestAnimationFrame(() => {
        const chatBox = document.getElementById('chat-history-box');
        if (chatBox) {
            chatBox.scrollTop = chatBox.scrollHeight;
            trackScrollState(chatBox);
        }
        const debugBox = document.getElementById('debug-console-box');
        if (debugBox) {
            debugBox.scrollTop = debugBox.scrollHeight;
            trackScrollState(debugBox);
        }
    });
}

function isAutoScrollEnabled() {
    const sw = document.getElementById('autoscroll-switch');
    if (!sw) return true; // Default: enabled if switch not found
    // Radix UI setzt data-state auf den Button innerhalb des Switch-Containers
    const button = sw.querySelector('button[role="switch"]');
    if (!button) return sw.getAttribute('data-state') === 'checked'; // Fallback
    return button.getAttribute('data-state') === 'checked';
}

// Track scroll position BEFORE mutations happen via scroll events.
// The MutationObserver fires AFTER DOM changes, so checking isNearBottom
// inside the callback fails when a single mutation adds >150px of content
// (scrollHeight grows, distance to bottom exceeds threshold → no scroll).
const scrollState = new Map();  // element id → wasAtBottom

function trackScrollState(element) {
    if (!element || !element.id) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    scrollState.set(element.id, distance < 150);
}

function wasAtBottom(element) {
    if (!element || !element.id) return true;
    const state = scrollState.get(element.id);
    return state !== undefined ? state : true;  // default: scroll
}

function autoScrollElement(element) {
    if (!element) return;
    if (wasAtBottom(element)) {
        element.scrollTop = element.scrollHeight;
        // Update tracked state after scrolling
        trackScrollState(element);
    }
}

function attachScrollTracker(element) {
    if (!element || element._scrollTrackerAttached) return;
    element.addEventListener('scroll', () => trackScrollState(element), { passive: true });
    trackScrollState(element);  // initial state
    element._scrollTrackerAttached = true;
}

// Observer für Debug-Console und Chat-History Updates
// characterData NOT set — rx.text() streaming uses text node updates (not childList).
// A separate polling interval handles autoscroll during streaming.
const observerConfig = { childList: true, subtree: true };

// Track if chat-history-box observer is already running
let chatObserverAttached = false;

// Streaming autoscroll: poll-based scroll during active streaming.
// MutationObserver misses rx.text() updates (characterData only, not childList).
// Behavior: scroll down while user is near bottom. If user scrolls up, pause.
// If user scrolls back to bottom, resume. Same as Claude Code terminal behavior.
let streamingScrollInterval = null;
function startStreamingScroll() {
    if (streamingScrollInterval) return;
    streamingScrollInterval = setInterval(() => {
        if (!isAutoScrollEnabled()) return;
        const box = document.getElementById('chat-history-box');
        if (!box) return;
        // Only scroll if user is near the bottom (within 150px)
        // User scrolls up → distance grows → no auto-scroll
        // User scrolls back down → distance shrinks → auto-scroll resumes
        const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
        if (distance < 150) {
            box.scrollTop = box.scrollHeight;
        }
    }, 120);
}
function stopStreamingScroll() {
    if (streamingScrollInterval) {
        clearInterval(streamingScrollInterval);
        streamingScrollInterval = null;
    }
}

const callback = function(mutationsList, observer) {
    const enabled = isAutoScrollEnabled();

    // Make all links open in new tab (always, regardless of auto-scroll)
    makeLinksOpenInNewTab();

    // Get elements once
    const chatBox = document.getElementById('chat-history-box');

    // Lazy-attach observer to chat-history-box when it appears
    // (it's conditionally rendered after backend_initializing=False)
    // Also re-attach after reconnect (container gets destroyed + recreated)
    if (chatBox && (!chatObserverAttached || !chatBox._observerAttached)) {
        const chatObserver = new MutationObserver(callback);
        chatObserver.observe(chatBox, observerConfig);
        attachScrollTracker(chatBox);
        chatBox._observerAttached = true;
        chatObserverAttached = true;
        // Scroll to bottom on (re)attach — catches reconnect scenario.
        // Poll until content stabilizes (same approach as page-load).
        let _reattachLastH = 0;
        let _reattachStable = 0;
        const _reattachScroll = setInterval(() => {
            const cb = document.getElementById('chat-history-box');
            const db = document.getElementById('debug-console-box');
            if (cb) cb.scrollTop = cb.scrollHeight;
            if (db) db.scrollTop = db.scrollHeight;
            const h = cb ? cb.scrollHeight : 0;
            if (h === _reattachLastH) { _reattachStable++; } else { _reattachStable = 0; }
            _reattachLastH = h;
            if (_reattachStable >= 3) {
                clearInterval(_reattachScroll);
                if (cb) { cb.scrollTop = cb.scrollHeight; trackScrollState(cb); }
                if (db) { db.scrollTop = db.scrollHeight; trackScrollState(db); }
            }
        }, 300);
        setTimeout(() => clearInterval(_reattachScroll), 10000);
    }

    // Start/stop streaming scroll based on whether streaming box exists
    const streamingBox = document.getElementById('streaming-box');
    if (streamingBox) {
        startStreamingScroll();
    } else if (streamingScrollInterval) {
        // Streaming just ended → Markdown rendering will change scrollHeight.
        // Force a final scroll-to-bottom after a short delay to account for
        // content shrinking when streaming text gets replaced by rendered Markdown.
        stopStreamingScroll();
        setTimeout(() => {
            const box = document.getElementById('chat-history-box');
            if (box && isAutoScrollEnabled()) {
                box.scrollTop = box.scrollHeight;
                trackScrollState(box);
            }
        }, 300);
    }

    // Only scroll if auto-scroll is enabled
    if (!enabled) {
        return;
    }

    // Auto-scroll Debug Console
    const debugBox = document.getElementById('debug-console-box');
    if (debugBox) {
        attachScrollTracker(debugBox);
        autoScrollElement(debugBox);
    }

    // Auto-scroll Chat History
    if (chatBox) {
        autoScrollElement(chatBox);
    }
};

// Debug Console height is now controlled by CSS Grid (flex: 1)
// No manual JavaScript height sync needed - removed to prevent conflicts

function setupObservers() {
    console.log('🚀 Setting up observers...');

    const debugBox = document.getElementById('debug-console-box');
    if (debugBox) {
        console.log('✅ Found debug-console-box');
        const observer = new MutationObserver(callback);
        observer.observe(debugBox, observerConfig);
        attachScrollTracker(debugBox);
    } else {
        console.warn('❌ debug-console-box not found');
    }

    // Chat History - JavaScript-basiertes Autoscroll (statt rx.auto_scroll)
    // May not exist yet if backend is still initializing (rx.cond renders it later)
    if (!chatObserverAttached) {
        const chatBox = document.getElementById('chat-history-box');
        if (chatBox) {
            console.log('✅ Found chat-history-box');
            const chatObserver = new MutationObserver(callback);
            chatObserver.observe(chatBox, observerConfig);
            attachScrollTracker(chatBox);
            chatObserverAttached = true;
        } else {
            console.warn('❌ chat-history-box not found (will attach via debug-console callback)');
        }
    }

    // Sync heights on accordion open/close - observe settings-accordion by ID
    const settingsAccordion = document.getElementById('settings-accordion');
    if (settingsAccordion) {
        // Height sync removed - CSS Grid handles it automatically via flex: 1

        // Accordion observer removed - no manual height sync needed

        // Click handler removed - CSS Grid auto-adjusts height

        console.log('✅ Height sync observers attached to settings-accordion');
    } else {
        console.warn('⚠️ settings-accordion not found for observers');
    }

    // Window resize handler removed - CSS Grid handles responsive height
}

// Prevent page-level scroll jumps during State updates.
// When Reflex re-renders, the browser may scroll the page to the focused element.
// This preserves the page scroll position across re-renders.
let _pageScrollLock = false;
function enablePageScrollLock() {
    if (_pageScrollLock) return;
    _pageScrollLock = true;
    const savedY = window.scrollY;
    requestAnimationFrame(() => {
        if (window.scrollY !== savedY) {
            window.scrollTo(0, savedY);
        }
        _pageScrollLock = false;
    });
}
// Observe body for childList changes (Reflex re-renders) and restore scroll
const _bodyObserver = new MutationObserver(() => enablePageScrollLock());
_bodyObserver.observe(document.body, { childList: true, subtree: false });

// Initialize immediately or wait for DOMContentLoaded
function initialize() {
    console.log('📄 Initializing autoscroll...');

    // Make existing links open in new tab
    makeLinksOpenInNewTab();

    setupObservers();

    // Height sync removed - CSS Grid handles it automatically

    // Einmaliger Retry nach 1.5s für Elemente die erst nach Backend-Init erscheinen
    // (chat-history-box wird durch rx.cond erst gerendert wenn backend_initializing=False)
    setTimeout(() => {
        setupObservers();
        makeLinksOpenInNewTab();
    }, 1500);

    // On page load/reload: scroll chat + debug to bottom once content stabilizes.
    // Polls every 300ms until scrollHeight stops changing, then does one final scroll.
    let _lastChatHeight = 0;
    let _lastDebugHeight = 0;
    let _stableCount = 0;
    const _loadScrollInterval = setInterval(() => {
        const chatBox = document.getElementById('chat-history-box');
        const debugBox = document.getElementById('debug-console-box');
        const chatH = chatBox ? chatBox.scrollHeight : 0;
        const debugH = debugBox ? debugBox.scrollHeight : 0;

        // Scroll on every check (smooth catch-up)
        if (chatBox) { chatBox.scrollTop = chatBox.scrollHeight; }
        if (debugBox) { debugBox.scrollTop = debugBox.scrollHeight; }

        // Check if heights stabilized
        if (chatH === _lastChatHeight && debugH === _lastDebugHeight) {
            _stableCount++;
        } else {
            _stableCount = 0;
        }
        _lastChatHeight = chatH;
        _lastDebugHeight = debugH;

        // Stop after 3 stable checks (~900ms of no change) or max 10s
        if (_stableCount >= 3) {
            clearInterval(_loadScrollInterval);
            // Final scroll + track state
            if (chatBox) { chatBox.scrollTop = chatBox.scrollHeight; trackScrollState(chatBox); }
            if (debugBox) { debugBox.scrollTop = debugBox.scrollHeight; trackScrollState(debugBox); }
        }
    }, 300);
    // Safety: stop after 10s regardless
    setTimeout(() => clearInterval(_loadScrollInterval), 10000);
}

// Note: NO periodic setInterval for auto-scroll. The MutationObserver
// (childList + subtree) fires when React/Reflex replaces DOM subtrees
// during streaming. A permanent interval would prevent manual scroll-up
// because it re-enforces scrollTop every 200ms.

// Check if DOM is already loaded (script loaded late)
if (document.readyState === 'loading') {
    // DOM not yet loaded, wait for it
    document.addEventListener('DOMContentLoaded', initialize);
} else {
    // DOM already loaded, run immediately
    initialize();
}
"""

    # Paste handler for image support (desktop)
    paste_handler_js = """
console.log('📋 Image paste handler loaded');

// Global paste event handler for images
document.addEventListener('paste', async function(e) {
    console.log('📋 Paste event detected');
    const items = e.clipboardData.items;
    const imageFiles = [];

    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.type.startsWith('image/')) {
            console.log('🖼️ Image found in clipboard:', item.type);
            const file = item.getAsFile();
            if (file) {
                imageFiles.push(file);
            }
        }
    }

    if (imageFiles.length > 0) {
        e.preventDefault();
        console.log('📸 Triggering upload for', imageFiles.length, 'image(s)');

        // Trigger Reflex upload handler
        const uploadEl = document.getElementById('image-upload');
        if (uploadEl) {
            // Simulate file drop event
            const dataTransfer = new DataTransfer();
            imageFiles.forEach(f => dataTransfer.items.add(f));

            const dropEvent = new DragEvent('drop', {
                dataTransfer: dataTransfer,
                bubbles: true,
                cancelable: true
            });

            uploadEl.dispatchEvent(dropEvent);
            console.log('✅ Upload event dispatched');
        } else {
            console.error('❌ Upload element not found');
        }
    }
});

console.log('✅ Paste handler initialized');
"""

    # JavaScript für Crop-Funktionalität
    crop_js = """
console.log('✂️ Crop handler loaded');

// Crop-Box Drag-Funktionalität
(function() {
    let isDragging = false;
    let dragType = null; // 'move', 'nw', 'ne', 'sw', 'se', 'n', 's', 'w', 'e'
    let startX, startY;
    let startBox = { x: 0, y: 0, width: 100, height: 100 };
    let currentBox = { x: 0, y: 0, width: 100, height: 100 };
    let listenersAdded = false;

    function initCrop() {
        const overlay = document.getElementById('crop-overlay');
        const cropBox = document.getElementById('crop-box');
        const image = document.getElementById('crop-image');
        const container = document.getElementById('crop-container');

        if (!overlay || !cropBox || !image || !container) {
            return; // Modal nicht offen
        }

        // WICHTIG: Body-Scroll blockieren und nach oben scrollen
        document.body.style.overflow = 'hidden';
        document.documentElement.style.overflow = 'hidden';
        window.scrollTo(0, 0);  // Scroll nach oben
        console.log('✂️ Body scroll disabled, scrolled to top');

        // Positioniere Overlay auf Bildgröße (wichtig für object-fit: contain)
        function positionOverlay() {
            const containerRect = container.getBoundingClientRect();
            const imageRect = image.getBoundingClientRect();

            // Berechne Offset des Bildes innerhalb des Containers
            const offsetLeft = imageRect.left - containerRect.left;
            const offsetTop = imageRect.top - containerRect.top;

            overlay.style.left = offsetLeft + 'px';
            overlay.style.top = offsetTop + 'px';
            overlay.style.width = imageRect.width + 'px';
            overlay.style.height = imageRect.height + 'px';

            console.log('✂️ Overlay positioned on image:', imageRect.width.toFixed(0), 'x', imageRect.height.toFixed(0));
        }

        // Warte auf Bild-Load
        if (image.complete && image.naturalWidth > 0) {
            setTimeout(positionOverlay, 50); // Kurze Verzögerung für Layout
        } else {
            image.onload = () => setTimeout(positionOverlay, 50);
        }

        // Initial: Ganzes Bild selektiert
        currentBox = { x: 0, y: 0, width: 100, height: 100 };
        updateCropBoxUI();

        // Nur einmal Listener hinzufügen
        if (listenersAdded) return;
        listenersAdded = true;

        // Event Listener für Crop-Box (move)
        cropBox.addEventListener('mousedown', (e) => {
            if (e.target === cropBox) {
                startDrag(e, 'move');
                e.preventDefault();
            }
        });
        cropBox.addEventListener('touchstart', (e) => {
            if (e.target === cropBox) {
                startDrag(e.touches[0], 'move');
                e.preventDefault();
                e.stopPropagation();
            }
        }, { passive: false });

        // Event Listener für Handles
        const handles = ['nw', 'ne', 'sw', 'se', 'n', 's', 'w', 'e'];
        handles.forEach(h => {
            const handle = document.getElementById('crop-' + h);
            if (handle) {
                handle.addEventListener('mousedown', (e) => {
                    startDrag(e, h);
                    e.preventDefault();
                    e.stopPropagation();
                });
                handle.addEventListener('touchstart', (e) => {
                    startDrag(e.touches[0], h);
                    e.preventDefault();
                    e.stopPropagation();
                }, { passive: false });
            }
        });

        // Global mouse/touch events
        document.addEventListener('mousemove', onDrag);
        document.addEventListener('mouseup', endDrag);
        document.addEventListener('touchmove', (e) => {
            if (isDragging) {
                onDrag(e.touches[0]);
                e.preventDefault();
                e.stopPropagation();
            }
        }, { passive: false });
        document.addEventListener('touchend', endDrag);
        document.addEventListener('touchcancel', endDrag);

        console.log('✂️ Crop initialized with touch support');
    }

    function startDrag(e, type) {
        isDragging = true;
        dragType = type;
        startX = e.clientX;
        startY = e.clientY;
        startBox = { ...currentBox };
        // Verhindere Selektion während Drag
        document.body.style.userSelect = 'none';
        document.body.style.webkitUserSelect = 'none';
    }

    function onDrag(e) {
        if (!isDragging) return;

        const overlay = document.getElementById('crop-overlay');
        if (!overlay) return;

        const rect = overlay.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;

        const deltaX = ((e.clientX - startX) / rect.width) * 100;
        const deltaY = ((e.clientY - startY) / rect.height) * 100;

        let newBox = { ...startBox };

        switch(dragType) {
            case 'move':
                newBox.x = Math.max(0, Math.min(100 - startBox.width, startBox.x + deltaX));
                newBox.y = Math.max(0, Math.min(100 - startBox.height, startBox.y + deltaY));
                break;
            case 'nw':
                newBox.x = Math.max(0, Math.min(startBox.x + startBox.width - 10, startBox.x + deltaX));
                newBox.y = Math.max(0, Math.min(startBox.y + startBox.height - 10, startBox.y + deltaY));
                newBox.width = startBox.width - (newBox.x - startBox.x);
                newBox.height = startBox.height - (newBox.y - startBox.y);
                break;
            case 'ne':
                newBox.y = Math.max(0, Math.min(startBox.y + startBox.height - 10, startBox.y + deltaY));
                newBox.width = Math.max(10, Math.min(100 - startBox.x, startBox.width + deltaX));
                newBox.height = startBox.height - (newBox.y - startBox.y);
                break;
            case 'sw':
                newBox.x = Math.max(0, Math.min(startBox.x + startBox.width - 10, startBox.x + deltaX));
                newBox.width = startBox.width - (newBox.x - startBox.x);
                newBox.height = Math.max(10, Math.min(100 - startBox.y, startBox.height + deltaY));
                break;
            case 'se':
                newBox.width = Math.max(10, Math.min(100 - startBox.x, startBox.width + deltaX));
                newBox.height = Math.max(10, Math.min(100 - startBox.y, startBox.height + deltaY));
                break;
            case 'n':
                newBox.y = Math.max(0, Math.min(startBox.y + startBox.height - 10, startBox.y + deltaY));
                newBox.height = startBox.height - (newBox.y - startBox.y);
                break;
            case 's':
                newBox.height = Math.max(10, Math.min(100 - startBox.y, startBox.height + deltaY));
                break;
            case 'w':
                newBox.x = Math.max(0, Math.min(startBox.x + startBox.width - 10, startBox.x + deltaX));
                newBox.width = startBox.width - (newBox.x - startBox.x);
                break;
            case 'e':
                newBox.width = Math.max(10, Math.min(100 - startBox.x, startBox.width + deltaX));
                break;
        }

        currentBox = newBox;
        updateCropBoxUI();
    }

    function endDrag() {
        if (isDragging) {
            isDragging = false;
            dragType = null;
            document.body.style.userSelect = '';
            document.body.style.webkitUserSelect = '';
        }
    }

    function updateCropBoxUI() {
        const cropBox = document.getElementById('crop-box');
        if (cropBox) {
            cropBox.style.left = currentBox.x + '%';
            cropBox.style.top = currentBox.y + '%';
            cropBox.style.width = currentBox.width + '%';
            cropBox.style.height = currentBox.height + '%';
        }
    }

    // Reset bei Modal-Schließung
    function resetCrop() {
        currentBox = { x: 0, y: 0, width: 100, height: 100 };
        listenersAdded = false;
        isDragging = false;
        // Body-Scroll wieder aktivieren
        document.body.style.overflow = '';
        document.documentElement.style.overflow = '';
        console.log('✂️ Body scroll re-enabled');
    }

    // Observer für Modal-Öffnung/Schließung
    const observer = new MutationObserver((mutations) => {
        const overlay = document.getElementById('crop-overlay');
        if (overlay) {
            initCrop();
        } else {
            resetCrop();
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });
})();
"""

    return rx.box(
        # Inline JavaScript (guaranteed to execute)
        rx.script(autoscroll_js),
        rx.script(paste_handler_js),
        rx.script(crop_js),

        # Load custom.js for MediaRecorder and other features (cache-busting version)
        rx.script(src="/custom.js?v=19"),

        # Login Dialog (rendered but hidden until needed)
        login_dialog(),

        # Crop Modal (rendered but hidden until open)
        crop_modal(),

        # Image Lightbox Modal (for viewing history images full-size)
        image_lightbox_modal(),

        # Multi-Agent Help Modal (Diskussionsmodi-Übersicht)
        multi_agent_help_modal(),

        # Research Mode Help Modal
        research_help_modal(),

        # Reasoning/Thinking Help Modal
        reasoning_thinking_help_modal(),

        # Agent Editor Modal
        agent_editor_modal(),

        # Document Manager Modal
        document_manager_modal(),

        # Email Credentials Modal (Message Hub)
        channel_credentials_modal(),
        audit_log_modal(),

        # Hidden element to trigger camera detection on mount
        rx.box(
            id="camera-detector",
            display="none",
            on_mount=[
                # Camera Detection
                rx.call_script(
                    """
                    (async () => {
                        try {
                            if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                                const devices = await navigator.mediaDevices.enumerateDevices();
                                const hasCamera = devices.some(device => device.kind === 'videoinput');
                                console.log('📷 Camera detected:', hasCamera);
                                return hasCamera;
                            }
                        } catch (err) {
                            console.log('⚠️ Camera detection failed:', err);
                        }
                        return false;
                    })()
                    """,
                    callback=AIState.set_camera_available
                ),
                # Mobile Detection (User-Agent + Touch)
                rx.call_script(
                    """
                    (() => {
                        // Check User-Agent for mobile devices
                        const userAgent = navigator.userAgent || navigator.vendor || window.opera;
                        const mobileRegex = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini|mobile/i;
                        const isMobileUA = mobileRegex.test(userAgent.toLowerCase());

                        // Check for touch support
                        const hasTouch = ('ontouchstart' in window) ||
                                        (navigator.maxTouchPoints > 0) ||
                                        (navigator.msMaxTouchPoints > 0);

                        // Mobile = Mobile UA + Touch support
                        const isMobile = isMobileUA && hasTouch;

                        console.log('📱 Mobile detection:', {
                            userAgent: userAgent,
                            isMobileUA: isMobileUA,
                            hasTouch: hasTouch,
                            maxTouchPoints: navigator.maxTouchPoints,
                            isMobile: isMobile
                        });

                        return isMobile;
                    })()
                    """,
                    callback=AIState.set_is_mobile
                )
            ]
        ),

        rx.vstack(
            # Header with title and user info
            rx.hstack(
                # Left side: Title and subtitle
                rx.vstack(
                    rx.hstack(
                        rx.image(src="/AIfred-Zylinder.png", width="32px", height="32px"),
                        rx.heading("AIfred Intelligence", size="6"),
                        align="center",
                        spacing="2",
                        margin_bottom="2",
                    ),
                    rx.text(
                        t("subtitle"),
                        color=COLORS["text_secondary"],
                        font_size="12px",
                        font_style="italic",
                    ),
                    align_items="flex-start",
                    spacing="0",
                ),
                rx.spacer(),
                # Right side: Agent editor + User info + logout (only when logged in)
                rx.cond(
                    AIState.logged_in_user != "",
                    rx.hstack(
                        # Settings menu button (Agent Editor + Memory + Database)
                        rx.tooltip(
                            rx.icon(
                                "menu",
                                size=18,
                                color="#d98030",
                                cursor="pointer",
                                on_click=AIState.open_agent_editor,
                                style={
                                    "transition": "color 0.2s ease",
                                    "&:hover": {"color": "#FFD700"},
                                },
                            ),
                            content=t("settings_menu_title"),
                        ),
                        rx.text(
                            AIState.logged_in_user,
                            font_size="14px",
                            font_weight="500",
                            color=COLORS["text_primary"],
                        ),
                        rx.button(
                            t("logout"),
                            on_click=AIState.do_logout,
                            size="1",
                            variant="soft",
                            color_scheme="gray",
                            cursor="pointer",
                        ),
                        spacing="3",
                        align="center",
                    ),
                ),
                width="100%",
                align="center",
                margin_bottom="4",
            ),

            # Session Picker (saved chats) - collapsible, above chat history
            session_list_display(),

            # Chat History (top - read conversation first)
            # NOTE: Failed sources are now displayed inline within each message (persistent)
            # NOTE: Sokrates now streams directly into chat_history (no separate panel)
            chat_history_display(),

            # TTS Audio Player - shows when TTS enabled AND chat history exists
            # This allows "Neu generieren" after app restart (before any audio generated)
            rx.cond(
                AIState.enable_tts & (ChatHistoryState.chat_history.length() > 0),
                rx.box(
                    rx.hstack(
                        rx.text("🔊", font_size="18px"),
                        rx.text(t("tts_player_label"), font_weight="bold", font_size="13px", color=COLORS["accent_blue"]),
                        rx.spacer(),
                        # Regenerate ALL TTS Button - re-synthesize all bubbles with current voice settings
                        rx.button(
                            rx.cond(
                                AIState.tts_regenerating,
                                rx.hstack(
                                    rx.spinner(size="1"),
                                    rx.text(t("tts_regenerate_all")),
                                    spacing="2",
                                    align="center",
                                ),
                                rx.text(t("tts_regenerate_all")),
                            ),
                            on_click=AIState.resynthesize_all_tts,
                            size="1",
                            variant="soft",
                            color_scheme="blue",
                            cursor="pointer",
                            disabled=AIState.tts_regenerating,
                            # Slight transparency when regenerating (not full loading state)
                            opacity=rx.cond(AIState.tts_regenerating, "0.7", "1"),
                        ),
                        spacing="2",
                        align="center",
                    ),
                    # HTML5 Audio Element - ALWAYS rendered to avoid React Hook order issues
                    # Use display:none instead of rx.cond to prevent mount/unmount cycles
                    rx.el.audio(
                        src=AIState.tts_audio_path,
                        id="tts-audio-player",
                        controls=True,
                        # Only autoplay when AutoPlay is ON and path exists
                        autoPlay=AIState.tts_autoplay & (AIState.tts_audio_path != ""),
                        key="tts-audio-" + AIState.tts_trigger_counter.to(str),  # Force remount on new audio
                        # Set playback rate from agent settings via data attribute
                        # JavaScript reads this on play event
                        **{"data-playback-rate": AIState.tts_playback_rate},  # type: ignore[arg-type]
                        style={
                            "width": "100%",
                            "height": "40px",
                            "margin_top": "8px",
                            # Visible when: tts_audio_path OR tts_audio_queue has items
                            "display": rx.cond(AIState.tts_player_visible, "block", "none"),
                        },
                    ),
                    # Hidden element for TTS queue data - JavaScript reads this to update local queue
                    # The MutationObserver in custom.js watches for changes to data-queue attribute
                    # data-polling triggers start/stop of API polling for streaming TTS
                    rx.el.div(
                        id="tts-queue-data",
                        **{
                            "data-queue": AIState.tts_queue_json,
                            "data-version": AIState.tts_queue_version.to(str),
                            "data-autoplay": rx.cond(AIState.tts_autoplay, "true", "false"),
                            "data-polling": rx.cond(
                                AIState.is_generating & AIState.enable_tts & AIState.tts_streaming_enabled,
                                "true",
                                "false"
                            ),
                        },
                        style={"display": "none"},
                    ),
                    # Placeholder when no audio yet - shows hint (inverse visibility)
                    rx.text(
                        t("tts_regenerate_hint"),
                        font_size="11px",
                        color="#888",
                        margin_top="8px",
                        font_style="italic",
                        # Hide when player is visible (audio playing or queued)
                        display=rx.cond(AIState.tts_player_visible, "none", "block"),
                    ),
                    padding="3",
                    background_color="rgba(66, 135, 245, 0.08)",
                    border_radius="8px",
                    border=f"1px solid {COLORS['accent_blue']}",
                    width="100%",
                    margin_top="4",
                    margin_bottom="4",
                ),
            ),

            # Input controls (below chat history for easy access after reading)
            rx.box(
                left_column(),
                padding="4",
                background_color=COLORS["card_bg"],
                border_radius="12px",
                border=f"1px solid {COLORS['border']}",
                width="100%",
            ),

            # Debug Console & Settings side-by-side (bottom)
            # Desktop: Debug Console flexibel (1fr), Settings schmaler (max 360px)
            # Mobile: Automatisches Umbrechen via CSS Container Query (custom.css)
            rx.box(
                rx.box(
                    debug_console(),
                    settings_accordion(),
                    class_name="debug-settings-grid",
                    width="100%",
                ),
                class_name="debug-settings-container",
                width="100%",
            ),

            spacing="4",
            width="100%",
            padding="16",  # Padding rundherum (64px) - deutlich größer!
            max_width="1200px",  # Festgelegte maximale Breite
            margin="0 auto",  # Zentriert
            background_color=COLORS["page_bg"],  # Explizite Hintergrundfarbe
        ),

        width="100%",
        min_height="100vh",
        background_color=COLORS["page_bg"],
        display="flex",
        justify_content="center",
    )


# ==============================================================
# Note: Automatik-LLM Preloading moved to State.on_load()
# ==============================================================
# Model preloading now happens in state.py initialize_backend()
# when the user first opens the page. This ensures:
# 1. Models are loaded from State settings (not hardcoded)
# 2. Available models list is populated before preloading
# 3. Everything happens in one place (cleaner architecture)


# Create app (API routes are mounted separately below)
app = rx.App(
    stylesheets=[
        "/custom.css",  # Custom CSS for dark theme
    ],
    head_components=[
        # SVG Favicon - uses system emoji font for consistent 🎩 display
        rx.el.link(rel="icon", type="image/svg+xml", href="/favicon.svg"),
        # CSS Custom Properties - inject UI layout constants from config.py
        rx.el.style(f"""
            :root {{
                --chat-max-height-desktop: {UI_CHAT_HISTORY_MAX_HEIGHT_DESKTOP};
                --chat-max-height-mobile: {UI_CHAT_HISTORY_MAX_HEIGHT_MOBILE};
                --thinking-max-height-desktop: {UI_THINKING_MAX_HEIGHT_DESKTOP};
                --thinking-max-height-mobile: {UI_THINKING_MAX_HEIGHT_MOBILE};
                --debug-max-height: {UI_DEBUG_CONSOLE_MAX_HEIGHT};
                --sandbox-max-height: {UI_SANDBOX_MAX_HEIGHT};
                --mobile-breakpoint: {UI_MOBILE_BREAKPOINT};
            }}
        """),
    ],
)

# Mount REST API routes directly on Reflex's backend
# This avoids the "ASGI flow error: Connection already upgraded" bug
# that occurs when using api_transformer with WebSocket connections
from .lib.api import api_app  # noqa: E402
assert app._api is not None
app._api.mount("/api", api_app)

# Mount static file directories from data/
# All user data is stored in data/ which is excluded from hot-reload
from starlette.staticfiles import StaticFiles  # noqa: E402
from .lib.config import DATA_DIR  # noqa: E402

# Mount images directory for Vision uploads
images_dir = DATA_DIR / "images"
images_dir.mkdir(parents=True, exist_ok=True)
app._api.mount("/_upload/images", StaticFiles(directory=str(images_dir)), name="uploaded_images")

# Mount html_preview directory for share_chat feature
html_preview_dir = DATA_DIR / "html_preview"
html_preview_dir.mkdir(parents=True, exist_ok=True)
app._api.mount("/_upload/html_preview", StaticFiles(directory=str(html_preview_dir)), name="html_preview")

# Mount sandbox_output directory for interactive code execution results
sandbox_output_dir = DATA_DIR / "sandbox_output"
sandbox_output_dir.mkdir(parents=True, exist_ok=True)
app._api.mount("/_upload/sandbox_output", StaticFiles(directory=str(sandbox_output_dir)), name="sandbox_output")

# Mount tts_audio directory for TTS playback (temporary chunks)
tts_audio_dir = DATA_DIR / "tts_audio"
tts_audio_dir.mkdir(parents=True, exist_ok=True)
app._api.mount("/_upload/tts_audio", StaticFiles(directory=str(tts_audio_dir)), name="tts_audio")

# Mount audio directory for permanent session audio (replay button)
session_audio_dir = DATA_DIR / "audio"
session_audio_dir.mkdir(parents=True, exist_ok=True)
app._api.mount("/_upload/audio", StaticFiles(directory=str(session_audio_dir)), name="session_audio")

# Mount documents directory for document download
documents_dir = DATA_DIR / "documents"
documents_dir.mkdir(parents=True, exist_ok=True)
app._api.mount("/_upload/documents", StaticFiles(directory=str(documents_dir)), name="uploaded_documents")

# ---------------------------------------------------------------------------
# Message Hub — background workers for channel listeners (email, discord, …)
# ---------------------------------------------------------------------------
import contextlib  # noqa: E402


@contextlib.asynccontextmanager
async def _message_hub_lifespan():
    """Start Message Hub on ASGI startup, stop on shutdown.

    This handles the initial start. on_load() provides a safety net
    for Granian worker respawns where the lifespan doesn't re-run.
    """
    from .lib.message_hub import message_hub, register_channel_workers  # noqa: E402
    from .lib.scheduler import scheduler_loop  # noqa: E402

    register_channel_workers(message_hub)
    if not message_hub.is_running("scheduler"):
        message_hub.register("scheduler", scheduler_loop)
    await message_hub.start_all()
    try:
        yield
    finally:
        await message_hub.stop_all()


def _register_message_hub_workers(hub: object) -> None:
    """Register all channel listener workers with the Message Hub.

    Delegates to register_channel_workers() in message_hub module.
    Kept for backwards compatibility with settings mixin.
    """
    from .lib.message_hub import register_channel_workers  # noqa: E402
    register_channel_workers(hub)  # type: ignore[arg-type]


app.register_lifespan_task(_message_hub_lifespan)
