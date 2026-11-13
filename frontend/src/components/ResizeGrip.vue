<script setup lang="ts">
// A bar under a box that drags its height (vertical only). Wide and in the middle, so a finger hits it: the browser's own
// grip at the corner is a few pixels, sits next to the buttons and at the screen's edge, where the back gesture lies.
// It reports the height in the box's own units (a zoom set on the page or on the box is taken out again); the box sets it.
import { nextTick } from "vue";

// maxScreens: the most the box may take, in screens high (the chat box must stay one whose grip and top can be reached).
// A quick second tap on the bar sets the box back to its default height (``reset``).
const props = defineProps<{ target: HTMLElement | null; minHeight?: number; maxScreens?: number }>();
const emit = defineEmits<{ resize: [height: number]; reset: [] }>();

const DOUBLE_TAP_MS = 400;
const TAP_SLOP_PX = 6;
let grab = 0; // how far the finger stands below the box's lower edge, in screen pixels
let scale = 1; // screen pixels per own unit
let pressedAt = { x: 0, y: 0 };
let lastTapAt = -Infinity;

function start(event: PointerEvent): void {
  const box = props.target;
  if (!box) return;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  grab = event.clientY - box.getBoundingClientRect().bottom;
  scale = box.getBoundingClientRect().height / box.offsetHeight || 1;
  pressedAt = { x: event.clientX, y: event.clientY };
  // The page is scrolled by this drag alone: the browser's own scroll anchoring would move it against the drag.
  document.documentElement.style.overflowAnchor = "none";
}

function end(event: PointerEvent): void {
  document.documentElement.style.overflowAnchor = "";
  const tapped = Math.hypot(event.clientX - pressedAt.x, event.clientY - pressedAt.y) < TAP_SLOP_PX;
  if (!tapped || event.type === "pointercancel") return;
  const now = performance.now();
  if (now - lastTapAt < DOUBLE_TAP_MS) {
    lastTapAt = -Infinity;
    emit("reset");
  } else {
    lastTapAt = now;
  }
}

// The height is read from where the box's edge should be (the finger's place), not from how far the finger moved: the page
// scrolls along, so the box's upper edge moves too. A box that grows below the screen takes the page with it: the grip
// stays under the finger and the drag goes on.
async function move(event: PointerEvent): Promise<void> {
  const box = props.target;
  if (!box || !(event.currentTarget as HTMLElement).hasPointerCapture(event.pointerId)) return;
  const wantedBottom = event.clientY - grab;
  const most = props.maxScreens ? (props.maxScreens * window.innerHeight) / scale : Infinity;
  const height = Math.min(most, Math.max(props.minHeight ?? 0, (wantedBottom - box.getBoundingClientRect().top) / scale));
  emit("resize", height);
  await nextTick();
  window.scrollBy(0, box.getBoundingClientRect().bottom - wantedBottom);
}
</script>

<template>
  <div class="grip" role="separator" aria-orientation="horizontal" @pointerdown.prevent="start" @pointermove="move" @pointerup="end" @pointercancel="end">
    <span />
  </div>
</template>

<style scoped>
/* Hardly more than the pill: the gap the parent puts between its children (--grip-gap) is taken back, so the bar costs its
   own 14 px and nothing around it. Only the pill itself takes the touch (6 px beyond it above and below), not the
   row to its left and right: a swipe there belongs to the page. */
.grip {
  display: flex; align-items: center; justify-content: center; height: 14px; pointer-events: none;
  margin-block: calc(-1 * var(--grip-gap, 0px));
}
.grip span {
  position: relative; pointer-events: auto; width: 56px; height: 5px; border-radius: 3px; background: var(--aifred-border);
  cursor: ns-resize; touch-action: none; user-select: none;
}
.grip span::before { content: ""; position: absolute; inset: -6px 0; }
.grip:hover span, .grip:active span { background: var(--aifred-primary); }
</style>
