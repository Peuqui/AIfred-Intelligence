<script setup lang="ts">
// The audio player's music and audio books in this page (lib/media.ts), visible while something plays: what it is,
// play/pause, stop, where it stands (a stream has no end to seek in) and how loud. Play, pause, stop and seek go
// through the server like an agent's tool, so every tab of the session follows; the volume is this device's own.
import { computed } from "vue";
import { t } from "../i18n";
import * as media from "../lib/media";
import { useChat } from "../stores/chat";

const chat = useChat();
const name = computed(() => media.current.value.split("/").pop() ?? "");
const seekable = computed(() => Number.isFinite(media.duration.value) && media.duration.value > 0);

function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const rest = `${Math.floor((total % 3600) / 60)}`.padStart(hours ? 2 : 1, "0") + `:${String(total % 60).padStart(2, "0")}`;
  return hours ? `${hours}:${rest}` : rest;
}

function seek(event: Event): void {
  void chat.mediaControl("seek", Number((event.target as HTMLInputElement).value));
}
</script>

<template>
  <div v-if="media.current.value" class="player">
    <span class="what" :title="media.current.value">🎵 {{ name }}</span>
    <button :title="media.playing.value ? t('audio_pause') : t('audio_resume')" @click="chat.mediaControl(media.playing.value ? 'pause' : 'resume')">
      {{ media.playing.value ? "⏸" : "▶" }}
    </button>
    <button :title="t('audio_stop')" @click="chat.mediaControl('stop')">⏹</button>
    <template v-if="seekable">
      <span class="time">{{ clock(media.position.value) }}</span>
      <input class="seek" type="range" min="0" :max="media.duration.value" step="1" :value="media.position.value" @change="seek" />
      <span class="time">{{ clock(media.duration.value) }}</span>
    </template>
    <label class="volume" :title="t('audio_volume')">
      🔊
      <input type="range" min="0" max="1" step="0.05" :value="media.volume.value" @input="media.volume.value = Number(($event.target as HTMLInputElement).value)" />
    </label>
  </div>
</template>

<style scoped>
.player { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--aifred-border); font-size: 0.85rem; min-width: 0; }
.what { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; max-width: 30%; }
button { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; padding: 0 8px; font: inherit; }
.time { color: var(--aifred-text-muted); font-variant-numeric: tabular-nums; }
.seek { flex: 1; min-width: 60px; accent-color: var(--aifred-primary); }
.volume { display: flex; align-items: center; gap: 4px; }
.volume input { width: 80px; accent-color: var(--aifred-primary); }
</style>
