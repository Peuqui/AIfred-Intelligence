<script setup lang="ts">
// The switch of the agents page (orange track when on), as the Reflex page's rx.switch: one look wherever something is
// switched on or off as a whole (an agent, a group of tools).
defineProps<{ on: boolean; disabled?: boolean; title?: string }>();
const emit = defineEmits<{ toggle: [on: boolean] }>();
</script>

<template>
  <label class="switch" :title="title">
    <input type="checkbox" :checked="on" :disabled="disabled" @change="emit('toggle', !on)" />
    <span class="track"></span>
  </label>
</template>

<style scoped>
.switch { position: relative; display: inline-block; width: 40px; height: 22px; flex: none; cursor: pointer; vertical-align: middle; }
.switch input { opacity: 0; width: 0; height: 0; }
.track { position: absolute; inset: 0; background: var(--aifred-border); border-radius: 11px; transition: background 0.2s; }
.track::before { content: ""; position: absolute; width: 16px; height: 16px; left: 3px; top: 3px; background: var(--aifred-text-primary); border-radius: 50%; transition: transform 0.2s; }
.switch input:checked + .track { background: var(--aifred-primary); }
.switch input:checked + .track::before { transform: translateX(18px); }
.switch input:disabled + .track { opacity: 0.5; cursor: default; }
</style>
