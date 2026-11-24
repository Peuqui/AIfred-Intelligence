<script setup lang="ts">
// Somebody asks for or holds this machine's cards: a small amber pill in the chat's title row, as in Agent-Orc. Owner and
// state word visible, the dot pulses while a request waits for a decision; a tap opens the band beneath (GpuHoldBanner).
import { computed } from "vue";
import { t } from "../i18n";
import { useServiceStatus } from "../stores/serviceStatus";

const status = useServiceStatus();
const hold = computed(() => (status.localHoldView?.card && !status.holdOpen ? status.localHold : null));
const shortKey = computed(() => {
  const state = hold.value?.state;
  return state === "active" && hold.value?.end_requested_by ? "gpu_hold_short_ending" : `gpu_hold_short_${state}`;
});
</script>

<template>
  <button v-if="hold" class="pill" :aria-label="status.localHoldView?.title" @click="status.setHoldOpen(true)">
    <span class="dot" :class="{ pending: status.localHoldView?.pending }" />
    <span class="owner">{{ hold.owner }}</span>
    <span class="state">{{ t(shortKey) }}</span>
  </button>
</template>

<style scoped>
.pill { display: inline-flex; flex-shrink: 0; align-items: center; gap: 6px; padding: 2px 8px; border: none; border-radius: 999px; background: var(--aifred-accent-amber); color: var(--aifred-page-bg); font-size: 0.75rem; font-weight: 600; }
.dot { width: 8px; height: 8px; flex-shrink: 0; border-radius: 50%; background: var(--aifred-page-bg); }
.dot.pending { animation: pulse 1.2s ease-in-out infinite; }
.owner { max-width: 8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state { font-weight: 400; }
@keyframes pulse { 50% { opacity: 0.3; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none !important; } }
</style>
