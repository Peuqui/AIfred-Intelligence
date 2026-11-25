<script setup lang="ts">
// A dialog over the page: the page behind it is dimmed, a click beside the dialog or Escape closes it. A dialog
// opened from another (a help from the quick choices) lies on top; Escape closes only the topmost.
import { onBeforeUnmount, onMounted } from "vue";

// full: the dialog always takes the whole height (less a margin), whatever its content; a file dialog that changed its
// height with every file jumped (Peuqui 25.11.2026). Its content fills it with flex: 1.
defineProps<{ full?: boolean }>();
const emit = defineEmits<{ close: [] }>();

const me = Symbol("dialog");

function key(event: KeyboardEvent): void {
  if (event.key === "Escape" && openDialogs.at(-1) === me) emit("close");
}
onMounted(() => {
  openDialogs.push(me);
  window.addEventListener("keydown", key);
});
onBeforeUnmount(() => {
  openDialogs.splice(openDialogs.indexOf(me), 1);
  window.removeEventListener("keydown", key);
});
</script>

<script lang="ts">
// The dialogs open now, the newest last (shared by every dialog of the page).
const openDialogs: symbol[] = [];
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="dialog" :class="{ full }" role="dialog" aria-modal="true"><slot /></div>
  </div>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; background: var(--aifred-backdrop); display: flex; align-items: center; justify-content: center; z-index: 100; }
/* A dialog grows with its content up to the screen less a margin of 16 px (Peuqui 25.11.2026: use what the screen
   offers); only then does it scroll. */
.dialog {
  display: flex; flex-direction: column; gap: 14px; padding: 20px; box-sizing: border-box;
  max-width: calc(100 * var(--vw) - 32px); max-height: calc(100 * var(--dvh) - 32px); overflow: auto;
  background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light); border-radius: 12px;
}
.dialog.full { height: calc(100 * var(--dvh) - 32px); }
/* A content's own width ("90 % of the screen") plus the dialog's padding ran wider than the dialog on a phone. */
.dialog > * { max-width: 100%; min-width: 0; box-sizing: border-box; }
</style>
