<script setup lang="ts">
// The opened pill (GpuHoldPill): a band above the chat with the hold's state, its detail and the status line's buttons.
// A button that may not act is greyed out with its reason as text beneath (a phone has no hover). "Fold" turns it back
// into the pill; any change of the hold does too.
import { ChevronUp, Hand, X, Zap } from "lucide-vue-next";
import { computed } from "vue";
import { t } from "../i18n";
import { useServiceStatus } from "../stores/serviceStatus";

const status = useServiceStatus();
const view = computed(() => (status.localHoldView?.card && status.holdOpen ? status.localHoldView : null));
</script>

<template>
  <div v-if="view" class="band" role="status">
    <span class="text">
      <span class="title">{{ view.title }}</span>
      <span v-if="view.detail" class="detail">{{ view.detail }}</span>
    </span>
    <span class="buttons">
      <button class="primary" :disabled="status.acting || !!view.grant.blocked" @click="status.act(view.grant)">
        <Hand :size="14" />{{ t("service_status_local_grant") }}
      </button>
      <button :disabled="status.acting || !!view.end.blocked" @click="status.endHold(view.end.item, status.localHold?.reason ?? '')">
        <X :size="14" />{{ t("service_status_local_end") }}
      </button>
      <button :disabled="status.acting || !!view.release.blocked" @click="status.releaseHoldNow(view.release.item)">
        <Zap :size="14" />{{ t("service_status_local_release_now") }}
      </button>
      <button class="fold" @click="status.setHoldOpen(false)"><ChevronUp :size="14" />{{ t("gpu_hold_fold") }}</button>
    </span>
    <span v-if="view.notes.length" class="notes">{{ view.notes.join(" · ") }}</span>
  </div>
</template>

<style scoped>
.band { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 6px 10px; border: 1px solid var(--aifred-accent-amber); border-radius: 8px; background: var(--aifred-warning-bg); color: var(--aifred-text-primary); font-size: 0.85rem; }
.text { display: flex; flex: 1 1 14rem; flex-direction: column; min-width: 0; overflow-wrap: anywhere; }
.title { color: var(--aifred-accent-amber); font-weight: 600; }
.detail, .notes { color: var(--aifred-text-soft); }
.notes { flex-basis: 100%; font-size: 0.8rem; }
.buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.buttons button { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; padding: 3px 8px; }
</style>
