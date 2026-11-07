<script setup lang="ts">
// Agent bundles on the agents page (lib/agent_bundle.py): export the agents chosen as one ZIP (configuration,
// prompts, sampling, voices), or import agents from one — first what the bundle holds and which exist here
// already, then the choice and what a name already taken does (rename, overwrite, abort).
import { computed, ref } from "vue";
import { ApiError, api, apiUrl } from "../api/client";
import type { AgentCard, BundleImported, BundlePeek } from "../api/types";
import { t } from "../i18n";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ mode: "export" | "import"; cards: AgentCard[] }>();
const emit = defineEmits<{ close: []; imported: [] }>();

// ── export ──
const chosen = ref<string[]>([]);
const exportUrl = computed(() => apiUrl(`agents/export?ids=${chosen.value.map(encodeURIComponent).join(",")}`));

// ── import ──
const file = ref<File | null>(null);
const peek = ref<BundlePeek | null>(null);
const selected = ref<string[]>([]);
const conflict = ref<"rename" | "overwrite" | "abort">("rename");
const notice = ref("");
const result = ref<BundleImported | null>(null);

async function picked(event: Event): Promise<void> {
  const files = (event.target as HTMLInputElement).files;
  file.value = files?.[0] ?? null;
  peek.value = null;
  result.value = null;
  notice.value = "";
  if (!file.value) return;
  const form = new FormData();
  form.append("file", file.value, file.value.name);
  try {
    peek.value = await api<BundlePeek>("agents/import/peek", { method: "POST", body: form });
    selected.value = peek.value.agents.map((agent) => agent.agent_id);
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}

async function runImport(): Promise<void> {
  if (!file.value) return;
  const form = new FormData();
  form.append("file", file.value, file.value.name);
  form.append("ids", selected.value.join(","));
  form.append("conflict", conflict.value);
  notice.value = "";
  try {
    result.value = await api<BundleImported>("agents/import", { method: "POST", body: form });
    emit("imported");
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="bundles">
      <template v-if="mode === 'export'">
        <strong>📦 {{ t("bundle_export_title") }}</strong>
        <p class="hint">{{ t("bundle_export_desc") }}</p>
        <label v-for="card in props.cards" :key="card.id" class="choice">
          <input v-model="chosen" type="checkbox" :value="card.id" /> {{ card.emoji }} {{ card.display_name }}
        </label>
        <p v-if="!chosen.length" class="hint">{{ t("bundle_export_empty") }}</p>
        <div class="actions">
          <button @click="emit('close')">{{ t("bundle_cancel") }}</button>
          <a v-if="chosen.length" class="button" :href="exportUrl" download>{{ t("bundle_export_btn") }}</a>
        </div>
      </template>

      <template v-else>
        <strong>📥 {{ t("bundle_import_title") }}</strong>
        <label class="file">{{ t("bundle_import_drop") }}<input type="file" accept=".zip" @change="picked" /></label>
        <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
        <template v-if="peek && !result">
          <p class="hint">{{ t("bundle_import_desc") }}</p>
          <label v-for="agent in peek.agents" :key="agent.agent_id" class="choice">
            <input v-model="selected" type="checkbox" :value="agent.agent_id" />
            <span>{{ agent.display_name }} <code>{{ agent.agent_id }}</code></span>
            <span v-if="agent.exists_locally" class="exists">{{ t("bundle_exists_locally") }}</span>
          </label>
          <fieldset v-if="peek.agents.some((agent) => agent.exists_locally)">
            <legend>{{ t("bundle_conflict_label") }}</legend>
            <label><input v-model="conflict" type="radio" value="rename" /> {{ t("bundle_conflict_rename") }}</label>
            <label><input v-model="conflict" type="radio" value="overwrite" /> {{ t("bundle_conflict_overwrite") }}</label>
            <label><input v-model="conflict" type="radio" value="abort" /> {{ t("bundle_conflict_abort") }}</label>
          </fieldset>
          <div class="actions">
            <button @click="emit('close')">{{ t("bundle_cancel") }}</button>
            <button :disabled="!selected.length" @click="runImport">{{ t("bundle_import_btn") }}</button>
          </div>
        </template>
        <template v-if="result">
          <p class="done">✅ {{ result.agent_ids.join(", ") }}</p>
          <p v-for="warning in result.warnings" :key="warning" class="notice">⚠️ {{ warning }}</p>
          <div class="actions"><button @click="emit('close')">{{ t("dialog_close") }}</button></div>
        </template>
      </template>
    </div>
  </ModalDialog>
</template>

<style scoped>
.bundles { width: min(560px, 86vw); display: flex; flex-direction: column; gap: 8px; }
.hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.choice { display: flex; gap: 8px; align-items: center; }
.exists { font-size: 0.75rem; color: var(--aifred-accent-amber); }
.file { display: flex; flex-direction: column; gap: 6px; font-size: 0.85rem; color: var(--aifred-text-secondary); }
fieldset { border: 1px solid var(--aifred-border); border-radius: 8px; display: flex; gap: 12px; flex-wrap: wrap; }
.actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 6px; }
button, .button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; cursor: pointer; text-decoration: none; }
button:disabled { opacity: 0.5; cursor: default; }
input[type="checkbox"], input[type="radio"] { accent-color: var(--aifred-primary); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.done { margin: 0; color: var(--aifred-accent-cyan); }
</style>
