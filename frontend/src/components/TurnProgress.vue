<script setup lang="ts">
// What the turn does while no text comes (lib/turnProgress.ts): a pulsing line, with a bar while pages are read. Waiting
// for the cards also carries the button that ends the hold (greyed out with its reason as text when it may not).
import { t } from "../i18n";
import type { ProgressLine } from "../lib/turnProgress";
import { useServiceStatus } from "../stores/serviceStatus";

defineProps<{ line: ProgressLine }>();
const status = useServiceStatus();

// The reasons the buttons are greyed out, as text (a phone has no hover); one reason shared by both buttons shows once.
function hints(end: NonNullable<ProgressLine["end"]>): string[] {
  return [...new Set([end.action.blocked, end.release.blocked].filter((hint) => hint !== undefined))];
}
</script>

<template>
  <p class="progress">
    <span class="icon">{{ line.icon }}</span>
    <span class="text">{{ line.text }}</span>
    <template v-if="line.count">
      <span class="bar"><span :style="{ width: `${Math.round(line.done * 100)}%` }" /></span>
      <span class="count">{{ line.count }}</span>
    </template>
    <span v-if="line.failed" class="failed">({{ line.failed }})</span>
    <template v-if="line.end">
      <button class="end-hold" :disabled="status.acting || !!line.end.action.blocked" @click="status.endHold(line.end.action.item, line.end.reason)">
        {{ t("service_status_local_end") }}
      </button>
      <button class="release-hold" :disabled="status.acting || !!line.end.release.blocked" @click="status.releaseHoldNow(line.end.release.item)">
        {{ t("service_status_local_release_now") }}
      </button>
      <span v-for="hint in hints(line.end)" :key="hint" class="hint">{{ hint }}</span>
    </template>
  </p>
</template>

<style scoped>
.progress { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 4px 0 0; font-size: 0.85rem; }
.icon { animation: pulse 2s ease-in-out infinite; }
.text { font-weight: 600; color: var(--aifred-primary); animation: pulse 2s ease-in-out infinite; }
.bar { width: 120px; height: 12px; border: 1px solid var(--aifred-border); border-radius: 4px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--aifred-primary); transition: width 0.3s ease; }
.count { font-weight: 600; color: var(--aifred-primary); }
.failed { color: var(--aifred-accent-amber); }
.hint { color: var(--aifred-accent-amber); }
.end-hold:disabled, .release-hold:disabled { opacity: 0.5; cursor: not-allowed; }
@keyframes pulse { 50% { opacity: 0.5; } }
</style>
