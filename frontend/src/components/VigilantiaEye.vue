<script setup lang="ts">
// The eye that arms Vigilantia's background watch or disarms it, exactly as the Reflex page drew it (Radix icon_button
// size 1, vigilantia_feed.py from d04db764 on): 24 px square, radius 3 px, a 14 px eye; armed variant solid in Radix
// orange (white eye), off variant soft gray (a faint tint, the crossed-out eye in light grey). One for the chat's card
// and the vision settings' mode line.
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
  display: inline-flex; align-items: center; justify-content: center; flex: none; width: 24px; height: 24px; padding: 0;
  border-radius: 3px; border: none; cursor: pointer;
  background: var(--aifred-radix-soft-gray-bg); color: var(--aifred-radix-soft-gray-text);
}
.eye.armed { background: var(--aifred-radix-solid-orange); color: var(--aifred-text-on-accent); }
.eye:disabled { opacity: 0.6; cursor: default; }
</style>
