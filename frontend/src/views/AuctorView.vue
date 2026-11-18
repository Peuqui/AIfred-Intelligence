<script setup lang="ts">
// The Auctor page: whose AIfred this is (the author's notice and the licence come from the LICENSE file, read by the
// server), where the name comes from (the header's subtitle), the running version and the public repository.
import { ExternalLink } from "lucide-vue-next";
import { onMounted, ref } from "vue";
import { api } from "../api/client";
import type { About } from "../api/types";
import { t } from "../i18n";

const about = ref<About | null>(null);
// The subtitle of the header, split at its "•" as there: "AI at your service", then where the name comes from.
const nameOrigin = t("subtitle").split(" • ").slice(1).join(" • ");

onMounted(async () => {
  about.value = await api<About>("about");
});
</script>

<template>
  <main v-if="about" class="auctor">
    <h2>AIfred Intelligence</h2>
    <p>{{ about.author_notice }}</p>
    <p class="hint">{{ nameOrigin }}</p>
    <p>
      <a :href="about.license_url" target="_blank" rel="noopener">{{ about.license_name }} <ExternalLink :size="12" /></a>
    </p>
    <p class="hint">{{ t("auctor_commercial") }}</p>
    <p v-if="about.version_commit">
      {{ t("auctor_version") }}: <span class="mono">{{ about.version_commit }}</span>
      ({{ new Date(about.version_date ?? "").toLocaleString() }})
    </p>
    <p>
      <a :href="about.repo_url" target="_blank" rel="noopener">{{ t("auctor_repo") }} <ExternalLink :size="12" /></a>
    </p>
  </main>
</template>

<style scoped>
.auctor { overflow-y: auto; padding: 16px 0; display: flex; flex-direction: column; gap: 4px; }
h2 { margin: 0; }
p { margin: 4px 0; }
.hint { font-size: 0.85rem; color: var(--aifred-text-muted); }
.mono { font-family: monospace; }
a { color: var(--aifred-accent-blue); }
</style>
