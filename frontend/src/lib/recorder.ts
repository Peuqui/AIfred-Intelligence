// Recording a dictation in the browser (as assets/custom.js did for the Reflex UI): the microphone with
// echo cancellation, noise suppression and gain control, webm/opus, a high beep at the start and a low
// one at the end. Browsers give the microphone only to HTTPS pages and localhost.
import { ref } from "vue";

const START_BEEP = { frequency: 1000, ms: 150 };
const STOP_BEEP = { frequency: 600, ms: 200 };

export const recording = ref(false);

let recorder: MediaRecorder | null = null;
let chunks: Blob[] = [];
let finished: ((file: File) => void) | null = null;

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

export function stop(): void {
  if (!recorder) return;
  beep(STOP_BEEP);
  recorder.stop();
  recording.value = false;
}
