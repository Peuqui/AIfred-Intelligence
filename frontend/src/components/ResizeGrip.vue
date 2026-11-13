<script setup lang="ts">
// A bar under a box that drags its height (vertical only). Wide and in the middle, so a finger hits it: the browser's own
// grip at the corner is a few pixels, sits next to the buttons and at the screen's edge, where the back gesture lies.
// It reports the height in the box's own units (a zoom set on the page or on the box is taken out again); the box sets it.
const props = defineProps<{ target: HTMLElement | null; minHeight?: number }>();
const emit = defineEmits<{ resize: [height: number] }>();

let startY = 0;
let startHeight = 0;
let scale = 1; // screen pixels per own unit

function start(event: PointerEvent): void {
  const box = props.target;
  if (!box) return;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  startY = event.clientY;
  startHeight = box.offsetHeight;
  scale = box.getBoundingClientRect().height / box.offsetHeight || 1;
}

function move(event: PointerEvent): void {
  if (!(event.currentTarget as HTMLElement).hasPointerCapture(event.pointerId)) return;
  emit("resize", Math.max(props.minHeight ?? 0, startHeight + (event.clientY - startY) / scale));
}
</script>

<template>
  <div class="grip" role="separator" aria-orientation="horizontal" @pointerdown.prevent="start" @pointermove="move">
    <span />
  </div>
</template>

<style scoped>
/* Hardly more than the pill: the gap the parent puts between its children (--grip-gap) is taken back, so the bar costs its
   own 14 px and nothing around it. Only the pill itself takes the touch (4 px beyond it above and below), not the
   row to its left and right: a swipe there belongs to the page. */
.grip {
  display: flex; align-items: center; justify-content: center; height: 14px; pointer-events: none;
  margin-block: calc(-1 * var(--grip-gap, 0px));
}
.grip span {
  position: relative; pointer-events: auto; width: 56px; height: 5px; border-radius: 3px; background: var(--aifred-border);
  cursor: ns-resize; touch-action: none; user-select: none;
}
.grip span::before { content: ""; position: absolute; inset: -4px 0; }
.grip:hover span, .grip:active span { background: var(--aifred-primary); }
</style>
