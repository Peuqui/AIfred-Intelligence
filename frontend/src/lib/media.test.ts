// The browser's music (lib/media.ts): a folder plays track after track (08.11.2026: the tracks after the first did
// not reach the Vue page), the spoken reply pauses it and it goes on afterwards a little earlier, where a track stands
// is saved on the server, and "stop" ends it. jsdom plays no audio: play and pause are stand-ins, fetch records the
// saved positions, and the module's player is caught where it is made.
import { beforeAll, beforeEach, expect, test, vi } from "vitest";
import { nextTick } from "vue";

const played: string[] = [];
const saved: Record<string, unknown>[] = [];
const made: HTMLAudioElement[] = [];
let media: typeof import("./media");
let speaking: typeof import("./speech").speaking;

beforeAll(async () => {
  HTMLMediaElement.prototype.play = function play(this: HTMLMediaElement) {
    played.push(this.src);
    Object.defineProperty(this, "paused", { configurable: true, value: false });
    return Promise.resolve();
  };
  HTMLMediaElement.prototype.pause = function pause(this: HTMLMediaElement) {
    Object.defineProperty(this, "paused", { configurable: true, value: true });
  };
  globalThis.fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
    saved.push(JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>);
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  const RealAudio = window.Audio;
  window.Audio = function Audio(src?: string) {
    const element = new RealAudio(src);
    made.push(element);
    return element;
  } as unknown as typeof window.Audio;
  media = await import("./media");
  speaking = (await import("./speech")).speaking;
});
beforeEach(() => {
  played.length = 0;
  saved.length = 0;
});

const player = (): HTMLAudioElement => {
  const element = made[0];
  if (!element) throw new Error("the module made no player");
  return element;
};
const flush = (): Promise<void> => new Promise((done) => setTimeout(done, 0));

test("a folder plays track after track and marks each one completed", async () => {
  media.handle("media", {
    url: "/api/audio/file?key=Musik/a.mp3", state_key: "Musik/a.mp3", start_pos_sec: 0, pre_roll_sec: 3, position_save_sec: 30,
    queue: [{ audio_url: "/api/audio/file?key=Musik/b.mp3", state_key: "Musik/b.mp3" }],
  });
  await flush();
  expect(played.at(-1)).toContain("key=Musik/a.mp3");
  player().dispatchEvent(new Event("ended"));
  await flush();
  expect(saved.at(-1)).toMatchObject({ state_key: "Musik/a.mp3", completed: true });
  expect(played.at(-1)).toContain("key=Musik/b.mp3");
  player().dispatchEvent(new Event("ended"));
  await flush();
  expect(saved.at(-1)).toMatchObject({ state_key: "Musik/b.mp3", completed: true });
  expect(played.filter((url) => url.includes("Musik/"))).toHaveLength(2);   // a and b, nothing after the last track
});

test("the spoken reply pauses the track, which goes on afterwards", async () => {
  media.handle("media", { url: "/api/audio/file?key=Hörbuch/x.mp3", state_key: "Hörbuch/x.mp3", pre_roll_sec: 3, position_save_sec: 30 });
  await flush();
  played.length = 0;
  speaking.value = true;
  await nextTick();
  expect(player().paused).toBe(true);
  expect(saved.at(-1)).toMatchObject({ state_key: "Hörbuch/x.mp3", completed: false });
  speaking.value = false;
  await nextTick();
  await flush();
  expect(played).toHaveLength(1);
  expect(player().paused).toBe(false);
});

test("pause keeps the place, resume goes on, stop saves and ends", async () => {
  media.handle("media", { url: "/api/audio/file?key=Musik/c.mp3", state_key: "Musik/c.mp3", pre_roll_sec: 3, position_save_sec: 30 });
  await flush();
  media.handle("pause", {});
  expect(player().paused).toBe(true);
  expect(saved.at(-1)).toMatchObject({ state_key: "Musik/c.mp3" });
  played.length = 0;
  media.handle("resume", {});
  await flush();
  expect(played).toHaveLength(1);
  saved.length = 0;
  media.stop();
  expect(player().paused).toBe(true);
  expect(saved).toHaveLength(1);
  media.handle("resume", {});               // nothing left to go on with
  await flush();
  expect(played).toHaveLength(1);
});
