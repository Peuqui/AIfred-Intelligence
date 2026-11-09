<script setup lang="ts">
// The status line under the top bar (docs/de/architecture/service-status.md): closed, one icon per service with a
// dot (green loaded and breathing, yellow loading and faster, grey nothing loaded, red not reachable); open, a line
// per loaded thing with the time until it unloads and its button (unload, load, hold a machine's card free). The
// refresh button reads STT and TTS anew. The page counts down from the moment the snapshot arrived.
import { Power, RefreshCw } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { language, t } from "../i18n";
import { type Action, type Color, type Row, SERVICES, type Service, formatRemaining, hostRows, serviceRows, summaryColor } from "../lib/serviceStatus";
import { useServiceStatus } from "../stores/serviceStatus";
import HelpButton from "./help/HelpButton.vue";
import hatIcon from "../../../assets/AIfred-Zylinder.png";
import describerIcon from "../assets/status-icons/vision-describer-icon.svg";
import sttIcon from "../assets/status-icons/stt-suite-icon.svg";
import ttsIcon from "../assets/status-icons/tts-suite-icon.svg";

const status = useServiceStatus();
const ICONS: Record<Service | "host", string> = { llm: hatIcon, describer: describerIcon, stt: sttIcon, tts: ttsIcon, host: describerIcon };
const NAMES: Record<Service, string> = {
  llm: "service_status_llm", describer: "service_status_describer", stt: "service_status_stt", tts: "service_status_tts",
};
// Until the first snapshot arrives the bar asks this often (the push brings only changes); then it ticks each second.
const ASK_AGAIN_MS = 5000;
const TICK_MS = 1000;

const labels = computed(() => ({
  lang: language.value,
  noModel: t("service_status_no_model"),
  noneLoaded: t("service_status_none_loaded"),
  loading: t("service_status_loading"),
  busy: t("service_status_busy"),
  unreachable: t("service_status_unreachable"),
  hostHeld: t("service_status_host_held"),
  hostFreeCards: t("service_status_host_free_cards"),
}));
const rows = computed(() => (status.snapshot ? serviceRows(status.snapshot, labels.value) : null));
const machines = computed(() => hostRows(status.hosts, labels.value));
const now = ref(performance.now());
const elapsed = computed(() => (now.value - status.receivedAt) / 1000);

function badgeColor(service: Service): Color {
  return rows.value ? summaryColor(rows.value[service]) : "grey";
}

function timeText(row: Row): string {
  if (row.remaining === null) return "";
  return `${t("service_status_unloads_in")} ${row.upperBound ? "≤ " : ""}${formatRemaining(row.remaining - elapsed.value, t("service_status_days_short"))}`;
}

const ACTION_TITLES: Record<Action["verb"], string> = {
  load: "service_status_load", unload: "service_status_unload", hold: "service_status_host_hold", release: "service_status_host_release",
};

let timer = 0;
let askedAt = -Infinity;
onMounted(() => {
  void status.fetch(false);
  askedAt = performance.now();
  timer = window.setInterval(() => {
    now.value = performance.now();
    if (!status.snapshot && now.value - askedAt > ASK_AGAIN_MS) {
      askedAt = now.value;
      void status.fetch(false);
    }
  }, TICK_MS);
});
onBeforeUnmount(() => window.clearInterval(timer));
</script>

<template>
  <details class="status">
    <summary>
      <span v-for="service in SERVICES" :key="service" class="badge" :class="badgeColor(service)" :title="t(NAMES[service])">
        <img :src="ICONS[service]" alt="" /><span class="dot" />
      </span>
      <button class="refresh" :title="t('service_status_refresh')" @click.prevent="status.fetch(true)"><RefreshCw :size="14" /></button>
    </summary>
    <div v-if="rows" class="list">
      <template v-for="service in SERVICES" :key="service">
        <div v-for="(row, index) in rows[service]" :key="`${service}-${index}`" class="line">
          <span class="badge" :class="row.color"><img :src="ICONS[service]" alt="" /><span class="dot" /></span>
          <span class="label">{{ t(NAMES[service]) }}:</span>
          <span class="value" :title="row.title ?? row.text">{{ row.text }}</span>
          <span class="time">{{ row.remaining !== null ? timeText(row) : "" }}</span>
          <span class="act">
            <button v-if="row.action" class="power" :disabled="status.acting" :title="t(ACTION_TITLES[row.action.verb])" @click="status.act(row.action)">
              <Power :size="14" />
            </button>
          </span>
        </div>
      </template>
      <div v-for="host in machines" :key="host.label" class="line">
        <span class="badge" :class="host.color"><img :src="ICONS.host" alt="" /><span class="dot" /></span>
        <span class="label">{{ host.label }}:</span>
        <span class="value" :title="host.title ?? host.text">{{ host.text }}</span>
        <span class="time" />
        <span class="act">
          <button v-if="host.action" class="power" :disabled="status.acting" :title="t(ACTION_TITLES[host.action.verb])" @click="status.act(host.action)">
            <Power :size="14" />
          </button>
        </span>
      </div>
      <p v-if="status.notice" class="notice">⚠️ {{ status.notice }}</p>
      <p class="lifecycle"><HelpButton topic="lifecycle" /> {{ t("model_lifecycle_help_tooltip") }}</p>
    </div>
  </details>
</template>

<style scoped>
.status { padding: 4px 0 0; font-size: 0.85rem; }
summary { display: flex; align-items: center; gap: 10px; cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
.lifecycle { display: flex; align-items: center; gap: 8px; margin: 4px 0 0; color: var(--aifred-text-secondary); }
.badge { position: relative; display: inline-flex; width: 22px; height: 22px; }
.badge img { width: 22px; height: 22px; object-fit: contain; }
.dot { position: absolute; right: -2px; bottom: -2px; width: 9px; height: 9px; border-radius: 50%; border: 1px solid var(--aifred-page-bg); }
.green .dot { background: var(--aifred-accent-success); animation: breathe 3s ease-in-out infinite; }
.yellow .dot { background: var(--aifred-accent-warning); animation: breathe 1s ease-in-out infinite; }
.grey .dot { background: var(--aifred-text-muted); }
.red .dot { background: var(--aifred-danger); }
@keyframes breathe { 50% { opacity: 0.4; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none !important; } }
.refresh, .power { display: inline-flex; align-items: center; background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: var(--aifred-text-secondary); cursor: pointer; padding: 3px 6px; font: inherit; }
.refresh:hover, .power:hover:not(:disabled) { color: var(--aifred-primary); border-color: var(--aifred-primary); }
.refresh { margin-left: auto; }
/* A table: every line shares the columns (sign, service, model, time left, button), so the buttons stand one under the
   other whatever the length of a model's name. */
.list { display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto auto; align-items: center; gap: 4px 10px; padding: 6px 0 2px; }
.line { display: contents; }
.act { display: flex; justify-content: flex-end; }
.notice, .lifecycle { grid-column: 1 / -1; }
.label { color: var(--aifred-text-secondary); white-space: nowrap; }
.value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.time { color: var(--aifred-text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
.power:disabled { opacity: 0.5; cursor: progress; }
.notice { margin: 2px 0 0; color: var(--aifred-accent-amber); }
</style>
