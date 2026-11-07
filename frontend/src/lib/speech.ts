// The spoken reply in the browser. The server (lib/reply_speech.py, lib/browser_reading.py) puts the
// reading on the session's playlist through the event stream: an audio file ("tts"), silence
// between paragraphs ("tts_pause", milliseconds), or a part the browser's own voice speaks
// ("tts_speak", JSON of the utterance). They play in the order they arrive, without gaps; "stop"
// ends the reading.
import { ref } from "vue";
import type { Utterance } from "../api/types";

type Entry = { kind: "audio"; url: string } | { kind: "pause"; ms: number } | { kind: "speak"; utterance: Utterance };

const queue: Entry[] = [];
let playing = false;
let rate = 1;
let current: HTMLAudioElement | null = null;
let pauseTimer: ReturnType<typeof setTimeout> | null = null;
let finishPause: (() => void) | null = null;

// True while something plays or waits on the playlist: the stop button shows as long as it is.
export const speaking = ref(false);

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

export function setRate(value: number): void {
  if (value > 0) rate = value;
  if (current) current.playbackRate = rate;
}

export function enqueue(kind: string, data: Record<string, unknown>): void {
  const url = String(data.url ?? "");
  if (data.playback_rate) setRate(parseFloat(String(data.playback_rate).replace("x", "")));
  if (kind === "tts") queue.push({ kind: "audio", url });
  else if (kind === "tts_pause") queue.push({ kind: "pause", ms: Number(url) });
  else if (kind === "tts_speak") queue.push({ kind: "speak", utterance: JSON.parse(url) as Utterance });
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
  if (pauseTimer) clearTimeout(pauseTimer);
  pauseTimer = null;
  finishPause?.();
  window.speechSynthesis?.cancel();
  playing = false;
  speaking.value = false;
}

// What a finished bubble kept of its reading, played again on request: its audio, or the parts
// the browser's own voice spoke.
export function replay(audioUrls: string[], browserSpeech: Utterance[]): void {
  stop();
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
    audio.playbackRate = rate;
    current = audio;
    audio.onended = () => done();
    audio.onpause = () => done();
    // A missing file must not stop the rest of the reading; it is skipped, visibly.
    audio.onerror = () => {
      console.warn(`speech: audio ${url} failed to load, skipped`);
      done();
    };
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
