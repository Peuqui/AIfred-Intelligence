<script setup lang="ts">
// A plugin's settings form (GET/PUT plugins/{name}/form): the fields in the plugin's own words, grouped in rows, a
// password only as "stored" (an empty field keeps it), and for an OAuth plugin the connection: the provider's
// sign-in page opens in a new tab, the form then asks every few seconds whether the connection has come.
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { PluginForm } from "../api/types";
import { t } from "../i18n";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ plugin: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();

const OAUTH_POLL_MS = 5000;
const OAUTH_POLL_TIMES = 60;   // five minutes: a sign-in with consent can take a while

const form = ref<PluginForm | null>(null);
const values = reactive<Record<string, string>>({});
const notice = ref("");
const busy = ref(false);
const link = ref("");
const connecting = ref(false);
let poll: number | undefined;

const rows = computed(() => {
  const grouped: PluginForm["fields"][] = [];
  for (const field of form.value?.fields ?? []) {
    const last = grouped.at(-1);
    if (field.group && last?.[0]?.group === field.group) last.push(field);
    else grouped.push([field]);
  }
  return grouped;
});

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}

async function load(described: PluginForm | null): Promise<void> {
  if (!described) return;
  form.value = described;
  for (const field of described.fields) values[field.key] = field.value;
  link.value = "";
  if (described.oauth_provider && !described.connected) {
    const offered = await call(() => api<{ url: string }>(`plugins/${props.plugin}/oauth-link?origin=${encodeURIComponent(window.location.origin)}`));
    link.value = offered?.url ?? "";
  }
}
onMounted(async () => load(await call(() => api<PluginForm>(`plugins/${props.plugin}/form`))));
onBeforeUnmount(() => window.clearInterval(poll));

async function save(): Promise<void> {
  busy.value = true;
  const saved = await call(() => api<PluginForm>(`plugins/${props.plugin}/form`, { method: "PUT", body: JSON.stringify({ values }) }));
  busy.value = false;
  if (!saved) return;
  emit("saved");
  emit("close");
}

// The tab with the provider's page is the user's own click (no pop-up blocker); here only the waiting.
function waitForConnection(): void {
  connecting.value = true;
  let asked = 0;
  window.clearInterval(poll);
  poll = window.setInterval(async () => {
    asked++;
    const now = await call(() => api<PluginForm>(`plugins/${props.plugin}/form`));
    if (now?.connected || asked >= OAUTH_POLL_TIMES) {
      window.clearInterval(poll);
      connecting.value = false;
      if (now) await load(now);
      if (!now?.connected) notice.value = t("oauth_connect_error");
    }
  }, OAUTH_POLL_MS);
}
async function disconnect(): Promise<void> {
  await call(() => api(`plugins/${props.plugin}/oauth`, { method: "DELETE" }));
  await load(await call(() => api<PluginForm>(`plugins/${props.plugin}/form`)));
}
</script>

<template>
  <ModalDialog @close="emit('close')">
    <header class="head">
      <strong>{{ form?.title ?? plugin }}</strong>
      <button class="icon" :title="t('dialog_close')" @click="emit('close')">×</button>
    </header>
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
    <template v-if="form">
      <div v-for="(row, index) in rows" :key="index" class="row">
        <label v-for="field in row" :key="field.key" class="field" :style="{ flex: field.width }" :title="field.tooltip">
          <span class="label">{{ field.label }}{{ field.tooltip ? " 💡" : "" }}</span>
          <select v-if="field.options.length" v-model="values[field.key]">
            <option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
          <input
            v-else
            v-model="values[field.key]"
            :type="field.password ? 'password' : 'text'"
            :placeholder="field.password && field.is_set ? t('plugin_password_stored') : field.placeholder"
            autocomplete="off"
          />
        </label>
      </div>
      <div v-if="form.oauth_provider" class="oauth">
        <template v-if="form.connected">
          <span class="ok">{{ t("oauth_connect_connected") }}</span>
          <button @click="disconnect">{{ t("oauth_connect_disconnect") }}</button>
        </template>
        <template v-else>
          <p v-if="form.rights_missing" class="notice">{{ t("oauth_connect_rights_missing") }}</p>
          <a v-if="link" class="connect" :href="link" target="_blank" rel="noopener" @click="waitForConnection">
            {{ t(connecting ? "oauth_connect_connecting" : "oauth_connect_idle") }}
          </a>
          <span v-else class="hint">{{ t("oauth_connect_no_credentials") }}</span>
          <span v-if="link" class="hint">{{ t("oauth_connect_hint") }}</span>
        </template>
      </div>
      <footer>
        <button @click="emit('close')">{{ t("cred_cancel") }}</button>
        <button class="primary" :disabled="busy" @click="save">{{ t("cred_save") }}</button>
      </footer>
    </template>
  </ModalDialog>
</template>

<style scoped>
.head { display: flex; align-items: center; gap: 10px; min-width: min(560px, 88vw); }
.head strong { flex: 1; color: var(--aifred-primary); }
.icon { background: none; border: none; color: inherit; font-size: 1.3rem; cursor: pointer; }
.row { display: flex; gap: 10px; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 3px; min-width: 160px; font-size: 0.85rem; }
.label { color: var(--aifred-text-secondary); }
input, select, button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; font-size: 0.85rem; }
button { cursor: pointer; }
button.primary { border-color: var(--aifred-primary); color: var(--aifred-primary); font-weight: 600; }
button:disabled { opacity: 0.5; cursor: default; }
.oauth { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 0.85rem; }
.connect { color: var(--aifred-primary); font-weight: 600; }
.ok { color: var(--aifred-accent-success); }
.hint { color: var(--aifred-text-muted); font-size: 0.8rem; }
footer { display: flex; justify-content: flex-end; gap: 8px; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
