<script setup lang="ts">
// The status line under the top bar (docs/de/architecture/service-status.md): closed, one icon per service with a
// dot (green loaded and breathing, yellow loading and faster, grey nothing loaded, red not reachable); open, a line
// per loaded thing with the time until it unloads and its button (unload, load, hold a machine's card free). The
// refresh button reads STT and TTS anew. The page counts down from the moment the snapshot arrived.
import { Gpu, Hand, Power, RefreshCw, TriangleAlert, X } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { language, t } from "../i18n";
import { type Action, type Color, type Row, SERVICES, type Service, formatRemaining, hostRows, localHoldRow, serviceRows, summaryColor } from "../lib/serviceStatus";
import { useServiceStatus } from "../stores/serviceStatus";
import HelpButton from "./help/HelpButton.vue";
import hatIcon from "../../../assets/AIfred-Zylinder.png";
import describerIcon from "../assets/status-icons/vision-describer-icon.svg";
import sttIcon from "../assets/status-icons/stt-suite-icon.svg";
import ttsIcon from "../assets/status-icons/tts-suite-icon.svg";

const status = useServiceStatus();
const ICONS: Record<Service, string> = { llm: hatIcon, describer: describerIcon, stt: sttIcon, tts: ttsIcon };
const NAMES: Record<Service, string> = {
  llm: "service_status_llm", describer: "service_status_describer", stt: "service_status_stt", tts: "service_status_tts",
};
// Until the first snapshot arrives the bar asks this often (the push brings only changes); then it ticks each second.
const ASK_AGAIN_MS = 5000;
const TICK_MS = 1000;
// STT and TTS push nothing, and a card another client frees (GPU-Orc for Agent-Orc, vllm-research) is not seen by
// AIfred itself: the visible bar reads them anew this often, and when the tab becomes visible again.
const REFRESH_EVERY_MS = 30000;

const labels = computed(() => ({
  lang: language.value,
  noModel: t("service_status_no_model"),
  noneLoaded: t("service_status_none_loaded"),
  loading: t("service_status_loading"),
  busy: t("service_status_busy"),
  unreachable: t("service_status_unreachable"),
  hostHeld: t("service_status_host_held"),
  hostRequested: t("service_status_host_requested"),
  hostHeldByOther: t("service_status_host_held_by_other"),
  hostUsed: t("service_status_host_used"),
}));
const rows = computed(() => (status.snapshot ? serviceRows(status.snapshot, labels.value) : null));
const machines = computed(() => hostRows(status.hosts, labels.value));
const localLabels = computed(() => ({
  lang: language.value,
  free: t("service_status_local_free"),
  requested: t("service_status_local_requested"),
  granted: t("service_status_local_granted"),
  active: t("service_status_local_active"),
  reason: t("service_status_local_reason"),
  model: t("service_status_local_model"),
  since: t("service_status_local_since"),
  off: t("service_status_local_off"),
  unreachable: t("service_status_local_unreachable"),
  notRequested: t("service_status_local_not_requested"),
  noHold: t("service_status_local_no_hold"),
}));
const localHold = computed(() => (status.localHold ? localHoldRow(status.localHold, localLabels.value) : null));
const now = ref(performance.now());
const elapsed = computed(() => (now.value - status.receivedAt) / 1000);

function badgeColor(service: Service): Color {
  return rows.value ? summaryColor(rows.value[service]) : "grey";
}

function timeText(row: Row): string {
  if (row.remaining === null) return "";
  return `${t("service_status_unloads_in")} ${row.upperBound ? "≤ " : ""}${formatRemaining(row.remaining - elapsed.value, t("service_status_days_short"))}`;
}

function endLocalHold(): void {
  void status.endHold(status.localHold?.owner ?? "", status.localHold?.reason ?? "");
}

const ACTION_TITLES: Record<Action["verb"], string> = {
  load: "service_status_load", unload: "service_status_unload", hold: "service_status_host_hold", release: "service_status_host_release",
  grant: "service_status_local_grant", end: "service_status_local_end",
};

let timer = 0;
let refreshTimer = 0;
let askedAt = -Infinity;

function refreshIfVisible(): void {
  if (document.visibilityState === "visible") void status.fetch(true);
}
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
  refreshTimer = window.setInterval(refreshIfVisible, REFRESH_EVERY_MS);
  document.addEventListener("visibilitychange", refreshIfVisible);
});
onBeforeUnmount(() => {
  window.clearInterval(timer);
  window.clearInterval(refreshTimer);
  document.removeEventListener("visibilitychange", refreshIfVisible);
});
</script>

<template>
  <details class="status">
    <summary>
      <span v-for="service in SERVICES" :key="service" class="badge" :class="badgeColor(service)" :title="t(NAMES[service])">
        <img :src="ICONS[service]" alt="" /><span class="dot" />
      </span>
      <!-- This machine's hold also in the closed bar: a request or a hold turns it yellow, seen without opening. -->
      <span v-if="localHold" class="badge" :class="[localHold.color, { pending: localHold.pending }]" :title="localHold.title">
        <Gpu :size="20" /><span class="dot" />
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
      <h4 v-if="localHold || machines.length" class="heading">{{ t("service_status_gpus") }}</h4>
      <!-- This machine: a plain line while nobody asks, an amber card once somebody asks for or holds the cards. The state
           in words is the visible reason a button is greyed out; the buttons stay. -->
      <div v-if="localHold" class="local-hold" :class="{ card: localHold.card }">
        <span class="badge" :class="[localHold.color, { pending: localHold.pending }]"><Gpu :size="20" /><span class="dot" /></span>
        <span class="label">{{ localHold.label }}</span>
        <span class="value wrap" :class="{ held: localHold.card }">{{ localHold.title }}</span>
        <span v-if="localHold.detail" class="detail">{{ localHold.detail }}</span>
        <span class="buttons">
          <button class="primary" :disabled="status.acting || !!localHold.grant.blocked" :title="localHold.grant.blocked" @click="status.act(localHold.grant)">
            <Hand :size="14" />{{ t("service_status_local_grant") }}
          </button>
          <button :disabled="status.acting || !!localHold.end.blocked" :title="localHold.end.blocked" @click="endLocalHold">
            <X :size="14" />{{ t("service_status_local_end") }}
          </button>
        </span>
      </div>
      <div v-for="host in machines" :key="host.label" class="line">
        <span class="badge" :class="host.color"><Gpu :size="20" /><span class="dot" /></span>
        <span class="label">{{ host.label }}</span>
        <span class="value wrap" :title="host.title ?? host.text">{{ host.text }}</span>
        <span class="time" />
        <span class="act">
          <button
            v-if="host.action"
            class="power"
            :class="{ blocked: host.action.blocked }"
            :disabled="status.acting || !!host.action.blocked"
            :title="host.action.blocked ?? t(ACTION_TITLES[host.action.verb])"
            @click="status.act(host.action)"
          >
            <Power :size="14" />
          </button>
        </span>
        <span v-if="host.detail" class="detail" :title="host.detail">{{ host.detail }}</span>
      </div>
      <p v-if="status.notice" class="notice"><TriangleAlert :size="16" /> {{ status.notice }}</p>
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
/* A hold: amber like Agent-Orc's. Still once granted or active; it pulses only while a request waits for a decision. */
.amber .dot { background: var(--aifred-accent-amber); }
.amber.pending .dot { animation: pulse 1.2s ease-in-out infinite; }
.value.held { color: var(--aifred-accent-amber); }
.grey .dot { background: var(--aifred-text-muted); }
.red .dot { background: var(--aifred-danger); }
@keyframes breathe { 50% { opacity: 0.4; } }
@keyframes pulse { 50% { transform: scale(1.5); opacity: 0.5; } }
@media (prefers-reduced-motion: reduce) { .dot { animation: none !important; } }
.refresh, .power { display: inline-flex; align-items: center; padding: 3px 6px; }
.refresh { margin-left: auto; }
/* A table: every line shares the columns (sign, service, model, time left, button), so the buttons stand one under the
   other whatever the length of a model's name. */
.list { display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto auto; align-items: center; gap: 4px 10px; padding: 6px 0 2px; }
.line { display: contents; }
.act { display: flex; justify-content: flex-end; }
.notice, .lifecycle { grid-column: 1 / -1; }
.label { color: var(--aifred-text-secondary); white-space: nowrap; }
.value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.heading { grid-column: 1 / -1; margin: 6px 0 0; font-size: 0.8rem; font-weight: 600; color: var(--aifred-text-secondary); }
.badge svg { color: var(--aifred-text-secondary); }
.detail { grid-column: 3 / -1; margin-top: -2px; color: var(--aifred-text-muted); overflow-wrap: anywhere; white-space: pre-line; }
.value.wrap { white-space: normal; overflow-wrap: anywhere; } /* a machine's line breaks instead of cutting off its numbers */
.time { color: var(--aifred-text-muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
.power:disabled { opacity: 0.5; cursor: progress; }
.power.blocked:disabled { cursor: not-allowed; } /* another owner's hold: nothing this button may do */
/* This machine's hold: one box over the whole width (sign, name, state, then the detail and the buttons beneath). */
/* subgrid: the same columns as the list, so the state stands where the other lines have their value */
.local-hold { grid-column: 1 / -1; display: grid; grid-template-columns: subgrid; align-items: center; gap: 4px 10px; }
.local-hold > .value { grid-column: 3 / -1; }
.local-hold > .detail, .local-hold > .buttons { grid-column: 3 / -1; margin-top: 0; }
/* The card's frame lies behind the line instead of padding it: padding would widen the shared columns and shift
   every line of the list the moment a request arrives. */
.local-hold.card { position: relative; isolation: isolate; margin: 8px 0; }
.local-hold.card::before { content: ""; position: absolute; inset: -6px -8px; z-index: -1; border: 1px solid var(--aifred-accent-amber); border-radius: 8px; background: var(--aifred-warning-bg); }
.local-hold.card .detail { color: var(--aifred-text-soft); }
.buttons { display: flex; flex-wrap: wrap; gap: 8px; }
.buttons button { display: inline-flex; align-items: center; gap: 4px; font-size: 0.8rem; padding: 3px 8px; }
.buttons button:disabled { cursor: not-allowed; opacity: 0.5; filter: grayscale(1); } /* greyed out: no amber that looks half active */
.notice { margin: 2px 0 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
