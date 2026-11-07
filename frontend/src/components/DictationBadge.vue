<script setup lang="ts">
// The dictation's button, as Agent-Orc draws it (frontend/src/components/DictationBadge.vue there): the
// STT-Suite's logo (assets/status-icons/stt-suite-icon.svg) in a round badge, the bars in amber, a gold rim.
// Ready, it stands still and waits to be pressed; while speech is taken in, its bars rise and fall like a
// level, the arcs glow and the rim turns red (also where motion is reduced, so that ready and recording never
// look alike); while it is transcribed, it stands still and grey. The colours are AIfred's (aifred/theme.py).
export type BadgePhase = "ready" | "listening" | "transcribing";
defineProps<{ phase: BadgePhase }>();
// The logo's own measures: they fit the circle as they are.
const BARS = [
  { x: 96, from: 226, to: 286 },
  { x: 156, from: 176, to: 336 },
  { x: 216, from: 116, to: 396 },
  { x: 276, from: 166, to: 346 },
  { x: 336, from: 206, to: 306 },
];
</script>

<template>
  <svg viewBox="0 0 512 512" :class="phase" aria-hidden="true">
    <defs>
      <linearGradient id="dictationBadgeWave" gradientUnits="userSpaceOnUse" x1="96" y1="116" x2="336" y2="396">
        <stop offset="0" class="wave-from" />
        <stop offset="1" class="wave-to" />
      </linearGradient>
    </defs>
    <circle class="rim" cx="256" cy="256" r="244" stroke-width="24" />
    <g fill="none" stroke="url(#dictationBadgeWave)" stroke-width="30" stroke-linecap="round">
      <line v-for="bar in BARS" :key="bar.x" class="bar" :x1="bar.x" :y1="bar.from" :x2="bar.x" :y2="bar.to" />
    </g>
    <path class="arc near" d="M388 196 q48 60 0 120" fill="none" stroke-width="22" stroke-linecap="round" />
    <path class="arc far" d="M424 156 q84 100 0 200" fill="none" stroke-width="22" stroke-linecap="round" />
  </svg>
</template>

<style scoped>
.wave-from {
  stop-color: var(--aifred-accent-amber);
}
.wave-to {
  stop-color: var(--aifred-primary-active);
}
.rim {
  fill: var(--aifred-page-bg);
  stroke: var(--aifred-accent-gold);
}
.listening .rim {
  stroke: var(--aifred-danger);
}
.near {
  stroke: var(--aifred-text-primary);
}
.far {
  stroke: var(--aifred-text-muted);
}
.transcribing {
  filter: grayscale(1);
  opacity: 0.6;
}
.bar {
  transform-box: fill-box;
  transform-origin: center;
  animation: level 0.9s ease-in-out infinite;
}
.bar:nth-child(2) {
  animation-delay: -0.6s;
}
.bar:nth-child(3) {
  animation-delay: -0.3s;
}
.bar:nth-child(4) {
  animation-delay: -0.75s;
}
.bar:nth-child(5) {
  animation-delay: -0.45s;
}
.arc {
  animation: glow 1.2s ease-in-out infinite;
}
.arc.far {
  animation-delay: -0.6s;
}
.ready .bar,
.ready .arc,
.transcribing .bar,
.transcribing .arc {
  animation: none;
}
@media (prefers-reduced-motion: reduce) {
  .bar,
  .arc {
    animation: none;
  }
}
@keyframes level {
  0%,
  100% {
    transform: scaleY(0.45);
  }
  50% {
    transform: scaleY(1);
  }
}
@keyframes glow {
  0%,
  100% {
    opacity: 0.35;
  }
  50% {
    opacity: 1;
  }
}
</style>
