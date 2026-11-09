<script setup lang="ts">
// The speaker symbol of the whole interface: the TTS icon (blue, as in the status line). `playing`: while it is read,
// the bars rise and fall like a level and the arcs glow. The same component and animation as Agent-Orc's
// SpeakerIcon.vue (a copy: the two projects are separate repositories). The file is placed in the page as it is
// (its own file, a local asset), so its bars can be moved one by one.
import iconSvg from "../assets/status-icons/tts-suite-icon.svg?raw";

defineProps<{ playing?: boolean }>();
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- the app's own asset, never user text -->
  <span class="tts-icon" :class="{ playing }" aria-hidden="true" v-html="iconSvg" />
</template>

<style scoped>
.tts-icon { display: inline-block; height: 1.4em; width: 1.4em; vertical-align: text-bottom; }
.tts-icon :deep(svg) { width: 100%; height: 100%; display: block; }
.playing :deep(line) { transform-box: fill-box; transform-origin: center; animation: level 0.9s ease-in-out infinite; }
.playing :deep(line:nth-child(2)) { animation-delay: -0.6s; }
.playing :deep(line:nth-child(3)) { animation-delay: -0.3s; }
.playing :deep(line:nth-child(4)) { animation-delay: -0.75s; }
.playing :deep(line:nth-child(5)) { animation-delay: -0.45s; }
.playing :deep(path) { animation: glow 1.2s ease-in-out infinite; }
.playing :deep(path:last-child) { animation-delay: -0.6s; }
@media (prefers-reduced-motion: reduce) {
  .playing :deep(line), .playing :deep(path) { animation: none; }
}
@keyframes level { 0%, 100% { transform: scaleY(0.45); } 50% { transform: scaleY(1); } }
@keyframes glow { 0%, 100% { opacity: 0.35; } 50% { opacity: 1; } }
</style>
