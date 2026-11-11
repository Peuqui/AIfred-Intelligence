<script setup lang="ts">
// The maintenance page: the local files nothing deletes by itself (chat exports and sandbox output: open, take an
// export into the GitHub showcase, delete one, several or all after asking) and the audit log of the tools' last
// runs. Both load when their section is opened.
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { AuditEntry, ManagedFile } from "../api/types";
import { formatNumber, t } from "../i18n";

const files = ref<ManagedFile[] | null>(null);
const audit = ref<AuditEntry[] | null>(null);
const chosen = ref<string[]>([]);
const confirming = ref("");          // a file id, or "*" for the chosen ones
const notice = ref("");

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
async function loadFiles(): Promise<void> {
  files.value = await call(() => api<ManagedFile[]>("storage/files"));
  chosen.value = [];
  confirming.value = "";
}
async function remove(ids: string[]): Promise<void> {
  files.value = (await call(() => api<ManagedFile[]>("storage/files/delete", { method: "POST", body: JSON.stringify({ ids }) }))) ?? files.value;
  chosen.value = [];
  confirming.value = "";
}
async function showcase(file: ManagedFile): Promise<void> {
  const copied = await call(() => api<{ name: string }>("storage/showcase", { method: "POST", body: JSON.stringify({ id: file.id }) }));
  if (copied) notice.value = t("storage_copied_to_showcase", { name: copied.name });
}
function toggle(id: string): void {
  chosen.value = chosen.value.includes(id) ? chosen.value.filter((other) => other !== id) : [...chosen.value, id];
}
const opened = (event: Event): boolean => (event.target as HTMLDetailsElement).open;
async function openFiles(event: Event): Promise<void> {
  if (opened(event)) await loadFiles();
}
async function openAudit(event: Event): Promise<void> {
  if (opened(event)) audit.value = await call(() => api<AuditEntry[]>("audit"));
}
</script>

<template>
  <main class="maintenance">
    <p v-if="notice" class="notice">{{ notice }}</p>
    <details @toggle="openFiles">
      <summary>💾 {{ t("tab_storage") }}</summary>
      <p class="hint">{{ t("storage_intro") }}</p>
      <div class="bar">
        <button @click="loadFiles">🔄 {{ t("storage_refresh") }}</button>
        <button v-if="files?.length" @click="chosen = files.map((file) => file.id)">{{ t("select_all") }}</button>
        <button v-if="chosen.length" @click="chosen = []">{{ t("clear_selection") }}</button>
        <template v-if="chosen.length">
          <button v-if="confirming !== '*'" class="danger" @click="confirming = '*'">{{ t("delete_selected") }} ({{ chosen.length }})</button>
          <template v-else>
            <span>{{ t("storage_delete_confirm") }}</span>
            <button class="danger" @click="remove(chosen)">{{ t("storage_delete") }}</button>
            <button @click="confirming = ''">{{ t("db_cancel") }}</button>
          </template>
        </template>
      </div>
      <p v-if="files && !files.length" class="hint">{{ t("storage_empty") }}</p>
      <div v-for="file in files ?? []" :key="file.id" class="file">
        <input type="checkbox" :checked="chosen.includes(file.id)" @change="toggle(file.id)" />
        <span class="kind">{{ t(`storage_kind_${file.kind}`) }}</span>
        <a class="name" :href="file.url" target="_blank" rel="noopener" :title="t('storage_open')">{{ file.name }}</a>
        <span class="meta">{{ formatNumber(file.size_kb) }} KB · {{ file.mtime }}</span>
        <button v-if="file.is_html && file.kind === 'export'" @click="showcase(file)">📺 {{ t("storage_to_showcase") }}</button>
        <button v-if="confirming !== file.id" class="danger" @click="confirming = file.id">🗑️</button>
        <template v-else>
          <span>{{ t("storage_delete_confirm") }}</span>
          <button class="danger" @click="remove([file.id])">{{ t("storage_delete") }}</button>
          <button @click="confirming = ''">{{ t("db_cancel") }}</button>
        </template>
      </div>
    </details>

    <details @toggle="openAudit">
      <summary>🛡️ {{ t("tab_audit") }}</summary>
      <p class="hint">{{ t("audit_log_subtitle") }}</p>
      <table v-if="audit?.length">
        <thead>
          <tr>
            <th>{{ t("audit_col_time") }}</th>
            <th>{{ t("audit_col_agent") }}</th>
            <th>{{ t("audit_col_session") }}</th>
            <th>{{ t("audit_col_source") }}</th>
            <th>{{ t("audit_col_tool") }}</th>
            <th>{{ t("audit_col_tier") }}</th>
            <th>{{ t("audit_col_status") }}</th>
            <th>{{ t("audit_col_duration") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(entry, index) in audit" :key="index">
            <td>{{ entry.timestamp }}</td>
            <td>{{ entry.agent_id }}</td>
            <td class="mono" :title="entry.session_id">{{ entry.session_short }}</td>
            <td>{{ entry.source }}</td>
            <td class="tool" :title="entry.args">{{ entry.tool_name }}</td>
            <td>{{ entry.tool_tier }}</td>
            <td :class="entry.success ? 'ok' : 'failed'">{{ entry.success ? "OK" : "FAIL" }}</td>
            <td>{{ entry.duration }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </main>
</template>

<style scoped>
.maintenance { overflow-y: auto; padding: 16px 0; display: flex; flex-direction: column; gap: 8px; max-width: 720px; }
details { padding: 10px 14px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
summary { cursor: pointer; font-weight: 600; }
.hint { margin: 6px 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.bar, .file { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 0.85rem; }
.file { padding: 3px 0; border-top: 1px solid var(--aifred-border); }
.kind { min-width: 70px; font-size: 0.75rem; color: var(--aifred-text-secondary); }
.name { flex: 1; color: var(--aifred-accent-blue); word-break: break-all; }
.meta { font-size: 0.75rem; color: var(--aifred-text-muted); }
button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 8px; font: inherit; font-size: 0.8rem; cursor: pointer; }
button.danger { border-color: var(--aifred-danger); color: var(--aifred-danger); }
table { border-collapse: collapse; font-size: 0.75rem; width: 100%; }
th { text-align: left; color: var(--aifred-text-secondary); font-weight: 600; padding: 3px 6px; }
td { padding: 3px 6px; border-top: 1px solid var(--aifred-border); }
.mono { font-family: monospace; }
.tool { font-weight: 500; }
.ok { color: var(--aifred-accent-success); }
.failed { color: var(--aifred-danger); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
