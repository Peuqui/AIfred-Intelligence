<script setup lang="ts">
// The reading aloud in this page (lib/speech.ts), visible while it speaks or is held: hold/go on, stop (the reading
// only, the turn goes on) and how loud. Volume is this device's own, like the audio player's.
import { t } from "../i18n";
import * as speech from "../lib/speech";
</script>

<template>
  <div v-if="speech.speaking.value" class="player">
    <span class="what">🔊 {{ t("speech_player_title") }}</span>
    <button :title="speech.paused.value ? t('audio_resume') : t('audio_pause')" @click="speech.togglePause()">
      {{ speech.paused.value ? "▶" : "⏸" }}
    </button>
    <button :title="t('audio_stop')" @click="speech.stop()">⏹</button>
    <label class="volume" :title="t('audio_volume')">
      🔉
      <input type="range" min="0" max="1" step="0.05" :value="speech.volume.value" @input="speech.volume.value = Number(($event.target as HTMLInputElement).value)" />
    </label>
  </div>
</template>

<style scoped>
.player { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--aifred-border); font-size: 0.85rem; min-width: 0; }
.what { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
button { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; padding: 0 8px; font: inherit; }
.volume { display: flex; align-items: center; gap: 4px; }
.volume input { width: 80px; accent-color: var(--aifred-primary); }
</style>
