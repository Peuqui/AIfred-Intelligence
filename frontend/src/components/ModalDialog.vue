<script setup lang="ts">
// A dialog over the page: the page behind it is dimmed, a click beside the dialog or Escape closes it.
import { onBeforeUnmount, onMounted } from "vue";

const emit = defineEmits<{ close: [] }>();

function key(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}
onMounted(() => window.addEventListener("keydown", key));
onBeforeUnmount(() => window.removeEventListener("keydown", key));
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="dialog" role="dialog" aria-modal="true"><slot /></div>
  </div>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; background: var(--aifred-backdrop); display: flex; align-items: center; justify-content: center; z-index: 100; }
.dialog {
  display: flex; flex-direction: column; gap: 14px; padding: 20px; box-sizing: border-box;
  max-width: 95vw; max-height: 90dvh; overflow: auto;
  background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light); border-radius: 12px;
}
</style>
