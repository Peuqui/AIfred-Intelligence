<script setup lang="ts">
// The eye that arms Vigilantia's background watch or disarms it: a bare 14 px eye without a box (Peuqui 13.11.2026),
// armed open in Radix orange (the Reflex page's colour), off crossed out in the muted orange of switched-off icons
// (assets/controls.css, Peuqui 14.11.2026: not grey). One for the chat's card and the vision settings' mode line.
import { Eye, EyeOff } from "lucide-vue-next";

defineProps<{ armed: boolean; disabled?: boolean; title: string }>();
defineEmits<{ toggle: [] }>();
</script>

<template>
  <button class="eye" :class="{ armed }" :disabled="disabled" :aria-pressed="armed" :title="title" @click="$emit('toggle')">
    <Eye v-if="armed" :size="14" /><EyeOff v-else :size="14" />
  </button>
</template>

<style scoped>
.eye {
  display: inline-flex; align-items: center; justify-content: center; flex: none; padding: 0;
  background: none; border: none; cursor: pointer; color: var(--aifred-radix-soft-gray-text);
  transition: transform 0.2s ease;
}
.eye:hover:not(:disabled) { transform: scale(1.15); }
.eye.armed { color: var(--aifred-radix-solid-orange); --icon-own: currentColor; }
.eye:disabled { opacity: 0.6; cursor: default; }
</style>
