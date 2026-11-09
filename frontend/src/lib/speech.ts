// The spoken reply in the browser. The server (lib/reply_speech.py, lib/browser_reading.py) puts the
// reading on the session's playlist through the event stream: an audio file ("tts", url), silence
// between paragraphs ("tts_pause", ms), or a part the browser's own voice speaks ("tts_speak",
// utterance). The speed is in the audio already (the agent's voice settings). They play in the order they arrive, without gaps; "stop"
// ends the reading.
import { ref, watch } from "vue";
import type { Utterance } from "../api/types";

type Entry = { kind: "audio"; url: string } | { kind: "pause"; ms: number } | { kind: "speak"; utterance: Utterance };

const queue: Entry[] = [];
let playing = false;
let current: HTMLAudioElement | null = null;
let pauseTimer: ReturnType<typeof setTimeout> | null = null;
let finishPause: (() => void) | null = null;
let finishAudio: (() => void) | null = null;

// True while something plays or waits on the playlist: the stop button shows as long as it is.
export const speaking = ref(false);
// The reading is held (not stopped): the playlist stays where it is. Volume is this device's own.
export const paused = ref(false);
export const volume = ref(1);
// The last reading, kept for the player: what a finished bubble kept of it (one audio file, so it can be seeked
// in), or the parts the device's own voice spoke. A reading still arriving is not seekable: its files come piece by piece.
let kept: { audioUrls: string[]; browserSpeech: Utterance[] } | null = null;
export const hasReading = ref(false);
export const live = ref(false);
export const position = ref(0);          // seconds, in the audio that plays
export const duration = ref(0);
watch(volume, (level) => {
  if (current) current.volume = level;
});

// Browsers allow audio.play() only after a gesture of the user on the page. The first click or key
// press plays a silent sound once; from then on the readings that arrive later may sound.
const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
function unlock(): void {
  void new Audio(SILENCE).play().catch(() => undefined);
  window.removeEventListener("pointerdown", unlock);
  window.removeEventListener("keydown", unlock);
}
window.addEventListener("pointerdown", unlock);
window.addEventListener("keydown", unlock);

export function enqueue(kind: string, data: Record<string, unknown>): void {
  live.value = true;
  if (kind === "tts") queue.push({ kind: "audio", url: String(data.url) });
  else if (kind === "tts_pause") queue.push({ kind: "pause", ms: Number(data.ms) });
  else if (kind === "tts_speak") queue.push({ kind: "speak", utterance: data.utterance as Utterance });
  void next();
}

// Generation of the playlist: bumped on every stop, so an entry that ends after the stop does not
// advance a playlist that has moved on.
let generation = 0;

export function stop(): void {
  generation += 1;
  queue.length = 0;
  current?.pause();
  current = null;
  finishAudio?.();
  window.speechSynthesis?.resume();
  paused.value = false;
  if (pauseTimer) clearTimeout(pauseTimer);
  pauseTimer = null;
  finishPause?.();
  window.speechSynthesis?.cancel();
  playing = false;
  speaking.value = false;
}

// Holds the reading and goes on where it was (the audio and the device's own voice; a silence between
// paragraphs runs out meanwhile).
export function togglePause(): void {
  paused.value = !paused.value;
  if (paused.value) {
    current?.pause();
    window.speechSynthesis?.pause();
  } else {
    void current?.play();
    window.speechSynthesis?.resume();
  }
}

// A finished bubble's reading becomes the one the player offers; nothing plays. Without audio the player has none.
export function keep(audioUrls: string[], browserSpeech: Utterance[]): void {
  kept = { audioUrls, browserSpeech };
  hasReading.value = audioUrls.length + browserSpeech.length > 0;
  position.value = 0;
  duration.value = 0;
  // A single file has a length the player shows before it plays; a reading in pieces has none.
  const [only] = audioUrls;
  if (only !== undefined && audioUrls.length === 1 && browserSpeech.length === 0) {
    const probe = new Audio();
    probe.preload = "metadata";
    probe.onloadedmetadata = () => {
      if (kept?.audioUrls[0] === only && !speaking.value) duration.value = probe.duration;
    };
    probe.src = only;
  }
}

// Plays the kept reading again, from the start or from a place in it (the file is one piece).
export function replayKept(fromSeconds = 0): void {
  if (!kept) return;
  replay(kept.audioUrls, kept.browserSpeech);
  if (current && fromSeconds > 0) current.currentTime = fromSeconds;
}

// Seconds from the start of the audio that plays (not for a reading that is still arriving).
export function seek(seconds: number): void {
  if (current && Number.isFinite(current.duration)) current.currentTime = Math.max(0, Math.min(current.duration, seconds));
}

// What a finished bubble kept of its reading, played again on request: its audio, or the parts
// the browser's own voice spoke.
export function replay(audioUrls: string[], browserSpeech: Utterance[]): void {
  stop();
  keep(audioUrls, browserSpeech);
  live.value = false;
  for (const url of audioUrls) queue.push({ kind: "audio", url });
  for (const utterance of browserSpeech) queue.push({ kind: "speak", utterance });
  void next();
}

async function next(): Promise<void> {
  if (playing) return;
  const entry = queue.shift();
  if (!entry) {
    speaking.value = false;
    return;
  }
  playing = true;
  speaking.value = true;
  const mine = generation;
  if (entry.kind === "audio") await playAudio(entry.url);
  else if (entry.kind === "pause") await pause(entry.ms);
  else await speak(entry.utterance);
  if (mine !== generation) return;
  playing = false;
  current = null;
  void next();
}

function pause(ms: number): Promise<void> {
  return new Promise((done) => {
    finishPause = () => {
      finishPause = null;
      done();
    };
    pauseTimer = setTimeout(finishPause, ms);
  });
}

function playAudio(url: string): Promise<void> {
  return new Promise((done) => {
    const audio = new Audio(url);
    audio.volume = volume.value;
    position.value = 0;
    duration.value = 0;
    audio.ontimeupdate = () => (position.value = audio.currentTime);
    audio.ondurationchange = () => (duration.value = audio.duration);
    current = audio;
    finishAudio = () => {
      finishAudio = null;
      done();
    };
    audio.onended = finishAudio;
    // A missing file must not stop the rest of the reading; it is skipped, visibly.
    audio.onerror = () => {
      console.warn(`speech: audio ${url} failed to load, skipped`);
      done();
    };
    if (paused.value) return; // held: togglePause() starts it
    audio.play().catch((error: unknown) => {
      console.warn(`speech: audio ${url} could not play (${String(error)}), skipped`);
      done();
    });
  });
}

// The device's own voice (as assets/custom.js speakInBrowser): a named voice if the device has it,
// for "Auto" or no voice one of the device's voices for the language.
function speak(utterance: Utterance): Promise<void> {
  return new Promise((done) => {
    if (!window.speechSynthesis) {
      console.warn("speech: speechSynthesis is not available in this browser");
      return done();
    }
    const spoken = new SpeechSynthesisUtterance(utterance.text);
    spoken.lang = utterance.language;
    const voices = window.speechSynthesis.getVoices();
    if (utterance.voice && utterance.voice !== "Auto") {
      const named = voices.find((voice) => voice.name === utterance.voice);
      if (named) spoken.voice = named;
    } else {
      const language = utterance.language.toLowerCase();
      const matching = voices.filter((voice) => voice.lang.toLowerCase().startsWith(language));
      const pick = matching.find((voice) => voice.default) ?? matching.find((voice) => voice.localService) ?? matching[0];
      if (pick) spoken.voice = pick;
    }
    spoken.rate = utterance.rate ?? 1;
    spoken.pitch = utterance.pitch ?? 1;
    spoken.onend = () => done();
    spoken.onerror = (event) => {
      if (event.error !== "canceled" && event.error !== "interrupted") console.warn(`speech: ${event.error}`);
      done();
    };
    window.speechSynthesis.speak(spoken);
  });
}
