<script setup lang="ts">
// The one player of this page: the spoken reply (lib/speech.ts) and the audio player's music and audio books
// (lib/media.ts). It shows what speaks or plays now; with nothing playing it offers the last reading again.
// Play, pause, stop and seek of music go through the server like an agent's tool, so every tab of the session
// follows; a reading is this device's own, like the volume. A reading that is still arriving comes piece by piece
// and has no length to seek in; once it is over, the kept audio (one file) can be played again and sought in.
import { FastForward, Pause, Play, Rewind, SkipBack, Square } from "lucide-vue-next";
import { computed } from "vue";
import { t } from "../i18n";
import * as media from "../lib/media";
import * as speech from "../lib/speech";
import { useChat } from "../stores/chat";

const SKIP_SEC = 10;
const chat = useChat();

const mode = computed<"speech" | "media" | "kept" | null>(() => {
  if (speech.speaking.value) return "speech";
  if (media.current.value) return "media";
  return speech.hasReading.value ? "kept" : null;
});
const isSpeech = computed(() => mode.value !== "media");
const name = computed(() => (isSpeech.value ? t("speech_player_title") : (media.current.value.split("/").pop() ?? "")));
const playing = computed(() => (isSpeech.value ? mode.value === "speech" && !speech.paused.value : media.playing.value));
const position = computed(() => (isSpeech.value ? speech.position.value : media.position.value));
const duration = computed(() => (isSpeech.value ? speech.duration.value : media.duration.value));
// A reading that is still arriving comes piece by piece: its bar is there, but it cannot be moved.
const seekable = computed(() => (!isSpeech.value || !speech.live.value) && Number.isFinite(duration.value) && duration.value > 0);
const length = computed(() => (Number.isFinite(duration.value) && duration.value > 0 ? duration.value : 0)); // a stream has none
const volume = computed({
  get: () => (isSpeech.value ? speech.volume.value : media.volume.value),
  set: (level: number) => {
    if (isSpeech.value) speech.volume.value = level;
    else media.volume.value = level;
  },
});

function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const rest = `${Math.floor((total % 3600) / 60)}`.padStart(hours ? 2 : 1, "0") + `:${String(total % 60).padStart(2, "0")}`;
  return hours ? `${hours}:${rest}` : rest;
}

function seekTo(seconds: number): void {
  if (mode.value === "kept") speech.replayKept(seconds);
  else if (isSpeech.value) speech.seek(seconds);
  else void chat.mediaControl("seek", Math.max(0, Math.min(media.duration.value, seconds)));
}

function playPause(): void {
  if (mode.value === "kept") speech.replayKept();
  else if (isSpeech.value) speech.togglePause();
  else void chat.mediaControl(media.playing.value ? "pause" : "resume");
}

function stop(): void {
  if (isSpeech.value) speech.stop();
  else void chat.mediaControl("stop");
}
</script>

<template>
  <div v-if="mode" class="player" :title="name">
    <div class="controls">
      <button v-if="isSpeech" :title="t('audio_restart')" :disabled="!speech.hasReading.value" @click="speech.replayKept()"><SkipBack :size="16" /></button>
      <button :title="t('audio_back')" :disabled="!seekable" @click="seekTo(position - SKIP_SEC)"><Rewind :size="16" /></button>
      <button :title="playing ? t('audio_pause') : t('audio_resume')" @click="playPause"><Pause v-if="playing" :size="16" /><Play v-else :size="16" /></button>
      <button :title="t('audio_forward')" :disabled="!seekable" @click="seekTo(position + SKIP_SEC)"><FastForward :size="16" /></button>
      <button :title="t('audio_stop')" :disabled="mode === 'kept'" @click="stop"><Square :size="16" /></button>
      <input v-model.number="volume" class="slider volume" type="range" min="0" max="1" step="0.05" :title="t('audio_volume')" />
    </div>
    <div class="progress">
      <span class="time">{{ clock(position) }}</span>
      <input class="slider seek" type="range" min="0" :max="length || 1" step="1" :value="position" :disabled="!seekable" @change="seekTo(Number(($event.target as HTMLInputElement).value))" />
      <span class="time">{{ length ? clock(length) : "–:––" }}</span>
    </div>
  </div>
</template>

<style scoped>
.player { display: flex; flex-direction: column; gap: 4px; padding: 4px 0; border-bottom: 1px solid var(--aifred-border); font-size: 0.85rem; min-width: 0; }
.controls, .progress { display: flex; align-items: center; gap: 8px; min-width: 0; }
.controls { flex-wrap: nowrap; }
button { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; padding: 0 8px; font: inherit; }
.time { color: var(--aifred-text-muted); font-variant-numeric: tabular-nums; }
.seek { flex: 1; min-width: 60px; }
/* Both sliders: a thin groove and the same small knob. */
.slider { -webkit-appearance: none; appearance: none; height: 12px; background: transparent; accent-color: var(--aifred-primary); }
.volume { width: 80px; }
.slider::-webkit-slider-runnable-track { height: 2px; border-radius: 1px; background: var(--aifred-primary); }
.slider::-moz-range-track { height: 2px; border-radius: 1px; background: var(--aifred-primary); }
.slider::-webkit-slider-thumb { -webkit-appearance: none; width: 10px; height: 10px; margin-top: -4px; border-radius: 50%; border: none; background: var(--aifred-primary); }
.slider::-moz-range-thumb { width: 10px; height: 10px; border-radius: 50%; border: none; background: var(--aifred-primary); }
.slider:disabled { opacity: 0.4; }
</style>
