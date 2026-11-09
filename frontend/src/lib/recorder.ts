// A dictation in the browser, a high beep at the start and a low one at the end. Two ways, chosen in
// the dictation settings (lib/dictation.py on the server):
// - recording for the STT-Suite (as assets/custom.js did for the Reflex UI): the microphone with echo
//   cancellation, noise suppression and gain control, webm/opus, uploaded when it ends;
// - the browser's own recognition (Web Speech API, as Agent-Orc's useDictation.ts): Chrome and Edge
//   send the speech to Google or Microsoft, Firefox has none. The browser stops by itself after a
//   silence and is started again, so a pause to think does not end the dictation; only the button
//   does. What was heard comes as one text at the end.
// ``cancel`` throws a dictation away: nothing is transcribed or handed on (the × beside the microphone, Escape).
// Browsers give the microphone only to HTTPS pages and localhost.
import { ref } from "vue";

const START_BEEP = { frequency: 1000, ms: 150 };
const STOP_BEEP = { frequency: 600, ms: 200 };

export const recording = ref(false);

let recorder: MediaRecorder | null = null;
let chunks: Blob[] = [];
let finished: ((file: File) => void) | null = null;

/** The parts of the Web Speech API used here; TypeScript's DOM types do not include it. */
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type RecognitionClass = new () => Recognition;
const speechWindow = window as unknown as { SpeechRecognition?: RecognitionClass; webkitSpeechRecognition?: RecognitionClass };
const BrowserRecognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;

let recognition: Recognition | null = null;
let ending = false;
let discarded = false;

function beep({ frequency, ms }: { frequency: number; ms: number }): void {
  const context = new AudioContext();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.frequency.value = frequency;
  oscillator.type = "sine";
  gain.gain.setValueAtTime(0.3, context.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.01, context.currentTime + ms / 1000);
  oscillator.start(context.currentTime);
  oscillator.stop(context.currentTime + ms / 1000);
}

// Starts the recording; ``done`` gets it as a file when ``stop`` ends it. Throws when the page may not
// use the microphone (not a secure page) or the user refused it.
export async function start(done: (file: File) => void): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("insecure");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  chunks = [];
  finished = done;
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  recorder.onstop = () => {
    for (const track of stream.getTracks()) track.stop();
    finished?.(new File(chunks, "recording.webm", { type: "audio/webm" }));
    recorder = null;
  };
  recorder.start();
  recording.value = true;
  beep(START_BEEP);
}

// Listens through the browser's own recognition in ``language``; ``done`` gets what was heard when
// ``stop`` ends it. Throws "unsupported" when the browser has no recognition (Firefox), "insecure" when
// the page may not use the microphone.
export function startBrowser(language: string, done: (text: string) => void, failed: (error: string) => void): void {
  if (BrowserRecognition === undefined) throw new Error("unsupported");
  if (!window.isSecureContext) throw new Error("insecure");
  const listener = new BrowserRecognition();
  const heard: string[] = [];
  listener.lang = language;
  listener.continuous = true;
  listener.interimResults = false;
  listener.onresult = (event) => {
    for (let index = event.resultIndex; index < event.results.length; index++) {
      const result = event.results[index];
      if (result?.isFinal) heard.push(result[0].transcript.trim());
    }
  };
  // "no-speech" is a silence before anything was said: it ends like any other silence.
  listener.onerror = (event) => {
    if (event.error === "no-speech") return;
    ending = true;
    failed(event.error);
  };
  listener.onend = () => {
    if (!ending) {
      listener.start();
      return;
    }
    recognition = null;
    recording.value = false;
    const text = heard.filter(Boolean).join(" ");
    if (text && !discarded) done(text);
  };
  ending = false;
  discarded = false;
  recognition = listener;
  listener.start();
  recording.value = true;
  beep(START_BEEP);
}

export function stop(): void {
  if (recognition) {
    beep(STOP_BEEP);
    ending = true;
    recognition.stop();
    return;
  }
  if (!recorder) return;
  beep(STOP_BEEP);
  recorder.stop();
  recording.value = false;
}

export function cancel(): void {
  if (recognition) {
    beep(STOP_BEEP);
    discarded = true;
    ending = true;
    recognition.stop();
    return;
  }
  if (!recorder) return;
  beep(STOP_BEEP);
  finished = null;
  recorder.stop();
  recording.value = false;
}
