<script setup lang="ts">
// The eye that arms Vigilantia's background watch or disarms it, as the Reflex page drew it (a small Radix icon button):
// 24 px square, a 14 px eye; armed solid in the primary colour, else a soft grey tint without a rim. One for the chat's
// card and the vision settings' mode line.
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
  border-radius: 4px; border: none; cursor: pointer;
  background: color-mix(in srgb, var(--aifred-text-muted) 18%, transparent); color: var(--aifred-text-muted);
}
.eye.armed { background: var(--aifred-primary); color: var(--aifred-text-on-accent); }
.eye:disabled { opacity: 0.6; cursor: default; }
</style>
