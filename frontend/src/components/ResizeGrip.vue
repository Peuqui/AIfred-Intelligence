<script setup lang="ts">
// A bar under a box that drags its height (vertical only). Wide and in the middle, so a finger hits it: the browser's own
// grip at the corner is a few pixels, sits next to the buttons and at the screen's edge, where the back gesture lies.
// It reports the height in the box's own units (a zoom set on the page or on the box is taken out again); the box sets it.
// maxScreens: the most the box may take, in screens high (the chat box must stay one whose grip and top can be reached).
// A quick second tap on the bar sets the box back to its default height (``reset``).
const props = defineProps<{ target: HTMLElement | null; minHeight?: number; maxScreens?: number }>();
const emit = defineEmits<{ resize: [height: number]; reset: [] }>();

const DOUBLE_TAP_MS = 400;
const TAP_SLOP_PX = 6;
const EDGE_PX = 64; // a finger this close to the screen's top or bottom edge takes the page along
const EDGE_STEP_MAX_PX = 8; // per frame, at the very edge; it starts at 1 where the edge zone begins
let grab = 0; // how far the finger stands below the box's lower edge, in screen pixels
let scale = 1; // screen pixels per own unit
let fingerY = 0;
let pressedAt = { x: 0, y: 0 };
let lastTapAt = -Infinity;
let frame = 0;

// The box's height from where its lower edge should be (above the finger, by the distance it was grabbed at). The target
// boxes are border-box, so the height reported is the one the box takes.
function follow(): boolean {
  const box = props.target;
  if (!box) return false;
  const most = props.maxScreens ? (props.maxScreens * window.innerHeight) / scale : Infinity;
  const height = (fingerY - grab - box.getBoundingClientRect().top) / scale;
  emit("resize", Math.min(most, Math.max(props.minHeight ?? 0, height)));
  return height >= most;
}

// While the finger holds at an edge the page scrolls on by itself (the finger does not move, so no event comes), and the
// box follows: a box can grow beyond the screen. Only here the page is scrolled, never to make up for the box's size.
// The deeper the finger in the edge zone, the faster, from 1 to EDGE_STEP_MAX_PX per frame. Upwards only while the box's
// top lies above the screen: the box shrinks by every pixel the page moves, and with its top in view the finger alone
// reaches every height (12 px a frame from the first pixel of the zone had shrunk it to its minimum at once).
function edgeScroll(): void {
  frame = requestAnimationFrame(edgeScroll);
  const box = props.target;
  if (!box) return;
  const depth = fingerY > window.innerHeight - EDGE_PX ? fingerY - (window.innerHeight - EDGE_PX)
    : fingerY < EDGE_PX ? fingerY - EDGE_PX : 0;
  if (!depth) return;
  if (depth < 0 && box.getBoundingClientRect().top >= 0) return;
  if (depth > 0 && follow()) return; // the box is as high as it may be: the page stays
  const step = Math.sign(depth) * Math.max(1, Math.round((EDGE_STEP_MAX_PX * Math.abs(depth)) / EDGE_PX));
  window.scrollBy(0, step);
  follow();
}

function start(event: PointerEvent): void {
  const box = props.target;
  if (!box) return;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  grab = event.clientY - box.getBoundingClientRect().bottom;
  scale = box.getBoundingClientRect().height / box.offsetHeight || 1;
  fingerY = event.clientY;
  pressedAt = { x: event.clientX, y: event.clientY };
  // The page is scrolled by this drag alone: the browser's own scroll anchoring would move it against the drag.
  document.documentElement.style.overflowAnchor = "none";
  frame = requestAnimationFrame(edgeScroll);
}

function end(event: PointerEvent): void {
  cancelAnimationFrame(frame);
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

function move(event: PointerEvent): void {
  if (!(event.currentTarget as HTMLElement).hasPointerCapture(event.pointerId)) return;
  fingerY = event.clientY;
  follow();
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
/* Lit while held; the hover only where there is a mouse (on a touch screen it stays on the last place touched). */
.grip:active span { background: var(--aifred-primary); }
@media (hover: hover) { .grip:hover span { background: var(--aifred-primary); } }
</style>
