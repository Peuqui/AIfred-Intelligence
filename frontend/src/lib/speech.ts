// The spoken reply in the browser. The server (lib/browser_reading.py) puts the reading on the
// session's playlist through the event stream: an audio file ("tts"), silence between paragraphs
// ("tts_pause", milliseconds), or a part the browser's own voice speaks ("tts_speak", JSON of the
// utterance). They play in the order they arrive, without gaps; "stop" ends the reading.

type Entry = { kind: "audio"; url: string } | { kind: "pause"; ms: number } | { kind: "speak"; utterance: Utterance };

interface Utterance {
  text: string;
  lang?: string;
  voice?: string;
  rate?: number;
  pitch?: number;
}

const queue: Entry[] = [];
let playing = false;
let rate = 1;
let current: HTMLAudioElement | null = null;
let pauseTimer: ReturnType<typeof setTimeout> | null = null;

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

export function stop(): void {
  queue.length = 0;
  current?.pause();
  current = null;
  if (pauseTimer) clearTimeout(pauseTimer);
  pauseTimer = null;
  window.speechSynthesis?.cancel();
  playing = false;
}

// A finished bubble's combined reply audio, played again on request.
export function replay(urls: string[]): void {
  stop();
  for (const url of urls) queue.push({ kind: "audio", url });
  void next();
}

async function next(): Promise<void> {
  if (playing) return;
  const entry = queue.shift();
  if (!entry) return;
  playing = true;
  try {
    if (entry.kind === "audio") await playAudio(entry.url);
    else if (entry.kind === "pause") await new Promise<void>((done) => (pauseTimer = setTimeout(done, entry.ms)));
    else await speak(entry.utterance);
  } finally {
    playing = false;
    current = null;
    pauseTimer = null;
  }
  void next();
}

function playAudio(url: string): Promise<void> {
  return new Promise((done) => {
    const audio = new Audio(url);
    audio.playbackRate = rate;
    current = audio;
    audio.onended = () => done();
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

function speak(utterance: Utterance): Promise<void> {
  return new Promise((done) => {
    if (!window.speechSynthesis) return done();
    const spoken = new SpeechSynthesisUtterance(utterance.text);
    if (utterance.lang) spoken.lang = utterance.lang;
    if (utterance.rate) spoken.rate = utterance.rate;
    if (utterance.pitch) spoken.pitch = utterance.pitch;
    const voice = window.speechSynthesis.getVoices().find((candidate) => candidate.name === utterance.voice);
    if (voice) spoken.voice = voice;
    spoken.onend = () => done();
    spoken.onerror = () => done();
    window.speechSynthesis.speak(spoken);
  });
}
