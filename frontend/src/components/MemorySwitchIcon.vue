<script setup lang="ts">
// The memory switch of the chat: the notebook while the agents remember this chat, the hat with glasses while it is
// incognito (Peuqui 14.11.2026; a lock said neither). The chat's switch and its help draw it from here, the look of
// both comes from .memory-switch in assets/controls.css.
import { HatGlasses, NotebookPen } from "lucide-vue-next";

defineProps<{ incognito: boolean; size: number }>();
</script>

<template>
  <HatGlasses v-if="incognito" :size="size" /><NotebookPen v-else :size="size" />
</template>
