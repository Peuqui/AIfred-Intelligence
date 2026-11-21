// A reading the server sends sentence by sentence: between two sentences (the next one still being made) the playlist
// is empty for a while, and the reading must still count as running, so the player stays (21.11.2026: it vanished
// whenever the next sentence took longer). Over only with the server's "tts_end", or a stop.
import { beforeEach, expect, test, vi } from "vitest";

// An audio file that plays until the test ends it (jsdom plays nothing).
class FakeAudio {
  static playing: FakeAudio[] = [];
  volume = 1;
  currentTime = 0;
  duration = 1;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeupdate: (() => void) | null = null;
  ondurationchange: (() => void) | null = null;
  constructor(public src: string) {}
  play(): Promise<void> {
    FakeAudio.playing.push(this);
    return Promise.resolve();
  }
  pause(): void {}
}
vi.stubGlobal("Audio", FakeAudio);

const speech = await import("./speech");

async function settle(): Promise<void> {
  for (let round = 0; round < 5; round += 1) await Promise.resolve();
}

beforeEach(() => {
  speech.stop();
  FakeAudio.playing = [];
});

test("between two sentences the reading still runs, with the end it is over", async () => {
  speech.enqueue("tts", { url: "/a/1.wav" });
  await settle();
  expect(speech.speaking.value).toBe(true);
  FakeAudio.playing[0]!.onended?.();
  await settle();
  expect(speech.speaking.value).toBe(true); // the next sentence is still being made
  speech.enqueue("tts", { url: "/a/2.wav" });
  await settle();
  speech.ended();
  expect(speech.speaking.value).toBe(true); // the last sentence still plays
  FakeAudio.playing[1]!.onended?.();
  await settle();
  expect(speech.speaking.value).toBe(false);
});

test("an end that comes while nothing plays ends it at once", async () => {
  speech.enqueue("tts", { url: "/a/1.wav" });
  await settle();
  FakeAudio.playing[0]!.onended?.();
  await settle();
  speech.ended();
  expect(speech.speaking.value).toBe(false);
});

test("a stop ends a reading that waits for its next sentence", async () => {
  speech.enqueue("tts", { url: "/a/1.wav" });
  await settle();
  FakeAudio.playing[0]!.onended?.();
  await settle();
  speech.stop();
  expect(speech.speaking.value).toBe(false);
});

test("a bubble's reading played again is over when its last part has played", async () => {
  speech.replay(["/a/kept.wav"], []);
  await settle();
  expect(speech.speaking.value).toBe(true);
  FakeAudio.playing[0]!.onended?.();
  await settle();
  expect(speech.speaking.value).toBe(false);
});

test("once something was read aloud in the chat the player keeps it, also after the end and a stop, until another chat", async () => {
  speech.forget();
  expect(speech.spokeHere.value).toBe(false);
  speech.enqueue("tts", { url: "/a/1.wav" });
  await settle();
  FakeAudio.playing[0]!.onended?.();
  speech.ended();
  await settle();
  speech.stop();
  expect(speech.speaking.value).toBe(false);
  expect(speech.spokeHere.value).toBe(true);
  speech.forget();
  expect(speech.spokeHere.value).toBe(false);
});
