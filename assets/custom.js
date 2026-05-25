// custom.js - Zusätzliche Frontend-Funktionen
// Auto-Scroll wird in aifred.py inline JS gehandhabt (mit ID-basiertem Switch-Check)

console.log('🔧 custom.js loaded');

// ============================================================
// MEDIARECORDER IMPLEMENTATION FOR LIVE AUDIO RECORDING
// ============================================================

var mediaRecorder = null;
var audioChunks = [];
var isRecording = false;
var audioStream = null;

// Audio feedback function - plays beep sounds
function playBeep(frequency = 800, duration = 100) {
    try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const oscillator = audioContext.createOscillator();
        const gainNode = audioContext.createGain();

        oscillator.connect(gainNode);
        gainNode.connect(audioContext.destination);

        oscillator.frequency.value = frequency;
        oscillator.type = 'sine';

        gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + duration / 1000);

        oscillator.start(audioContext.currentTime);
        oscillator.stop(audioContext.currentTime + duration / 1000);

        console.log(`🔊 Beep played: ${frequency}Hz, ${duration}ms`);
    } catch (error) {
        console.warn('⚠️ Could not play beep sound:', error);
    }
}

// Start recording
async function startAudioRecording() {
    try {
        console.log('🎤 Requesting microphone access...');
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });

        audioStream = stream;
        mediaRecorder = new MediaRecorder(stream, {
            mimeType: 'audio/webm;codecs=opus'
        });
        audioChunks = [];

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
                console.log('📦 Audio chunk received:', event.data.size, 'bytes');
            }
        };

        mediaRecorder.onstop = async () => {
            console.log('⏹️ Recording stopped, processing audio...');
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            console.log('🎵 Audio blob created:', audioBlob.size, 'bytes');

            // Upload audio blob to backend
            await uploadAudioBlob(audioBlob);

            // Stop all tracks to release microphone
            if (audioStream) {
                audioStream.getTracks().forEach(track => {
                    track.stop();
                    console.log('🛑 Stopped audio track');
                });
                audioStream = null;
            }
        };

        mediaRecorder.start();
        isRecording = true;
        updateRecordingButton(true);
        playBeep(1000, 150); // Higher pitch for START (1000Hz, 150ms)
        console.log('🔴 Recording started');

    } catch (error) {
        console.error('❌ MediaRecorder error:', error);
        alert('Mikrofon-Zugriff verweigert! Bitte erlauben Sie den Mikrofon-Zugriff in Ihren Browser-Einstellungen.');
        isRecording = false;
        updateRecordingButton(false);
    }
}

// Stop recording
function stopAudioRecording() {
    if (mediaRecorder && isRecording) {
        console.log('⏸️ Stopping recording...');
        playBeep(600, 200); // Lower pitch for STOP (600Hz, 200ms)
        mediaRecorder.stop();
        isRecording = false;
        updateRecordingButton(false);
    }
}

// Toggle recording
function toggleRecording() {
    console.log('🟢 toggleRecording() called, current state:', isRecording);
    if (isRecording) {
        stopAudioRecording();
    } else {
        startAudioRecording();
    }
}

// Make toggleRecording available globally for Reflex rx.call_script()
window.toggleRecording = toggleRecording;

// Upload blob to backend via hidden upload component
async function uploadAudioBlob(blob) {
    try {
        console.log('📤 Uploading audio blob to backend...');

        // Find the hidden upload input
        const uploadInput = document.querySelector('#audio-recording-upload input[type="file"]');

        if (!uploadInput) {
            console.error('❌ Upload input not found! Make sure #audio-recording-upload exists in the DOM.');
            return;
        }

        // Create File from Blob
        const file = new File([blob], 'recording.webm', { type: 'audio/webm' });

        // Use DataTransfer to set files property
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(file);
        uploadInput.files = dataTransfer.files;

        console.log('✅ File set on input:', uploadInput.files[0].name, uploadInput.files[0].size, 'bytes');

        // Trigger change event to notify Reflex
        const changeEvent = new Event('change', { bubbles: true });
        uploadInput.dispatchEvent(changeEvent);

        console.log('🚀 Change event dispatched to Reflex');

    } catch (error) {
        console.error('❌ Upload error:', error);
    }
}

// Update button UI state
function updateRecordingButton(recording) {
    const btn = document.querySelector('#recording-button');
    if (btn) {
        // Fixed width for both Aufnahme and Stop states
        btn.style.width = '160px';

        // .recording-Klasse togglen — das CSS &.recording in
        // input_sections.py (mit !important) übernimmt dann das
        // Rot. Inline-Styles werden gelöscht, damit das CSS nicht
        // gegen sie kämpft.
        btn.style.backgroundColor = '';
        btn.style.color = '';
        if (recording) {
            btn.classList.add('recording');
            const textElement = btn.querySelector('.rt-Text');
            if (textElement) textElement.textContent = 'Stop';
            console.log('🔴 Button updated to RECORDING state');
        } else {
            btn.classList.remove('recording');
            const textElement = btn.querySelector('.rt-Text');
            if (textElement) textElement.textContent = 'Aufnahme';
            console.log('🟢 Button updated to IDLE state');
        }
    } else {
        console.warn('⚠️ Recording button #recording-button not found');
    }
}

// ============================================================
// TTS AUTO-PLAY (Direct call from backend via rx.call_script)
// ============================================================

// Track currently playing audio to allow stopping
var currentTtsAudio = null;
var lastPlayedTtsUrl = '';

/**
 * Play TTS audio from URL - uses the VISIBLE HTML5 player for full user control
 *
 * The visible player (`#tts-audio-player`) is the single source of truth.
 * This allows the user to control playback with native HTML5 controls (pause, seek, volume).
 *
 * @param {string} audioUrl - URL path like '/tts_audio/audio_123.mp3'
 */
function playTtsFromUrl(audioUrl) {
    console.log('🔊 TTS: playTtsFromUrl called with', audioUrl);

    // Stop any old hidden audio (cleanup from previous implementation)
    if (currentTtsAudio) {
        console.log('⏹️ TTS: Stopping old hidden audio');
        currentTtsAudio.pause();
        currentTtsAudio.src = '';
        currentTtsAudio = null;
    }

    // Skip if same URL already playing on visible player
    const player = document.getElementById('tts-audio-player');
    if (player) {
        // Check if same audio already playing
        if (player.src === audioUrl || player.src.endsWith(audioUrl.split('/').pop())) {
            if (!player.paused && !player.ended) {
                console.log('⚠️ TTS: Same audio already playing on visible player, skipping');
                return;
            }
        }

        // Apply playback rate from data attribute (set by agent settings) or global default
        const dataRate = player.dataset.playbackRate;
        if (dataRate) {
            const rate = parseFloat(dataRate.replace('x', ''));
            if (!isNaN(rate) && rate > 0) {
                ttsPlaybackRate = rate;
            }
        }
        player.playbackRate = ttsPlaybackRate;
        console.log('🔊 TTS: Applied playback rate', ttsPlaybackRate);

        // Set source if different
        if (!player.src.endsWith(audioUrl.split('/').pop())) {
            player.src = audioUrl;
            player.load();
        }

        // Play the visible player
        player.play()
            .then(() => {
                console.log('✅ TTS: Playback started on visible player');
            })
            .catch(err => {
                console.warn('⚠️ TTS: Autoplay blocked:', err.message);
                console.log('ℹ️ TTS: User can click play on the visible player');
            });
    } else {
        console.warn('⚠️ TTS: Visible player not found, creating fallback audio');
        // Fallback: Create hidden audio only if visible player doesn't exist
        const audio = new Audio();
        currentTtsAudio = audio;
        audio.playbackRate = ttsPlaybackRate;
        audio.src = audioUrl;
        audio.play().catch(err => console.warn('⚠️ TTS fallback autoplay blocked:', err.message));
    }

    lastPlayedTtsUrl = audioUrl;
}

/**
 * Update the visible HTML5 audio player (fallback for manual playback)
 */
function updateVisiblePlayer(audioUrl) {
    const player = document.getElementById('tts-audio-player');
    if (player && player.src !== audioUrl) {
        player.src = audioUrl;
        player.load();
        console.log('🔊 TTS: Updated visible player for manual playback');
    }
}

/**
 * Stop TTS playback - stops both visible player and any hidden fallback audio
 */
function stopTts() {
    // Stop double-buffered queue playback (the actual audio path for streaming)
    clearTtsQueue();

    // Stop visible player (used by bubble audio replay)
    const player = document.getElementById('tts-audio-player');
    if (player) {
        player.pause();
        player.currentTime = 0;
    }

    // Stop hidden fallback audio (if any)
    if (currentTtsAudio) {
        currentTtsAudio.pause();
        currentTtsAudio.src = '';
        currentTtsAudio = null;
    }

    console.log('⏹️ TTS: Stopped all playback');
}

// Legacy function for compatibility (DOM observer based)
function playTtsAudio() {
    const player = document.getElementById('tts-audio-player');
    if (player && player.src && player.src.includes('/tts_audio/')) {
        playTtsFromUrl(player.src);
    }
}

// ============================================================
// TTS PLAYBACK RATE (Persistent browser-side speed setting)
// ============================================================

// Store current playback rate (persisted via backend)
var ttsPlaybackRate = 1.0;  // Default (speed via Agent Settings)

/**
 * Set TTS playback rate - called from Python backend via rx.call_script()
 * Also applies to any currently playing audio
 * @param {number} rate - Playback rate (0.5, 0.75, 1, 1.25, 1.5, 2)
 */
function setTtsPlaybackRate(rate) {
    ttsPlaybackRate = parseFloat(rate);
    console.log('🔊 TTS: Playback rate set to', ttsPlaybackRate);

    // Apply to currently playing audio
    if (currentTtsAudio) {
        currentTtsAudio.playbackRate = ttsPlaybackRate;
        console.log('🔊 TTS: Applied rate to current audio');
    }

    // Apply to visible HTML5 player
    const player = document.getElementById('tts-audio-player');
    if (player) {
        player.playbackRate = ttsPlaybackRate;
        console.log('🔊 TTS: Applied rate to visible player');
    }
}

/**
 * Get current playback rate
 * @returns {number} Current playback rate
 */
function getTtsPlaybackRate() {
    return ttsPlaybackRate;
}

// Make available globally
window.playTtsFromUrl = playTtsFromUrl;
window.playTtsAudio = playTtsAudio;
window.stopTts = stopTts;
window.setTtsPlaybackRate = setTtsPlaybackRate;
window.getTtsPlaybackRate = getTtsPlaybackRate;

// ============================================================
// BUBBLE AUDIO PLAYBACK - Replay audio from chat bubbles
// ============================================================

/**
 * Play audio from a chat bubble button
 * Extracts audio_urls from the button's data attribute and plays the last one
 * @param {HTMLElement} button - The button element clicked
 */
function playBubbleAudioFromButton(button) {
    if (!button) {
        console.warn('playBubbleAudioFromButton: No button provided');
        return;
    }

    // audio_urls_json is a proper JSON array string
    const audioUrlsJson = button.dataset.audioUrls;
    if (!audioUrlsJson) {
        console.warn('playBubbleAudioFromButton: No audio URLs in data attribute');
        return;
    }

    try {
        const audioUrls = JSON.parse(audioUrlsJson);

        if (!Array.isArray(audioUrls) || audioUrls.length === 0) {
            console.log('🔊 Bubble Audio: Empty audio URLs array');
            return;
        }

        // Play the last audio URL (most complete chunk)
        const audioUrl = audioUrls[audioUrls.length - 1];
        console.log('🔊 Bubble Audio: Playing', audioUrl);
        playBubbleAudio(audioUrl);
    } catch (e) {
        console.warn('playBubbleAudioFromButton: Failed to parse JSON:', e, audioUrlsJson);
    }
}

// Bubble audio playback state
var bubbleAudioUrls = [];
var bubbleAudioIndex = 0;
var bubbleAudioPlaying = false;
var bubbleAudioActiveBtn = null;  // Button that triggered current playback
var bubbleAudioElement = null;    // Current Audio element (for stop when player is hidden)

/**
 * Play all audio URLs from a bubble sequentially
 * @param {string[]} audioUrls - Array of audio URLs to play
 */
function playBubbleAudioAll(audioUrls) {
    if (!audioUrls || !Array.isArray(audioUrls) || audioUrls.length === 0) {
        console.warn('playBubbleAudioAll: No audio URLs provided');
        return;
    }

    console.log(`🔊 Bubble Audio: Playing ${audioUrls.length} chunks sequentially`);

    // Stop any current playback
    if (bubbleAudioElement) {
        bubbleAudioElement.pause();
        bubbleAudioElement.onended = null;
        bubbleAudioElement = null;
    }

    // Stop TTS queue if playing
    if (ttsQueuePlaying) {
        console.log('🔊 Bubble Audio: Stopping TTS queue playback');
        ttsQueuePlaying = false;
    }

    // Setup bubble playback state
    bubbleAudioUrls = audioUrls;
    bubbleAudioIndex = 0;
    bubbleAudioPlaying = true;

    // Start playing
    playNextBubbleChunk();
}

/**
 * Play the next chunk in the bubble audio sequence
 */
function playNextBubbleChunk() {
    if (bubbleAudioIndex >= bubbleAudioUrls.length || !bubbleAudioPlaying) {
        bubbleAudioPlaying = false;
        if (bubbleAudioActiveBtn) {
            bubbleAudioActiveBtn.classList.remove('bubble-audio-playing');
            bubbleAudioActiveBtn = null;
        }
        console.log('🔊 Bubble Audio: Playback complete');
        return;
    }

    const audioUrl = bubbleAudioUrls[bubbleAudioIndex];
    console.log(`🔊 Bubble Audio: Playing chunk ${bubbleAudioIndex + 1}/${bubbleAudioUrls.length}`);

    // Use visible HTML5 player if available, otherwise create Audio element
    const player = document.getElementById('tts-audio-player');
    const usePlayer = player && player.style.display !== 'none';
    const audio = usePlayer ? player : new Audio(audioUrl);
    bubbleAudioElement = audio;

    if (usePlayer) {
        audio.src = audioUrl;
    }
    audio.playbackRate = ttsPlaybackRate || 1.0;

    audio.onended = () => {
        bubbleAudioIndex++;
        setTimeout(playNextBubbleChunk, 150);
    };

    audio.play()
        .then(() => console.log(`✅ Bubble Audio: Chunk ${bubbleAudioIndex + 1} started`))
        .catch(err => {
            console.warn('⚠️ Bubble Audio: Autoplay blocked:', err.message);
            bubbleAudioPlaying = false;
        });
}

/**
 * Stop bubble audio playback
 */
function stopBubbleAudio() {
    bubbleAudioPlaying = false;
    bubbleAudioUrls = [];
    bubbleAudioIndex = 0;
    if (bubbleAudioActiveBtn) {
        bubbleAudioActiveBtn.classList.remove('bubble-audio-playing');
        bubbleAudioActiveBtn = null;
    }
    if (bubbleAudioElement) {
        bubbleAudioElement.pause();
        bubbleAudioElement.onended = null;
        bubbleAudioElement = null;
    }
    console.log('🔊 Bubble Audio: Stopped');
}

/**
 * Play audio from a URL (single URL - legacy compatibility)
 * @param {string} audioUrl - The URL of the audio file to play
 */
function playBubbleAudio(audioUrl) {
    if (!audioUrl) {
        console.warn('playBubbleAudio: No audio URL provided');
        return;
    }
    // Delegate to playBubbleAudioAll with single-item array
    playBubbleAudioAll([audioUrl]);
}

/**
 * Initialize bubble audio buttons - hide those without audio URLs
 * Called after DOM updates to manage button visibility
 */
function initBubbleAudioButtons() {
    const buttons = document.querySelectorAll('.bubble-audio-btn');
    // Idempotent: only write style if it actually changes. The MutationObserver
    // re-runs this on every DOM change, so unconditional writes here become
    // self-triggering loops and wipe text selection inside chat bubbles.
    const setDisplay = (button, value) => {
        if (button.style.display !== value) button.style.display = value;
    };
    buttons.forEach((button, idx) => {
        const audioUrlsJson = button.dataset.audioUrls;
        if (!audioUrlsJson) {
            setDisplay(button, 'none');
            return;
        }
        try {
            const audioUrls = JSON.parse(audioUrlsJson);
            if (!Array.isArray(audioUrls) || audioUrls.length === 0) {
                setDisplay(button, 'none');
            } else {
                setDisplay(button, 'inline-flex');
                // Attach click handler via JS (Reflex doesn't support native onclick strings)
                // Important: Read URLs fresh on click, not from closure (URLs may change after regeneration)
                if (!button.dataset.clickAttached) {
                    button.addEventListener('click', function() {
                        const btn = this;
                        // Toggle: if anything is playing, stop it
                        if (bubbleAudioPlaying) {
                            stopBubbleAudio();
                            return;
                        }
                        const freshUrlsJson = btn.dataset.audioUrls;
                        try {
                            const freshUrls = JSON.parse(freshUrlsJson);
                            console.log(`🔊 Button clicked, playing ${freshUrls.length} URLs (fresh read)`);
                            bubbleAudioActiveBtn = btn;
                            btn.classList.add('bubble-audio-playing');
                            playBubbleAudioAll(freshUrls);
                        } catch (e) {
                            console.warn('🔊 Button click: Failed to parse audio URLs', e);
                        }
                    });
                    button.dataset.clickAttached = 'true';
                }
            }
        } catch (e) {
            console.log(`🔊 Button[${idx}] → HIDE (parse error)`, e);
            button.style.display = 'none';
        }
    });
}

/**
 * Play bubble audio from click event (called from Reflex with event object)
 * @param {Event} event - The click event
 */
function playBubbleAudioFromEvent(event) {
    if (!event || !event.currentTarget) {
        console.warn('playBubbleAudioFromEvent: No event or currentTarget');
        return;
    }

    const button = event.currentTarget;
    const audioUrlsJson = button.dataset.audioUrls;

    if (!audioUrlsJson) {
        console.warn('playBubbleAudioFromEvent: No audio URLs in data attribute');
        return;
    }

    try {
        const audioUrls = JSON.parse(audioUrlsJson);

        if (!Array.isArray(audioUrls) || audioUrls.length === 0) {
            console.log('🔊 Bubble Audio: Empty audio URLs array');
            return;
        }

        // Play all audio URLs sequentially
        console.log(`🔊 Bubble Audio Event: Playing all ${audioUrls.length} URLs`);
        playBubbleAudioAll(audioUrls);
    } catch (e) {
        console.warn('playBubbleAudioFromEvent: Failed to parse JSON:', e, audioUrlsJson);
    }
}

/**
 * Initialize bubble regenerate buttons.
 *
 * Display-Sichtbarkeit kommt aus Reflex (style.display via enable_tts).
 * Hier passen wir nur den Tooltip an: ist fuer diese Bubble bereits
 * Audio vorhanden -> "Audio neu generieren" (regenerate),
 * ist noch keines da -> "Audio generieren" (initial generate).
 * Click-Handler bindet Reflex via on_click selbst.
 */
function initBubbleRegenerateButtons() {
    const buttons = document.querySelectorAll('.bubble-regenerate-btn');
    // Idempotent: only update title if it actually changes. Re-runs every time
    // the MutationObserver fires, and title writes count as DOM mutations
    // that wipe text selection inside chat bubbles.
    buttons.forEach((button, idx) => {
        const audioUrlsJson = button.dataset.audioUrls;
        let hasAudio = false;
        if (audioUrlsJson) {
            try {
                const audioUrls = JSON.parse(audioUrlsJson);
                hasAudio = Array.isArray(audioUrls) && audioUrls.length > 0;
            } catch (e) {
                console.log(`🔄 Button[${idx}] tooltip parse error`, e);
            }
        }
        const tipRegenerate = button.dataset.tooltipRegenerate;
        const tipGenerate = button.dataset.tooltipGenerate;
        const newTitle = hasAudio ? (tipRegenerate || button.title) : (tipGenerate || button.title);
        if (button.title !== newTitle) {
            button.title = newTitle;
        }
    });
}

window.playBubbleAudioFromButton = playBubbleAudioFromButton;
window.playBubbleAudio = playBubbleAudio;
window.playBubbleAudioAll = playBubbleAudioAll;
window.stopBubbleAudio = stopBubbleAudio;
window.playBubbleAudioFromEvent = playBubbleAudioFromEvent;
window.initBubbleAudioButtons = initBubbleAudioButtons;
window.initBubbleRegenerateButtons = initBubbleRegenerateButtons;

// ============================================================
// TTS AUDIO QUEUE - Double-buffered gapless playback with pitch preservation
// ============================================================

// Queue state
var ttsQueue = [];  // Array of audio URLs to play
var ttsQueuePlaying = false;  // Is queue currently playing?
var ttsQueueCurrentIndex = 0;  // Current playback position
var ttsQueueVersion = 0;  // Track version to detect updates from backend

// Blob prefetch: download upcoming chunks into memory for instant src switching
var ttsBlobCache = {};  // originalURL → blobURL mapping
var ttsPrefetchInFlight = new Set();  // URLs currently being fetched

/**
 * Update the TTS queue from backend state.
 * Called when SSE pushes a new audio URL or when tts_audio_queue changes.
 */
function updateTtsQueue(queue, version) {
    if (version <= ttsQueueVersion && queue.length === ttsQueue.length) {
        return;
    }

    console.log(`🔊 TTS Queue: Update v${ttsQueueVersion}→${version}, items ${ttsQueue.length}→${queue.length}`);

    // Detect queue reset (new inference or chat clear)
    const versionReset = version < ttsQueueVersion && ttsQueueVersion > 0;
    const queueShrunk = queue.length < ttsQueue.length || queue.length === 0;

    if (queueShrunk || versionReset) {
        console.log(`🔊 TTS Queue: Reset detected, stopping playback`);
        stopPlayback();
        ttsQueue = [];
    }

    ttsQueueVersion = version;
    const prevLength = ttsQueue.length;
    ttsQueue = [...queue];

    // Auto-start playback if new items arrived and not already playing
    const queueElement = document.getElementById('tts-queue-data');
    const autoplayEnabled = queueElement?.dataset?.autoplay === 'true';

    if (queue.length > prevLength && !ttsQueuePlaying && autoplayEnabled && !document.hidden) {
        console.log(`🔊 TTS Queue: New items, starting playback`);
        playNextChunk();
    } else if (queue.length > prevLength && ttsQueuePlaying) {
        // Already playing - prefetch upcoming chunks
        prefetchChunks();
    }
}

/**
 * Play the current chunk through the visible <audio> element.
 * Uses in-memory blob URL if available (instant load, no gap).
 */
function playNextChunk() {
    if (ttsQueueCurrentIndex >= ttsQueue.length) {
        ttsQueuePlaying = false;
        console.log('🔊 TTS Queue: Playback complete');
        // Clean up blob URLs
        for (const blobUrl of Object.values(ttsBlobCache)) {
            URL.revokeObjectURL(blobUrl);
        }
        ttsBlobCache = {};

        // If media was queued behind TTS, kick it off now.
        // Resume only when media is *currently* paused-for-tts — the
        // client-side audioCurrentMediaKey is set when a media URL is
        // active and cleared on media-end. Without that check, every
        // bubble-audio replay or TTS-regenerate after a finished
        // hörbuch would falsely re-start the media from 0 (the
        // server-side data-paused-for-tts flag stays sticky).
        const player = document.getElementById('tts-audio-player');
        if (player && audioCurrentMediaKey) {
            const mediaUrl = player.dataset.mediaUrl || '';
            const pausedForTts = player.dataset.mediaPausedForTts === 'true';
            if (mediaUrl && pausedForTts) {
                console.log('🔊 Audio: TTS done — resuming media');
                audioLoadAndPlayMedia(mediaUrl);
            }
        }
        return;
    }

    const player = document.getElementById('tts-audio-player');
    if (!player) {
        console.warn('🔊 TTS Queue: No audio player element found');
        ttsQueuePlaying = false;
        return;
    }

    const audioUrl = ttsQueue[ttsQueueCurrentIndex];
    const chunkIndex = ttsQueueCurrentIndex;

    // Use blob URL (in-memory, instant) if prefetched, otherwise original URL
    player.src = ttsBlobCache[audioUrl] || audioUrl;

    // Apply playback rate with pitch preservation
    if (player.dataset?.playbackRate) {
        const rate = parseFloat(player.dataset.playbackRate.replace('x', ''));
        if (!isNaN(rate) && rate > 0) {
            ttsPlaybackRate = rate;
        }
    }
    player.playbackRate = ttsPlaybackRate;
    player.preservesPitch = true;

    ttsQueuePlaying = true;

    // When this chunk ends, advance to the next
    player.onended = () => {
        // Revoke old blob URL to free memory
        if (ttsBlobCache[audioUrl]) {
            URL.revokeObjectURL(ttsBlobCache[audioUrl]);
            delete ttsBlobCache[audioUrl];
        }
        console.log(`🔊 TTS Queue: Chunk ${chunkIndex + 1} finished`);
        ttsQueueCurrentIndex++;
        playNextChunk();
    };

    player.play()
        .then(() => {
            player.playbackRate = ttsPlaybackRate;
            console.log(`🔊 TTS Queue: Playing chunk ${chunkIndex + 1}/${ttsQueue.length} at ${ttsPlaybackRate}x`);
        })
        .catch(err => {
            if (err.message && err.message.includes('interrupted')) {
                console.warn('⚠️ TTS Queue: Play interrupted, retrying');
                setTimeout(() => playNextChunk(), 100);
            } else {
                console.warn('⚠️ TTS Queue: Autoplay blocked:', err.message);
                ttsQueuePlaying = false;
            }
        });

    // Prefetch upcoming chunks into memory
    prefetchChunks();
}

/**
 * Prefetch upcoming chunks as blob URLs (in-memory, instant load).
 * Downloads next 2 chunks and creates blob URLs for gap-free src switching.
 */
function prefetchChunks() {
    for (let i = 1; i <= 2; i++) {
        const idx = ttsQueueCurrentIndex + i;
        if (idx >= ttsQueue.length) continue;
        const url = ttsQueue[idx];
        if (ttsBlobCache[url] || ttsPrefetchInFlight.has(url)) continue;

        ttsPrefetchInFlight.add(url);
        fetch(url)
            .then(r => r.blob())
            .then(blob => {
                ttsBlobCache[url] = URL.createObjectURL(blob);
                ttsPrefetchInFlight.delete(url);
                console.log(`🔊 TTS Queue: Prefetched chunk ${idx + 1} into memory`);
            })
            .catch(() => {
                ttsPrefetchInFlight.delete(url);
            });
    }
}

/**
 * Stop playback and reset state.
 */
function stopPlayback() {
    const player = document.getElementById('tts-audio-player');
    if (player) {
        player.pause();
        player.onended = null;
    }
    // Clean up blob URLs
    for (const blobUrl of Object.values(ttsBlobCache)) {
        URL.revokeObjectURL(blobUrl);
    }
    ttsBlobCache = {};
    ttsPrefetchInFlight.clear();
    ttsQueueCurrentIndex = 0;
    ttsQueuePlaying = false;
}

/**
 * Clear the TTS queue and stop playback.
 */
function clearTtsQueue() {
    console.log('🔊 TTS Queue: Clearing');
    stopPlayback();
    ttsQueue = [];
    ttsQueueVersion = 0;
}

/**
 * Skip current chunk and play the next one.
 */
function skipTtsQueueItem() {
    console.log('🔊 TTS Queue: Skipping current chunk');
    const player = document.getElementById('tts-audio-player');
    if (player) {
        player.pause();
        player.onended = null;
    }
    ttsQueueCurrentIndex++;
    playNextChunk();
}

// Make queue functions available globally
window.updateTtsQueue = updateTtsQueue;
window.clearTtsQueue = clearTtsQueue;
window.skipTtsQueueItem = skipTtsQueueItem;

// ============================================================
// BROWSER PUSH BUS - Server-Sent Events, reflex-independent
// ============================================================

// Browser Push Bus — single SSE stream for everything the server pushes
// to the browser WITHOUT a Reflex state delta. Audio kinds (tts/media/…)
// share it with non-audio kinds (session_title, debug). Sharing one
// EventSource means the user-gesture chain (Send-button click →
// startBrowserStream) is the origin for ALL browser audio.play() calls —
// no autoplay blocks for audio_player tool calls. Server-side: see
// browser_push() in aifred/lib/api.py.
var browserStreamActive = false;
var browserEventSource = null;
var browserStreamSessionId = null;
var browserStreamRetryCount = 0;
var browserStreamGaveUp = false;
var BROWSER_STREAM_MAX_RETRIES = 3;

/**
 * Get session ID from cookie
 */
function getSessionIdFromCookie() {
    const name = 'aifred_session_id=';
    const decodedCookie = decodeURIComponent(document.cookie);
    const cookies = decodedCookie.split(';');
    for (let cookie of cookies) {
        cookie = cookie.trim();
        if (cookie.indexOf(name) === 0) {
            return cookie.substring(name.length);
        }
    }
    return null;
}

/**
 * Start the unified Browser Push Bus SSE stream.
 *
 * Called from the user-gesture stack (login, send-button, session switch)
 * — the EventSource opens IN that gesture chain, so every audio.play()
 * triggered by a server-pushed event later is treated as user-initiated
 * by the browser. This is what makes audio_player tool-calls auto-play
 * just like TTS.
 *
 * Server pushes events of many kinds (tts, media, bubble_audio,
 * session_title, debug, …); the onmessage handler routes by kind.
 *
 * @param {string} sessionIdParam - Optional session ID (else read from cookie)
 */
function startBrowserStream(sessionIdParam) {
    const sessionId = sessionIdParam || getSessionIdFromCookie();
    if (!sessionId) {
        console.warn('📡 Browser SSE: No session ID found');
        return;
    }

    if (browserStreamGaveUp && browserStreamSessionId === sessionId) {
        return;
    }

    if (browserStreamActive && browserStreamSessionId === sessionId && browserEventSource) {
        if (browserEventSource.readyState === EventSource.OPEN || browserEventSource.readyState === EventSource.CONNECTING) {
            console.log('🔊 Audio SSE: Already connected for this session');
            return;
        } else {
            console.log('🔊 Audio SSE: Previous connection closed, reconnecting...');
        }
    }

    if (browserEventSource) {
        browserEventSource.close();
        browserEventSource = null;
    }

    if (browserStreamSessionId !== sessionId) {
        browserStreamRetryCount = 0;
        browserStreamGaveUp = false;
    }
    browserStreamSessionId = sessionId;
    browserStreamActive = true;
    console.log(`🔊 Audio SSE: Connecting for session ${sessionId.substring(0, 8)}...`);

    const sseUrl = `/api/browser/stream/${sessionId}`;
    console.log(`🔊 Audio SSE: URL = ${sseUrl}`);

    browserEventSource = new EventSource(sseUrl);

    browserEventSource.onopen = () => {
        console.log('🔊 Audio SSE: Connection opened');
        browserStreamRetryCount = 0;
        browserStreamGaveUp = false;
    };

    browserEventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            const kind = data.kind || 'tts';  // legacy items default to TTS
            const url = data.url || data.audio_url;  // accept either field
            console.log(`🔊 Audio SSE: Received ${kind} v${data.version}`);

            // url is required for tts/media; control events (stop, pause,
            // resume, seek, speed) carry no url by design.
            const isControl = (
                kind === 'stop' || kind === 'pause' || kind === 'resume'
                || kind === 'seek' || kind === 'speed'
            );
            if (!url && !isControl) return;

            if (data.playback_rate) {
                const rate = parseFloat(String(data.playback_rate).replace('x', ''));
                if (!isNaN(rate) && rate > 0) {
                    ttsPlaybackRate = rate;
                }
            }

            // Dedupe by version on reconnect (server may resend items with
            // version <= ttsQueueVersion). Same dedup applies for media —
            // we share the version counter across kinds.
            if (data.version <= ttsQueueVersion && ttsQueueVersion > 0) {
                console.log(`🔊 Audio SSE: Skipping already-known v${data.version} (current v${ttsQueueVersion})`);
                return;
            }

            if (kind === 'bubble_audio') {
                // Streaming-TTS finalize announces the combined replay URL.
                // The server-side create_task already patched chat_history,
                // but Reflex never pushes that delta — so attach the URL to
                // the most recent assistant bubble still without audio. The
                // MutationObserver on data-audio-urls then activates the
                // speaker button. No Reflex state round-trip needed.
                ttsQueueVersion = data.version;
                const audioBtns = document.querySelectorAll('.bubble-audio-btn');
                let pendingBtn = null;
                for (let i = audioBtns.length - 1; i >= 0; i--) {
                    const have = audioBtns[i].getAttribute('data-audio-urls');
                    if (!have || have === '' || have === '[]') {
                        pendingBtn = audioBtns[i];
                        break;
                    }
                }
                if (pendingBtn) {
                    pendingBtn.setAttribute('data-audio-urls', JSON.stringify([url]));
                    console.log('🔊 Audio SSE: bubble_audio attached to latest bubble');
                } else {
                    console.warn('🔊 Audio SSE: bubble_audio — no pending bubble found');
                }
                return;
            }

            if (kind === 'session_title') {
                // Background title generation finished. The server-side
                // create_task wrote the title to disk + state, but Reflex
                // pushes no delta — so patch the session-list entry directly.
                // The SSE connection is per-session, so the title always
                // belongs to browserStreamSessionId. Reflex' next
                // refresh_session_list re-render is consistent (reads disk).
                ttsQueueVersion = data.version;
                const titleEl = document.querySelector(
                    `.session-title-text[data-session-id="${browserStreamSessionId}"]`
                );
                if (titleEl) {
                    titleEl.textContent = url;  // url carries the title text
                    console.log('📡 Browser SSE: session_title patched into session list');
                } else {
                    console.warn('📡 Browser SSE: session_title — no session-list entry found');
                }
                return;
            }

            if (kind === 'media') {
                // Single-track replace via the existing media-load path.
                // Same audio element as TTS — user gesture from SSE-open
                // applies, so player.play() is allowed.
                ttsQueueVersion = data.version;
                audioCurrentMediaKey = data.state_key || '';
                if (data.start_pos_sec && data.start_pos_sec > 0) {
                    // audioLoadAndPlayMedia consumes audioTtsPauseSnapshotSec
                    // for resume offset — set BEFORE load so the
                    // loadedmetadata handler picks it up.
                    audioTtsPauseSnapshotSec = Number(data.start_pos_sec);
                }
                // Race-defense: if the audio element isn't in the DOM yet
                // (Reflex renders it after media_audio_url state-update),
                // remember the URL and replay from audioBindObservers.
                if (!audioPlayerEl()) {
                    _audioPendingMediaUrl = url;
                    console.log('🔊 Audio Bus: media event arrived before element — deferred');
                } else {
                    audioLoadAndPlayMedia(url);
                }
            } else if (kind === 'stop') {
                // Server-triggered stop (audio_stop tool, _stop wake-word).
                // Final state: paused, position-final-saved, queue cleared.
                // We do NOT removeAttribute('src') anymore — browsers can
                // get stuck in a "no media" state after that, where the
                // next src= assignment doesn't fire loadedmetadata reliably.
                // pause() alone halts playback + buffering; the next
                // kind="media" event replaces src naturally.
                ttsQueueVersion = data.version;
                const player = audioPlayerEl();
                if (player) {
                    if (audioCurrentMediaKey) {
                        audioSaveCurrentPosition();
                    }
                    player.pause();
                }
                audioCurrentMediaKey = '';
                audioCurrentMediaUrl = '';
                audioMediaQueue = [];
                audioMediaQueueIdx = 0;
                audioStopPositionSaver();
                console.log('🔊 Audio Bus: media stopped');
            } else if (kind === 'pause') {
                // Server-triggered pause (audio_pause tool, _pause wake-word).
                // Player halts, src + position stay intact for resume.
                ttsQueueVersion = data.version;
                const player = audioPlayerEl();
                if (player && !player.paused) {
                    audioSaveCurrentPosition();
                    player.pause();
                }
                console.log('🔊 Audio Bus: media paused');
            } else if (kind === 'resume') {
                // Server-triggered resume (audio_resume tool, _resume wake-
                // word). Same audio element + same src — player.play() runs
                // inside the SSE-onmessage handler, which inherits the
                // user-gesture chain from startBrowserStream → no autoplay block.
                ttsQueueVersion = data.version;
                const player = audioPlayerEl();
                if (player && player.paused && player.src) {
                    player.play().then(() => {
                        audioStartPositionSaver();
                        console.log('🔊 Audio Bus: media resumed');
                    }).catch((err) => {
                        console.warn('🔊 Audio Bus: resume blocked:', err.message);
                    });
                }
            } else if (kind === 'seek') {
                // Server-triggered seek (audio_seek / audio_skip tool).
                // ``relative=true`` → ±N seconds from current position.
                ttsQueueVersion = data.version;
                const player = audioPlayerEl();
                if (player && player.duration) {
                    const target = data.relative
                        ? player.currentTime + Number(data.position_sec || 0)
                        : Number(data.position_sec || 0);
                    const clamped = Math.max(0, Math.min(player.duration, target));
                    player.currentTime = clamped;
                    audioSaveCurrentPosition();
                    console.log(`🔊 Audio Bus: seek → ${clamped.toFixed(1)}s`);
                }
            } else if (kind === 'speed') {
                // Server-triggered speed change (audio_speed tool).
                // HTML5 audio.playbackRate accepts 0.25–4.0 in spec; clamp
                // here as a safety net.
                ttsQueueVersion = data.version;
                const factor = Math.max(0.25, Math.min(4.0, Number(data.factor || 1.0)));
                const player = audioPlayerEl();
                if (player) {
                    player.playbackRate = factor;
                    console.log(`🔊 Audio Bus: speed → ${factor}×`);
                }
            } else {
                // TTS — gapless queue append for streaming inference output.
                const newQueue = [...ttsQueue, url];
                updateTtsQueue(newQueue, data.version);
            }
        } catch (e) {
            console.warn('🔊 Audio SSE: Failed to parse event data:', e);
        }
    };

    browserEventSource.onerror = (event) => {
        if (browserEventSource.readyState === EventSource.CLOSED) {
            browserStreamActive = false;
            browserEventSource = null;
            browserStreamRetryCount++;

            if (browserStreamRetryCount > BROWSER_STREAM_MAX_RETRIES) {
                console.warn(`🔊 Audio SSE: Giving up after ${BROWSER_STREAM_MAX_RETRIES} retries (endpoint unavailable)`);
                browserStreamGaveUp = true;
                return;
            }

            const delay = browserStreamRetryCount * 2000;
            console.log(`🔊 Audio SSE: Connection closed, retry ${browserStreamRetryCount}/${BROWSER_STREAM_MAX_RETRIES} in ${delay}ms...`);
            setTimeout(() => {
                if (!browserStreamActive && browserStreamSessionId) {
                    startBrowserStream(browserStreamSessionId);
                }
            }, delay);
        } else {
            console.warn('🔊 Audio SSE: Connection error, EventSource will auto-retry...');
        }
    };
}

/**
 * Stop the Audio Bus SSE stream.
 * Called when generation completes (is_generating becomes false).
 */
function stopAudioStream() {
    if (browserEventSource) {
        browserEventSource.close();
        browserEventSource = null;
    }
    browserStreamActive = false;
    console.log('🔊 Audio SSE: Stopped');
}

/**
 * Audio-Element Auto-Play-Unlock.
 *
 * Browsers gate ``audio.play()`` behind a "user has interacted with the
 * element" flag — set permanently for the tab once any play() succeeds
 * inside a user-gesture stack. This helper plays a 0.1 s silent data-URI
 * to flip that flag. Call it from EVERY user-click handler that may
 * eventually trigger autoplayed audio (Send-button, Login, Session-
 * switch). Idempotent and very cheap (no network).
 *
 * Why this matters: audio_player tool-calls fire long AFTER inference
 * (often 20+ seconds), well outside any browser autoplay-grace-window.
 * Without prior unlock, the SSE-pushed media event would land in a
 * blocked-autoplay state and the player stays at 0:00.
 *
 * NOTE: just calling play() on an empty src does NOT unlock — the
 * browser sees "no media" and the gesture-flag stays unset. We need an
 * actual playable source. The data-URI below is a 1-frame silent WAV
 * (44 Hz, 8-bit mono).
 */
var _audioUnlockDone = false;
var _AUDIO_UNLOCK_SILENT_WAV = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';

function audioUnlock() {
    if (_audioUnlockDone) return;  // once per tab is enough
    const player = audioPlayerEl();
    if (!player) return;

    const savedSrc = player.src;
    const savedVolume = player.volume;

    player.volume = 0;
    player.src = _AUDIO_UNLOCK_SILENT_WAV;

    const promise = player.play();
    if (promise && typeof promise.then === 'function') {
        promise
            .then(() => {
                player.pause();
                _audioUnlockDone = true;
                console.log('🔊 Audio: unlocked for autoplay');
            })
            .catch((err) => {
                console.warn('🔊 Audio: unlock failed:', err.message);
            })
            .finally(() => {
                // Restore previous src (or empty if none was set).
                if (savedSrc) {
                    player.src = savedSrc;
                } else {
                    player.removeAttribute('src');
                    player.load();
                }
                player.volume = savedVolume;
            });
    } else {
        // Older browsers without play()-Promise — best-effort restore.
        if (savedSrc) player.src = savedSrc;
        else player.removeAttribute('src');
        player.volume = savedVolume;
    }
}

// Make SSE functions available globally
window.startBrowserStream = startBrowserStream;
window.stopAudioStream = stopAudioStream;
window.audioUnlock = audioUnlock;

// One-shot tab-wide unlock on FIRST user gesture. Reflex's rx.call_script
// arrives over WebSocket — even when triggered by a click handler, the
// async boundary means the browser doesn't see it as a user gesture.
// A direct DOM listener captures the gesture in its actual stack.
//
// We listen on multiple gesture types because users send messages by
// pressing Enter just as often as by clicking — keydown alone wouldn't
// catch click-to-focus, click alone wouldn't catch Enter-after-typing.
// Capture phase ensures we run BEFORE any other handler can call
// preventDefault/stopPropagation. Listeners self-remove once unlocked.
var _AUDIO_UNLOCK_GESTURES = ['click', 'pointerdown', 'keydown', 'touchstart'];

function _unlockAudioFromUserGesture(ev) {
    audioUnlock();
    // _audioUnlockDone is set asynchronously inside audioUnlock's
    // play().then() — so on the SAME tick it's still false. We schedule
    // the cleanup for after the unlock attempt resolves.
    setTimeout(() => {
        if (_audioUnlockDone) {
            _AUDIO_UNLOCK_GESTURES.forEach(t =>
                document.removeEventListener(t, _unlockAudioFromUserGesture, true)
            );
            console.log('🔊 Audio: unlock-listeners removed');
        }
    }, 200);
}

_AUDIO_UNLOCK_GESTURES.forEach(t =>
    document.addEventListener(t, _unlockAudioFromUserGesture, true)
);

// ============================================================
// TTS AUDIO OBSERVER - Watch for NEW audio elements (React re-mounts)
// ============================================================

var lastObservedTtsSrc = '';
var ttsDocumentObserver = null;

/**
 * Setup a document-level observer that watches for newly added audio elements.
 * This is necessary because React re-mounts the audio element when the key changes,
 * destroying the old element and creating a new one.
 */
function setupTtsAudioObserver() {
    console.log('🔊 TTS Observer: Setting up document-level observer');

    // Disconnect existing observer if any
    if (ttsDocumentObserver) {
        ttsDocumentObserver.disconnect();
    }

    // Function to handle a found audio player
    // When a NEW audio element is detected (React re-mounts with new key), we:
    // 1. Apply the playback rate from data attribute (agent-specific speed)
    // 2. Trigger play() on the VISIBLE player after a short delay
    // The HTML5 player has autoPlay=True, but browsers may block it. This is a backup.
    // NOTE: If queue is playing, the queue controls playback - observer should not interfere
    const handleAudioPlayer = (player) => {
        if (!player || !player.src) return;

        const src = player.src;
        console.log('🔊 TTS Observer: Detected audio player, src =', src);

        // Only process TTS audio URLs
        if (!src.includes('/tts_audio/')) return;

        // If queue is actively playing, don't interfere - queue controls playback
        if (ttsQueuePlaying) {
            console.log('🔊 TTS Observer: Queue is playing, skipping observer play trigger');
            return;
        }

        // Read playback rate from data attribute (set by backend per agent)
        const dataRate = player.dataset.playbackRate;
        if (dataRate) {
            const rate = parseFloat(dataRate.replace('x', ''));
            if (!isNaN(rate) && rate > 0) {
                ttsPlaybackRate = rate;
                console.log('🔊 TTS Observer: Agent speed from data-playback-rate =', rate);
            }
        }

        // Apply playback rate immediately
        player.playbackRate = ttsPlaybackRate;
        console.log('🔊 TTS Observer: Applied playback rate', ttsPlaybackRate);

        // Check if this is a NEW audio (different from last observed)
        if (src === lastObservedTtsSrc) {
            console.log('🔊 TTS Observer: Same audio URL, skipping play trigger');
            return;
        }

        // NEW audio detected - this is a fresh player (React re-mounted it)
        lastObservedTtsSrc = src;
        console.log('🔊 TTS Observer: NEW audio detected');

        // Small delay to ensure audio is fully loaded, then ensure it plays
        // The autoPlay attribute should work, but this is a backup in case browser blocks it
        setTimeout(() => {
            // Double-check queue isn't playing (might have started during delay)
            if (ttsQueuePlaying) {
                console.log('🔊 TTS Observer: Queue started playing during delay, skipping');
                return;
            }
            if (player.paused && player.readyState >= 2) {
                console.log('🔊 TTS Observer: AutoPlay may have been blocked, triggering play()');
                player.play()
                    .then(() => console.log('✅ TTS Observer: Playback started via backup'))
                    .catch(err => console.warn('⚠️ TTS Observer: Play blocked:', err.message));
            } else if (!player.paused) {
                console.log('🔊 TTS Observer: Already playing (autoPlay worked)');
            }
        }, 200);
    };

    // Function to handle TTS queue data updates
    const handleQueueDataUpdate = (element) => {
        if (!element) return;

        const queueJson = element.dataset.queue;
        const version = parseInt(element.dataset.version || '0', 10);

        if (!queueJson) return;

        try {
            const queue = JSON.parse(queueJson);
            if (Array.isArray(queue)) {
                updateTtsQueue(queue, version);
            }
        } catch (e) {
            console.warn('⚠️ TTS Queue: Failed to parse queue JSON', e);
        }
    };

    // Create document-level observer to watch for added nodes
    ttsDocumentObserver = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            // Check added nodes for audio elements
            if (mutation.type === 'childList') {
                mutation.addedNodes.forEach(node => {
                    // Check if the added node IS an audio element
                    if (node.nodeName === 'AUDIO' && node.id === 'tts-audio-player') {
                        console.log('🔊 TTS Observer: Audio element ADDED to DOM');
                        handleAudioPlayer(node);
                    }
                    // Check if the added node CONTAINS an audio element
                    if (node.querySelector) {
                        const audio = node.querySelector('#tts-audio-player');
                        if (audio) {
                            console.log('🔊 TTS Observer: Audio element found in added subtree');
                            handleAudioPlayer(audio);
                        }
                        // Check for queue data element
                        const queueData = node.querySelector('#tts-queue-data');
                        if (queueData) {
                            handleQueueDataUpdate(queueData);
                        }
                    }
                    // Check if the added node IS the queue data element
                    if (node.id === 'tts-queue-data') {
                        handleQueueDataUpdate(node);
                    }
                });
            }
            // Watch for attribute changes
            if (mutation.type === 'attributes') {
                const target = mutation.target;
                // Audio src changes
                if (mutation.attributeName === 'src' && target.nodeName === 'AUDIO' && target.id === 'tts-audio-player') {
                    console.log('🔊 TTS Observer: Audio src attribute changed');
                    handleAudioPlayer(target);
                }
                // Playback rate changes (from dropdown) - apply IMMEDIATELY to playing audio
                if (mutation.attributeName === 'data-playback-rate' && target.nodeName === 'AUDIO' && target.id === 'tts-audio-player') {
                    const newRate = target.dataset.playbackRate;
                    if (newRate) {
                        const rate = parseFloat(newRate.replace('x', ''));
                        if (!isNaN(rate) && rate > 0) {
                            ttsPlaybackRate = rate;
                            target.playbackRate = rate;
                            console.log(`🔊 TTS Observer: Playback rate changed to ${rate}x (applied immediately)`);
                        }
                    }
                }
                // Queue data changes (data-queue or data-version)
                if ((mutation.attributeName === 'data-queue' || mutation.attributeName === 'data-version') && target.id === 'tts-queue-data') {
                    console.log('🔊 TTS Observer: Queue data attribute changed');
                    handleQueueDataUpdate(target);
                }
                // NOTE: Audio Bus SSE stream is started once on login via
                // rx.call_script("startBrowserStream('...')") and stays open
                // for the entire session. The stream handles idle periods
                // automatically (SSE keeps connection open).
            }
        }
    });

    // Observe the entire document for changes
    ttsDocumentObserver.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['src', 'data-queue', 'data-version', 'data-playback-rate', 'data-polling']
    });

    // Also check if audio element already exists
    const existingPlayer = document.getElementById('tts-audio-player');
    if (existingPlayer) {
        console.log('🔊 TTS Observer: Found existing audio player');
        handleAudioPlayer(existingPlayer);
    }

    // Check if queue data element already exists
    const existingQueueData = document.getElementById('tts-queue-data');
    if (existingQueueData) {
        console.log('🔊 TTS Observer: Found existing queue data element');
        handleQueueDataUpdate(existingQueueData);

        // Also check if SSE should be started (data-polling might already be true)
        const shouldStream = existingQueueData.dataset.polling === 'true';
        console.log(`🔊 TTS Observer: Initial polling state = ${shouldStream}`);
        if (shouldStream && !browserStreamActive) {
            console.log('🔊 Audio Bus: Starting SSE stream on init');
            startBrowserStream();
        }
    }

    console.log('🔊 TTS Observer: Document observer active');
}

// ============================================================
// INITIALIZATION
// ============================================================

function initializeAllObservers() {
    console.log('🚀 Initializing TTS observer...');

    // Setup TTS audio observer
    setupTtsAudioObserver();

    // Initialize bubble audio and regenerate buttons (hide those without audio)
    initBubbleAudioButtons();
    initBubbleRegenerateButtons();

    // AUTO-START Audio Bus SSE stream on page load if session exists.
    // Stream is open BEFORE any inference triggers TTS or audio_play —
    // and runs in the user-gesture chain via Reflex on_load events, so
    // every server-pushed audio item plays without autoplay block.
    const sessionId = getSessionIdFromCookie();
    if (sessionId && !browserStreamActive) {
        console.log('🔊 Audio Bus: Auto-starting stream on page load');
        startBrowserStream(sessionId);
    } else if (!sessionId) {
        // Cookie not yet set (Reflex hasn't initialized session yet) —
        // poll quickly until we have a session, then start SSE.
        console.log('🔊 Audio Bus: No session cookie yet, starting fast poll...');
        let fastPollCount = 0;
        const fastPollInterval = setInterval(() => {
            fastPollCount++;
            const newSessionId = getSessionIdFromCookie();
            if (newSessionId && !browserStreamActive) {
                console.log(`🔊 Audio Bus: Session cookie found after ${fastPollCount * 100}ms, starting stream`);
                startBrowserStream(newSessionId);
                clearInterval(fastPollInterval);
            } else if (fastPollCount >= 50) {
                console.log('🔊 Audio Bus: Fast poll timeout, falling back to slow poll');
                clearInterval(fastPollInterval);
            }
        }, 100);
    }

    // Periodic reconnect check (tab sleep, network interruptions).
    setInterval(() => {
        const currentSessionId = getSessionIdFromCookie();
        if (currentSessionId && !browserStreamActive) {
            console.log('🔊 Audio Bus: Reconnecting (periodic check)');
            startBrowserStream(currentSessionId);
        }
    }, 5000);

    // Retry after 500ms in case elements render later
    setTimeout(() => {
        setupTtsAudioObserver();
        initBubbleAudioButtons();
        initBubbleRegenerateButtons();
    }, 500);

    // Setup a MutationObserver to initialize new bubble audio/regenerate buttons as they're added
    // AND to detect when data-audio-urls attribute changes on existing buttons
    const chatObserver = new MutationObserver((mutations) => {
        let needsInit = false;
        for (const mutation of mutations) {
            // Check for new buttons added to DOM
            if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                for (const node of mutation.addedNodes) {
                    if (node.querySelector && (node.querySelector('.bubble-audio-btn') || node.querySelector('.bubble-regenerate-btn'))) {
                        needsInit = true;
                        break;
                    }
                    if (node.classList && (node.classList.contains('bubble-audio-btn') || node.classList.contains('bubble-regenerate-btn'))) {
                        needsInit = true;
                        break;
                    }
                }
            }
            // Check for data-audio-urls attribute changes on existing buttons
            if (mutation.type === 'attributes' && mutation.attributeName === 'data-audio-urls') {
                if (mutation.target.classList && mutation.target.classList.contains('bubble-audio-btn')) {
                    console.log('🔊 Bubble Audio: data-audio-urls attribute changed');
                    needsInit = true;
                }
            }
            if (needsInit) break;
        }
        if (needsInit) {
            // Debounce initialization
            setTimeout(() => {
                initBubbleAudioButtons();
                initBubbleRegenerateButtons();
            }, 50);
        }
    });

    // Observe the document body for new chat bubbles AND attribute changes
    chatObserver.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['data-audio-urls']
    });
}

// Handle both cases: DOMContentLoaded not yet fired, or already fired
if (document.readyState === 'loading') {
    // DOM is still loading, wait for DOMContentLoaded
    document.addEventListener('DOMContentLoaded', function() {
        console.log('📄 DOMContentLoaded event fired');
        initializeAllObservers();
    });
} else {
    // DOM already loaded (script loaded after DOMContentLoaded)
    console.log('📄 DOM already ready, initializing immediately');
    initializeAllObservers();
}

// ============================================================
// KaTeX LaTeX Rendering
// ============================================================
// Renders LaTeX formulas in chat messages using KaTeX
// Supports: $...$ (inline), $$...$$ (block), and \ce{} (chemistry via mhchem)

var katexLoaded = false;
var mhchemLoaded = false;

function loadKatexScript() {
    if (katexLoaded || window.katex) {
        katexLoaded = true;
        // Also load mhchem if KaTeX already loaded
        return loadMhchemExtension();
    }

    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = '/katex/katex.min.js';
        script.onload = () => {
            console.log('📐 KaTeX script loaded');
            katexLoaded = true;
            // Load mhchem extension after KaTeX
            loadMhchemExtension().then(resolve).catch(resolve); // Don't fail if mhchem fails
        };
        script.onerror = () => {
            console.error('❌ Failed to load KaTeX');
            reject(new Error('KaTeX load failed'));
        };
        document.head.appendChild(script);
    });
}

function loadMhchemExtension() {
    if (mhchemLoaded) {
        return Promise.resolve();
    }

    return new Promise((resolve) => {
        const script = document.createElement('script');
        script.src = '/katex/mhchem.min.js';
        script.onload = () => {
            console.log('🧪 KaTeX mhchem extension loaded (chemistry support)');
            mhchemLoaded = true;
            resolve();
        };
        script.onerror = () => {
            console.warn('⚠️ mhchem extension not loaded (chemistry formulas disabled)');
            resolve(); // Don't fail, just continue without chemistry
        };
        document.head.appendChild(script);
    });
}

function renderLatexInElement(element) {
    if (!window.katex) return;

    const walker = document.createTreeWalker(
        element,
        NodeFilter.SHOW_TEXT,
        null,
        false
    );

    const textNodes = [];
    let node;
    while (node = walker.nextNode()) {
        // Skip if already processed or inside code/pre blocks
        if (node.parentElement.closest('code, pre, .katex')) continue;
        // Check for $ delimiter (server converts \[...\] and \(...\) to $...$)
        if (node.textContent.includes('$')) {
            textNodes.push(node);
        }
    }

    textNodes.forEach(textNode => {
        const text = textNode.textContent;
        let combined = text;
        let hasMatch = false;

        // 1. Block math: $$...$$ (server converts \[...\] to this format)
        combined = combined.replace(/\$\$([^$]+)\$\$/g, (match, formula) => {
            hasMatch = true;
            try {
                return '<span class="katex-block">' +
                    window.katex.renderToString(formula.trim(), {
                        displayMode: true,
                        throwOnError: false
                    }) + '</span>';
            } catch (e) {
                console.warn('KaTeX block $$ error:', e);
                return match;
            }
        });

        // 2. Inline math: $...$ (server converts \(...\) to this format)
        // Note: \[...\], \(...\), and \text{} are handled server-side in formatting.py
        combined = combined.replace(/(?<!\$)\$([^$\n]+)\$(?!\$)/g, (match, formula) => {
            hasMatch = true;
            try {
                return window.katex.renderToString(formula.trim(), {
                    displayMode: false,
                    throwOnError: false
                });
            } catch (e) {
                console.warn('KaTeX inline $ error:', e);
                return match;
            }
        });

        if (hasMatch) {
            const span = document.createElement('span');
            span.innerHTML = combined;
            textNode.parentNode.replaceChild(span, textNode);
        }
    });
}

function renderLatexInChat() {
    loadKatexScript().then(() => {
        // Find the chat history container
        const chatBox = document.getElementById('chat-history-box');
        if (!chatBox) {
            console.log('📐 KaTeX: chat-history-box not found');
            return;
        }

        // Find all text containers in chat that might contain LaTeX
        // Reflex markdown generates nested divs, so we look for any element containing LaTeX patterns
        const allElements = chatBox.querySelectorAll('div, p, span');
        let processedCount = 0;
        allElements.forEach(el => {
            // Skip if already processed or is a KaTeX element
            if (el.dataset.katexProcessed || el.classList.contains('katex') || el.closest('.katex')) {
                return;
            }
            // Skip code blocks
            if (el.closest('code') || el.closest('pre')) {
                return;
            }
            // Check if element contains any LaTeX pattern
            const text = el.textContent || '';

            // Check for LaTeX indicators: $...$ or $$...$$
            // Note: Server-side (formatting.py) converts \[...\], \(...\), and \text{} to $...$
            // So we only need to check for $ here
            const hasDollar = text.includes('$');

            if (hasDollar) {
                renderLatexInElement(el);
                el.dataset.katexProcessed = 'true';
                processedCount++;
            }
        });
        if (processedCount > 0) {
            console.log('📐 KaTeX: Processed', processedCount, 'elements');
        }
    }).catch(err => {
        console.warn('KaTeX not available:', err);
    });
}

// Setup KaTeX observer
var katexObserver = null;

function setupKatexObserver() {
    if (katexObserver) return;

    katexObserver = new MutationObserver((mutations) => {
        let shouldRender = false;
        for (const mutation of mutations) {
            if (mutation.addedNodes.length > 0) {
                shouldRender = true;
                break;
            }
        }
        if (shouldRender) {
            // Debounce rendering
            clearTimeout(window.katexRenderTimeout);
            window.katexRenderTimeout = setTimeout(renderLatexInChat, 100);
        }
    });

    katexObserver.observe(document.body, {
        childList: true,
        subtree: true
    });

    // Initial render
    renderLatexInChat();
    console.log('📐 KaTeX observer active');
}

// Initialize KaTeX after DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupKatexObserver);
} else {
    setTimeout(setupKatexObserver, 500);
}

// ============================================================
// AUDIO PLAYER (audio_player tool target=browser)
// ============================================================
//
// The shared #tts-audio-player element is SSOT for both TTS speech and
// media (audio_player tool). TTS has priority — when TTS pushes a chunk
// while media plays, we save media position, switch to TTS, and resume
// media after TTS finishes.
//
// Server pushes intent via data attributes on the player:
//   data-media-url            → /api/audio/file?key=... or http stream
//   data-media-state-key      → for /api/audio/position
//   data-media-is-stream      → "true" / "false"
//   data-media-paused-for-tts → set when TTS interrupts media
//   data-media-pause-pos      → position to resume from (seconds)
//
// Browser flow:
//   1. URL change observed → load + play media (apply pause-pos with pre-roll)
//   2. TTS push detected → save current pos to /api/audio/position, mark paused-for-tts
//   3. TTS ends + queue empty → resume media at pause-pos minus pre-roll
//   4. Periodic position-save every 30s while media plays (cheap)
//   5. ended event → mark completed via /api/audio/position {completed:true}
// ============================================================

var AUDIO_PRE_ROLL_SEC = 3;            // resume offset after TTS interrupt
var AUDIO_POSITION_SAVE_INTERVAL_MS = 30000;  // periodic save while playing
var audioPositionSaveTimer = null;
var audioCurrentMediaKey = '';           // track which media is loaded
var audioCurrentMediaUrl = '';
// Client-side snapshot of media position at the moment TTS interrupts.
// Used to resume after TTS — the server-side data-media-pause-pos is
// only set by the audio_play tool, not by JS, so we keep our own value.
var audioTtsPauseSnapshotSec = 0;

// Sequenzielles Playback (audio_play_folder).
// audioMediaQueue: full list of {audio_url, state_key} items as set by the
// audio_play_folder tool. JS owns the cursor (audioMediaQueueIdx) once
// playback starts — server state.media_audio_url stays at the FIRST item
// for the entire queue lifetime, and React doesn't re-render src as long
// as that prop doesn't change. JS advances player.src locally when one
// item ends.
var audioMediaQueue = [];
var audioMediaQueueIdx = 0;

function audioPlayerEl() {
    return document.getElementById('tts-audio-player');
}

/** POST current player position to backend for resume tracking. */
async function audioPostPosition({ stateKey, posSec, durationSec, completed }) {
    if (!stateKey) return;
    try {
        await fetch('/api/audio/position', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                state_key: stateKey,
                pos_sec: Math.max(0, Number(posSec) || 0),
                duration_sec: durationSec ? Number(durationSec) : null,
                completed: !!completed,
            }),
        });
    } catch (e) {
        console.warn('🔊 Audio: position POST failed', e);
    }
}

/** Save current player position if a media item is loaded. */
function audioSaveCurrentPosition({ completed = false } = {}) {
    const player = audioPlayerEl();
    if (!player) return;
    if (!audioCurrentMediaKey) return;
    audioPostPosition({
        stateKey: audioCurrentMediaKey,
        posSec: player.currentTime,
        durationSec: isFinite(player.duration) ? player.duration : null,
        completed,
    });
}

/** Start periodic position-save (idempotent). */
function audioStartPositionSaver() {
    if (audioPositionSaveTimer) return;
    audioPositionSaveTimer = setInterval(() => {
        const player = audioPlayerEl();
        if (!player || player.paused || player.ended) return;
        audioSaveCurrentPosition();
    }, AUDIO_POSITION_SAVE_INTERVAL_MS);
}

function audioStopPositionSaver() {
    if (audioPositionSaveTimer) {
        clearInterval(audioPositionSaveTimer);
        audioPositionSaveTimer = null;
    }
}

/**
 * Handle a fresh media URL on the player. Called by the data-media-url
 * MutationObserver. If TTS is currently playing on top, we just remember
 * the URL — JS resume logic will pick it up after TTS ends.
 */
function audioHandleMediaUrlChange(player, newUrl) {
    if (!newUrl) {
        // Cleared → save final position + stop saver
        if (audioCurrentMediaKey) {
            audioSaveCurrentPosition();
        }
        audioCurrentMediaKey = '';
        audioCurrentMediaUrl = '';
        audioStopPositionSaver();
        return;
    }

    audioCurrentMediaUrl = newUrl;
    audioCurrentMediaKey = player.dataset.mediaStateKey || '';

    // Hold off if TTS is playing right now — audioOnEnded will resume.
    const ttsActive = (player.src || '').includes('/_upload/tts_audio/');
    if (ttsActive && !player.paused) {
        console.log('🔊 Audio: media URL queued behind active TTS');
        return;
    }

    // Hold off if the server flagged the media as paused-for-tts. This
    // happens when audio_play is called while TTS is enabled — the LLM
    // is about to speak; we wait for that to finish first.
    const pausedForTts = player.dataset.mediaPausedForTts === 'true';
    if (pausedForTts) {
        console.log('🔊 Audio: media queued — waiting for TTS to finish');
        return;
    }

    audioLoadAndPlayMedia(newUrl);
}

/** Load media URL into player, applying any saved pause-pos with pre-roll. */
function audioLoadAndPlayMedia(url) {
    const player = audioPlayerEl();
    if (!player) return;

    const pausedForTts = player.dataset.mediaPausedForTts === 'true';
    const datasetPos = parseFloat(player.dataset.mediaPausePos || '0');
    // JS-snapshot wins if it's newer (server-state only updates from the
    // audio_play tool, not from JS-side TTS-takeover position-save).
    const pausePosRaw = audioTtsPauseSnapshotSec > 0
        ? audioTtsPauseSnapshotSec
        : datasetPos;
    const isStream = player.dataset.mediaIsStream === 'true';

    let startPos = 0;
    if (pausedForTts && pausePosRaw > 0 && !isStream) {
        startPos = Math.max(0, pausePosRaw - AUDIO_PRE_ROLL_SEC);
    }
    // Consume the snapshot — next interrupt-cycle starts fresh
    audioTtsPauseSnapshotSec = 0;

    console.log(`🔊 Audio: loading media ${url} (start=${startPos}s)`);

    // Listener MUST be attached before assigning src — some browsers fire
    // loadedmetadata synchronously when src is already cached (HTTP-Range),
    // which would otherwise miss the listener and leave the player paused.
    const onLoaded = () => {
        if (startPos > 0) {
            try { player.currentTime = startPos; } catch (e) { /* ignore */ }
        }
        player.removeEventListener('loadedmetadata', onLoaded);
        player.play().then(() => {
            console.log('✅ Audio: media playback started');
            audioStartPositionSaver();
        }).catch(err => {
            console.warn('⚠️ Audio: autoplay blocked:', err.message);
        });
    };
    player.addEventListener('loadedmetadata', onLoaded);

    player.src = url;
    player.load();
}

/**
 * Called when the player switches to TTS while media is loaded.
 * Save current position so we can resume after TTS.
 */
function audioOnTtsTakeover(prevUrl) {
    // prevUrl was a media URL → save its position both server-side
    // (audio_state.json) and as a client-side snapshot for resume.
    if (prevUrl && (prevUrl.includes('/api/audio/file') || /^https?:/.test(prevUrl))) {
        if (audioCurrentMediaKey) {
            const player = audioPlayerEl();
            if (player) {
                audioTtsPauseSnapshotSec = player.currentTime || 0;
            }
            console.log(
                '🔊 Audio: TTS takeover — snapshot at',
                audioTtsPauseSnapshotSec.toFixed(2), 's'
            );
            audioSaveCurrentPosition();
        }
    }
}

/**
 * Player 'ended' event. If we just finished a TTS chunk and the queue is
 * empty AND media was paused for TTS, resume media. Otherwise, if a media
 * item just ended naturally, mark it completed.
 */
function audioOnEnded() {
    const player = audioPlayerEl();
    if (!player) return;

    const wasMedia = (player.src || '').includes('/api/audio/file');
    if (wasMedia && audioCurrentMediaKey) {
        console.log('🔊 Audio: media ended — marking completed');
        audioPostPosition({ stateKey: audioCurrentMediaKey, posSec: 0, completed: true });
        // Disarm any TTS-queue `onended` handler that's still attached
        // (the queue installs one for chunk-advance — if it fires after
        // a media-end it falsely thinks the next TTS chunk completed,
        // advances the queue index, hits 'Playback complete', and the
        // resume-hook re-plays media from start. Endless replay loop.)
        if (typeof player.onended === 'function') {
            player.onended = null;
        }

        // Sequenzielles Playback: wenn noch ein Item in audioMediaQueue
        // wartet, lade es DIREKT (player.src=...) — der Reflex-State
        // bleibt am ersten Item, React rerendered src nicht, weil sich
        // state.media_audio_url nicht ändert.
        if (audioMediaQueue.length > 0 && audioMediaQueueIdx < audioMediaQueue.length - 1) {
            audioMediaQueueIdx += 1;
            const next = audioMediaQueue[audioMediaQueueIdx];
            if (next && next.audio_url) {
                console.log(
                    `🔊 Audio Queue: advancing ${audioMediaQueueIdx + 1}/${audioMediaQueue.length}: ${next.state_key}`
                );
                audioCurrentMediaKey = next.state_key || '';
                audioCurrentMediaUrl = next.audio_url;
                player.src = next.audio_url;
                player.load();
                player.play().then(() => {
                    audioStartPositionSaver();
                }).catch((err) => {
                    console.warn('⚠️ Audio Queue: autoplay blocked on advance:', err.message);
                });
                return;
            }
        }

        // Queue erschöpft (oder keine Queue) — Player räumt auf
        audioCurrentMediaKey = '';
        audioCurrentMediaUrl = '';
        audioStopPositionSaver();
        if (audioMediaQueue.length > 0) {
            console.log('🔊 Audio Queue: playback complete');
        }
        return;
    }

    // TTS chunk ended — resume media only if BOTH:
    //   1. tts queue is empty (no chunk waiting in JS)
    //   2. data-tts-active === 'false' (server says streaming session
    //      has finalized — all pending sentence tasks awaited)
    // The active flag is the authoritative "more TTS may come" signal.
    // Without it, the queue is transiently empty between chunks during
    // streaming and we'd start media too early, cutting off the last
    // sentence ("Soll ich noch etwas..." → schwupps Musik).
    const ttsQueue = document.getElementById('tts-queue-data');
    const queueRaw = ttsQueue ? (ttsQueue.dataset.queue || '[]') : '[]';
    let queueEmpty = true;
    try {
        const parsed = JSON.parse(queueRaw);
        queueEmpty = !Array.isArray(parsed) || parsed.length === 0;
    } catch { /* ignore */ }

    if (!queueEmpty) return;  // chunk already in queue

    const ttsActive = ttsQueue && ttsQueue.dataset.ttsActive === 'true';
    if (ttsActive) {
        console.log('🔊 Audio: TTS chunk done but stream still active — holding media');
        return;
    }

    // Resume-URL prefers JS-side audioCurrentMediaUrl (correct after queue-
    // advance) over the server-pushed data-media-url (which still points to
    // the FIRST queue item — Reflex state stays put while JS owns the cursor).
    const mediaUrl = audioCurrentMediaUrl || (player.dataset.mediaUrl || '');
    const pausedForTts = player.dataset.mediaPausedForTts === 'true';
    if (mediaUrl && pausedForTts) {
        console.log('🔊 Audio: TTS stream finalized — resuming media');
        audioLoadAndPlayMedia(mediaUrl);
    }
}

// Document-level capture listeners — survive React remounts of the
// player element (the `key=` on <audio> forces remount on every TTS
// push, so per-element addEventListener wouldn't last).
// 'ended', 'pause', 'play' don't bubble, so we use capture phase.
document.addEventListener('ended', (e) => {
    if (e.target && e.target.id === 'tts-audio-player') audioOnEnded();
}, true);
document.addEventListener('pause', (e) => {
    if (e.target && e.target.id === 'tts-audio-player') {
        if (audioCurrentMediaKey && (e.target.src || '').includes('/api/audio/file')) {
            audioSaveCurrentPosition();
        }
    }
}, true);

// Re-attach MutationObservers whenever the player element gets remounted.
// Tracks the current player instance and re-binds observers when a new
// one shows up (React replaces the node when `key=` changes).
var audioCurrentPlayer = null;
var audioPrevSrc = '';
var audioSrcObserver = null;
var audioDataObserver = null;
// Bus-Event arrived before the <audio> element was in the DOM. Replayed
// from audioBindObservers once the element appears.
var _audioPendingMediaUrl = '';

function audioBindObservers(player) {
    if (audioSrcObserver) audioSrcObserver.disconnect();
    if (audioDataObserver) audioDataObserver.disconnect();

    audioPrevSrc = player.src || '';
    audioSrcObserver = new MutationObserver(() => {
        const newSrc = player.src || '';
        if (newSrc === audioPrevSrc) return;
        const wasMedia = audioPrevSrc.includes('/api/audio/file');
        const nowTts = newSrc.includes('/_upload/tts_audio/');
        if (wasMedia && nowTts) audioOnTtsTakeover(audioPrevSrc);
        audioPrevSrc = newSrc;
    });
    audioSrcObserver.observe(player, { attributes: true, attributeFilter: ['src'] });

    // Folder-Queue sync (audio_play_folder pushes a list of items via
    // data-media-queue). Server-side it's still the trigger for sequential
    // playback advance — the Audio Bus delivers the FIRST item via
    // kind="media", and the queue attribute lets JS pre-stage the rest.
    audioDataObserver = new MutationObserver((mutations) => {
        for (const m of mutations) {
            if (m.attributeName === 'data-media-queue') {
                audioSyncMediaQueue(player);
            }
        }
    });
    audioDataObserver.observe(player, {
        attributes: true,
        attributeFilter: ['data-media-queue'],
    });

    // Replay any media URL that was pushed via the Audio Bus BEFORE
    // this element existed in the DOM (race between SSE event arrival
    // and Reflex rendering the <audio> after media_audio_url state-set).
    if (_audioPendingMediaUrl) {
        const pending = _audioPendingMediaUrl;
        _audioPendingMediaUrl = '';
        console.log(`🔊 Audio Bus: replaying deferred media URL ${pending}`);
        audioLoadAndPlayMedia(pending);
    }

    // Initial queue sync. NOTE: data-media-url is NOT used as a trigger
    // anymore — that path is the Audio Bus (kind="media"). Re-mount of
    // the player element no longer auto-replays the URL, so a user pause
    // sticks even when React swaps the audio node.
    audioSyncMediaQueue(player);
}

/**
 * Re-sync audioMediaQueue from data-media-queue. Reset cursor to the item
 * matching the current src — so when a new queue arrives we don't lose
 * position. If the current track isn't in the new queue, start at 0.
 */
function audioSyncMediaQueue(player) {
    const raw = player.dataset.mediaQueue || '[]';
    let parsed = [];
    try {
        parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) parsed = [];
    } catch {
        parsed = [];
    }
    audioMediaQueue = parsed;

    if (parsed.length === 0) {
        audioMediaQueueIdx = 0;
        return;
    }

    const currentKey = audioCurrentMediaKey || player.dataset.mediaStateKey || '';
    const idx = currentKey
        ? parsed.findIndex((it) => it && it.state_key === currentKey)
        : -1;
    audioMediaQueueIdx = idx >= 0 ? idx : 0;
    console.log(
        `🔊 Audio Queue: synced ${parsed.length} items, ` +
        `cursor=${audioMediaQueueIdx} (${parsed[audioMediaQueueIdx]?.state_key || '?'})`
    );
}

/** Watch for player element appearance/replacement and re-bind. */
function setupAudioPlayer() {
    const tick = () => {
        const player = audioPlayerEl();
        if (player && player !== audioCurrentPlayer) {
            audioCurrentPlayer = player;
            audioBindObservers(player);
            console.log('🔊 Audio Player bound (remount detected)');
        }
    };
    tick();
    // Re-check periodically — Reflex re-renders force remount on `key=` changes
    setInterval(tick, 500);
}

// ── TTS-Active-Falling-Edge Watcher ───────────────────────────────────
// audioOnEnded prüft data-tts-active und hält media zurück solange noch
// TTS-Sentences kommen können. Aber: wenn der LETZTE Chunk endet, BEVOR
// data-tts-active auf false flippt, läuft audioOnEnded leer (queue empty
// + tts active=true → halt) und feuert nicht nochmal. Dieser Observer
// fängt den Wechsel true→false und triggert dann den resume-check.
var ttsActivePrev = null;
var ttsActiveObserverBound = null;
function bindTtsActiveObserver() {
    const node = document.getElementById('tts-queue-data');
    if (!node || node === ttsActiveObserverBound) return;
    ttsActiveObserverBound = node;
    ttsActivePrev = node.dataset.ttsActive || 'false';
    const obs = new MutationObserver(() => {
        const now = node.dataset.ttsActive || 'false';
        if (ttsActivePrev === 'true' && now === 'false') {
            // Stream-Ende: prüfe ob media-resume fällig ist
            const player = audioPlayerEl();
            if (!player) { ttsActivePrev = now; return; }
            const queueRaw = node.dataset.queue || '[]';
            let queueEmpty = true;
            try {
                const parsed = JSON.parse(queueRaw);
                queueEmpty = !Array.isArray(parsed) || parsed.length === 0;
            } catch { /* ignore */ }
            const mediaUrl = audioCurrentMediaUrl || (player.dataset.mediaUrl || '');
            const pausedForTts = player.dataset.mediaPausedForTts === 'true';
            if (queueEmpty && mediaUrl && pausedForTts && player.paused) {
                console.log('🔊 Audio: TTS stream finalized (post-chunk) — resuming media');
                audioLoadAndPlayMedia(mediaUrl);
            }
        }
        ttsActivePrev = now;
    });
    obs.observe(node, { attributes: true, attributeFilter: ['data-tts-active'] });
}
setInterval(bindTtsActiveObserver, 500);

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(setupAudioPlayer, 600));
} else {
    setTimeout(setupAudioPlayer, 600);
}

// ============================================================
// Enter-to-send: Enter pressed in #user-text-input clicks #send-button
// when the user has enabled the toggle. Shift+Enter falls through to
// the textarea's default behavior (newline). Document-level capture
// listener — survives any remounts of the textarea.
// ============================================================
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    // The only textarea in AIfred's chat UI is the message input.
    // Radix' TextArea places id="user-text-input" on the wrapper div;
    // the inner <textarea> has no id. Identify by tagName + sanity-
    // check that the keydown happened inside #user-text-input subtree.
    if (!e.target || e.target.tagName !== 'TEXTAREA') return;
    const wrapper = document.getElementById('user-text-input');
    if (wrapper && e.target !== wrapper && !wrapper.contains(e.target)) {
        return;  // some other textarea elsewhere
    }
    // Flag lives on #ui-flags (always rendered, unconditional). Earlier
    // attempts used #tts-queue-data which is conditional on TTS being
    // enabled with a non-empty chat — fresh chats had no flag element.
    const flagEl = document.getElementById('ui-flags');
    if (!flagEl || flagEl.dataset.enterSends !== 'true') return;
    if (e.target.disabled) return;
    const sendBtn = document.getElementById('send-button');
    if (!sendBtn || sendBtn.disabled) return;
    e.preventDefault();
    sendBtn.click();
}, true);


// ============================================================
// Vision live-VLM teleprompter SSE manager.
//
// Watches the DOM for .vlm-event-target elements (each carrying a
// data-vlm-source attribute) and opens an EventSource per unique
// source into the backend's /api/vision/events/<source_id>. Server-
// sent JSON {timestamp, description, ...} events get appended to
// the matching DOM target as a 1-per-line teleprompter feed.
//
// Lives inside custom.js because direct rx.script(src="...") tags
// don't reliably execute in Reflex 0.8's popup routes (React-DOM
// strips inline <script> children from foreign tree). custom.js is
// loaded the boring way and runs everywhere.
// ============================================================
(function () {
  console.log('[AIfred-VLM] SSE manager booting');
  if (window.__aifredVLMSSEInit) return;
  window.__aifredVLMSSEInit = true;

  const streams = {};
  const lines = {};
  const MAX_LINES = 8;

  function render(sid) {
    const items = lines[sid] || [];
    document.querySelectorAll(
      '.vlm-event-target[data-vlm-source="' + sid + '"]'
    ).forEach(function (el) {
      if (items.length === 0) {
        el.textContent = el.dataset.idleText || '';
        el.style.fontStyle = 'italic';
        return;
      }
      el.style.fontStyle = 'normal';
      el.textContent = items.join('\n');
      el.scrollTop = el.scrollHeight;
    });
  }

  function openStream(sid) {
    if (streams[sid] && streams[sid].readyState !== 2) return;
    const url = '/api/vision/events/' + sid;
    console.log('[AIfred-VLM] EventSource opening:', url);
    const es = new EventSource(url);
    streams[sid] = es;
    lines[sid] = lines[sid] || [];
    es.onopen = function () {
      console.log('[AIfred-VLM] EventSource open:', sid);
    };
    es.onmessage = function (ev) {
      try {
        const data = JSON.parse(ev.data);
        const ts = (data.timestamp || '').split('T')[1] || '';
        const desc = (data.description || '').replace(/\s+/g, ' ').trim();
        const line = ts + '  ' + desc;
        lines[sid].push(line);
        if (lines[sid].length > MAX_LINES) lines[sid].shift();
        render(sid);
      } catch (e) {
        console.warn('[AIfred-VLM] parse error', e);
      }
    };
    es.onerror = function (e) {
      console.warn('[AIfred-VLM] EventSource error:', sid, e);
    };
  }

  function scan() {
    const targets = document.querySelectorAll(
      '.vlm-event-target[data-vlm-source]'
    );
    const seen = new Set();
    targets.forEach(function (el) {
      const sid = el.dataset.vlmSource;
      if (!sid) return;
      if (!el.dataset.idleText) el.dataset.idleText = el.textContent;
      seen.add(sid);
      openStream(sid);
      render(sid);
    });
    Object.keys(streams).forEach(function (sid) {
      if (!seen.has(sid) && streams[sid]) {
        streams[sid].close();
        delete streams[sid];
      }
    });
  }

  function boot() {
    const obs = new MutationObserver(function () { scan(); });
    obs.observe(document.body, { childList: true, subtree: true });
    scan();
  }
  // Script lebt im <head> via app.head_components — beim ersten Run
  // gibt es noch kein document.body. Auf DOMContentLoaded warten,
  // wenn nötig.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
