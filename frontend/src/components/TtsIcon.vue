<script setup lang="ts">
// The speaker symbol of the whole interface: the TTS icon (blue), as in the status line.
import ttsIcon from "../assets/status-icons/tts-suite-icon.svg";
</script>

<template>
  <img class="tts-icon" :src="ttsIcon" alt="" />
</template>

<style scoped>
.tts-icon { height: 1.4em; width: 1.4em; vertical-align: text-bottom; filter: brightness(1.5); }
</style>
