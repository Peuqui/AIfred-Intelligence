<script setup lang="ts">
// The cards of this machine and what the installed backends can run on them (maintenance).
import { Cpu, Lightbulb, Monitor, TriangleAlert } from "lucide-vue-next";
import { computed, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { BackendOverview, GpuCard } from "../api/types";
import { formatNumber, t } from "../i18n";

const overview = ref<BackendOverview | null>(null);
const notice = ref("");

async function opened(event: Event): Promise<void> {
  if (!(event.target as HTMLDetailsElement).open) return;
  notice.value = "";
  try {
    overview.value = await api<BackendOverview>("backend");
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}

// One line per kind of card, in the order the server detected them (lowest compute capability first).
const cardKinds = computed(() => {
  const kinds = new Map<string, { card: GpuCard; count: number }>();
  for (const card of overview.value?.gpu?.cards ?? []) {
    const kind = kinds.get(card.name);
    if (kind) kind.count += 1;
    else kinds.set(card.name, { card, count: 1 });
  }
  return [...kinds.values()];
});
</script>

<template>
  <details @toggle="opened">
    <summary><Monitor :size="16" /> {{ t("gpu_details") }}</summary>
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <template v-if="overview?.gpu">
      <p v-for="kind in cardKinds" :key="kind.card.name" class="card">
        <Cpu :size="16" /> {{ t("gpu_card_line", { count: kind.count, name: kind.card.name, vram: formatNumber(kind.card.vram_mb / 1024), compute: formatNumber(kind.card.compute_capability, 1) }) }}
      </p>
      <p class="card">{{ t("gpu_total", { vram: formatNumber(overview.gpu.total_vram_gb) }) }}</p>
      <template v-if="overview.gpu.unsupported_backends.includes('vllm')">
        <p class="hint">{{ t("gpu_vllm_requires_compute") }}</p>
        <p class="hint"><Lightbulb :size="16" /> {{ t("gpu_gguf_hint") }}</p>
      </template>
    </template>
  </details>
</template>

<style scoped>
p { margin: 6px 0 0; }
.card { font-size: 0.85rem; color: var(--aifred-accent-cyan); --icon-own: currentColor; }
.hint { color: var(--aifred-text-secondary); font-size: 0.85rem; }
.notice { color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
