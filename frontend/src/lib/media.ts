// Music and audio books in the browser (the audio player's browser channel, lib/audio_channels/browser.py): the
// server sends "media" (a track, or a folder: the tracks after it travel along), "pause", "resume", "seek", "speed"
// and "stop" over the session's event stream. The spoken reply goes first: while it speaks the track pauses, after it
// the track goes on a few seconds earlier (pre_roll_sec, the server's). Where a track stands is saved on the server
// while it plays (every position_save_sec), when it pauses or stops, and as completed when it ends, so it resumes
// there next time. What plays, where it stands and how loud are refs for the visible player (MediaPlayer.vue); its
// buttons go through the server (POST /api/sessions/{id}/media), so every tab and the agents' tools see the same.
import { ref, watch } from "vue";
import { api } from "../api/client";
import { speaking } from "./speech";

type Track = { url: string; stateKey: string };

const player = new Audio();
let track: Track | null = null;
let following: Track[] = [];            // the folder's tracks after the current one
let preRollSec = 0;
let saveEverySec = 0;
let saver: number | null = null;
let pausedForSpeech = false;
let queueEnded: (() => void) | null = null;

export const current = ref("");          // the state_key of the track (folder/file), "" = nothing
export const playing = ref(false);
export const position = ref(0);          // seconds
export const duration = ref(0);          // seconds; Infinity for a stream
export const volume = ref(1);

player.addEventListener("play", () => (playing.value = true));
player.addEventListener("pause", () => (playing.value = false));
player.addEventListener("timeupdate", () => (position.value = player.currentTime));
player.addEventListener("durationchange", () => (duration.value = player.duration));
watch(volume, (level) => (player.volume = level));

// What to do when the last track has ended (the chat store tells the server: nothing plays there any more).
export function onQueueEnd(callback: () => void): void {
  queueEnded = callback;
}

function savePosition(completed = false): void {
  if (!track) return;
  void api("audio/position", {
    method: "POST",
    body: JSON.stringify({
      state_key: track.stateKey,
      pos_sec: Math.max(0, player.currentTime || 0),
      duration_sec: Number.isFinite(player.duration) ? player.duration : null,
      completed,
    }),
  });
}

function keepSaving(on: boolean): void {
  if (saver !== null) window.clearInterval(saver);
  saver = on && saveEverySec > 0 ? window.setInterval(() => savePosition(), saveEverySec * 1000) : null;
}

function load(next: Track, startSec: number): void {
  track = next;
  current.value = next.stateKey;
  player.src = next.url;
  player.currentTime = 0;
  if (startSec > 0) player.addEventListener("loadedmetadata", () => { player.currentTime = startSec; }, { once: true });
  if (speaking.value) {
    pausedForSpeech = true;        // it starts once the reply has been spoken
    return;
  }
  void player.play().then(() => keepSaving(true));
}

player.addEventListener("ended", () => {
  savePosition(true);
  keepSaving(false);
  const next = following.shift();
  if (next) {
    load(next, 0);
    return;
  }
  track = null;
  current.value = "";
  queueEnded?.();
});

export function handle(kind: string, data: Record<string, unknown>): void {
  if (kind === "media") {
    preRollSec = Number(data.pre_roll_sec ?? 0);
    saveEverySec = Number(data.position_save_sec ?? 0);
    following = ((data.queue as { audio_url: string; state_key: string }[] | undefined) ?? [])
      .map((item) => ({ url: item.audio_url, stateKey: item.state_key }));
    load({ url: String(data.url), stateKey: String(data.state_key ?? "") }, Number(data.start_pos_sec ?? 0));
  } else if (kind === "pause") {
    if (!player.paused) {
      savePosition();
      player.pause();
      keepSaving(false);
    }
    pausedForSpeech = false;
  } else if (kind === "resume") {
    if (track && player.paused) void player.play().then(() => keepSaving(true));
  } else if (kind === "seek") {
    if (Number.isFinite(player.duration)) {
      const target = data.relative ? player.currentTime + Number(data.position_sec ?? 0) : Number(data.position_sec ?? 0);
      player.currentTime = Math.max(0, Math.min(player.duration, target));
      savePosition();
    }
  } else if (kind === "speed") {
    player.playbackRate = Number(data.factor ?? 1);
  }
}

export function stop(): void {
  if (track) savePosition();
  player.pause();
  keepSaving(false);
  track = null;
  current.value = "";
  following = [];
  pausedForSpeech = false;
}

// The spoken reply goes first: the track waits while it speaks and goes on a little earlier afterwards.
watch(speaking, (now) => {
  if (now && track && !player.paused) {
    savePosition();
    player.pause();
    keepSaving(false);
    pausedForSpeech = true;
  } else if (!now && pausedForSpeech && track) {
    pausedForSpeech = false;
    player.currentTime = Math.max(0, player.currentTime - preRollSec);
    void player.play().then(() => keepSaving(true));
  }
});
