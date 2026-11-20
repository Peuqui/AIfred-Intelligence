<script setup lang="ts">
// A band above the chat while somebody asks for this machine's cards and nobody has decided yet (state requested). The
// data is the status line's (stores/serviceStatus.ts), the buttons its actions with the same question before ending.
import { Hand, X } from "lucide-vue-next";
import { computed } from "vue";
import { t } from "../i18n";
import { useServiceStatus } from "../stores/serviceStatus";

const status = useServiceStatus();
const request = computed(() => (status.localHold?.state === "requested" ? status.localHold : null));
</script>

<template>
  <div v-if="request" class="band" role="status">
    <span class="text">{{ t("gpu_hold_banner", { owner: request.owner ?? "", reason: request.reason ?? "" }) }}</span>
    <span class="buttons">
      <button class="primary" :disabled="status.acting" @click="status.act({ verb: 'grant', service: 'host', item: request.owner ?? '' })">
        <Hand :size="14" />{{ t("service_status_local_grant") }}
      </button>
      <button :disabled="status.acting" @click="status.endHold(request.owner ?? '', request.reason ?? '')">
        <X :size="14" />{{ t("service_status_local_end") }}
      </button>
    </span>
  </div>
</template>

<style scoped>
.band { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 6px 10px; border: 1px solid var(--aifred-accent-amber); border-radius: 8px; background: var(--aifred-warning-bg); color: var(--aifred-text-primary); font-size: 0.85rem; }
.text { flex: 1 1 14rem; min-width: 0; overflow-wrap: anywhere; }
.buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.buttons button { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; padding: 3px 8px; }
</style>
