<script setup lang="ts">
// The vision's settings: the eye (armed: every camera with its background switch watched, inside its window), the
// image model (on demand or live, which one, loaded or not), who describes images in which order, face and person
// detection, how long events are kept, the alert rules, and per camera everything that belongs to it: name,
// background, alerts with their types, quiet hours, active window, event interval, zones, its connection. A change
// of the background or the window takes effect at once.
import { Power } from "lucide-vue-next";
import { onMounted, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { AlertRules, CameraRow, LiveView, RtspCameras, VigilantiaRow } from "../api/types";
import { t } from "../i18n";
import DescriberList from "./DescriberList.vue";
import RtspForm from "./RtspForm.vue";
import ZoneEditor from "./ZoneEditor.vue";

const settings = ref<VigilantiaRow | null>(null);
const cameras = ref<CameraRow[]>([]);
const loaded = ref<boolean | null>(null);
const rtsp = ref<RtspCameras | null>(null);
const rules = ref<AlertRules | null>(null);
const connecting = ref("");     // the camera whose connection form is open; "+" = a new one
const zoning = ref<CameraRow | null>(null);   // the camera whose zone mask is being painted
const notice = ref("");
const busy = ref(false);

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
const send = <T,>(path: string, method: string, body: object): Promise<T> => api<T>(path, { method, body: JSON.stringify(body) });

onMounted(async () => {
  settings.value = await call(() => api<VigilantiaRow>("vision/settings"));
  cameras.value = (await call(() => api<LiveView>("vision/live")))?.cameras ?? [];
  loaded.value = (await call(() => api<{ loaded: boolean }>("vision/model")))?.loaded ?? null;
  rtsp.value = await call(() => api<RtspCameras>("vision/rtsp-cameras"));
  rules.value = await call(() => api<AlertRules>("vision/alert-rules"));
});

async function rule(category: string, body: object): Promise<void> {
  rules.value = (await call(() => send<AlertRules>(`vision/alert-rules/${category}`, "PATCH", body))) ?? rules.value;
}

const rtspOf = (camera: CameraRow) => rtsp.value?.cameras.find((entry) => entry.source_id === camera.id) ?? null;
async function connected(list: RtspCameras): Promise<void> {
  rtsp.value = list;
  connecting.value = "";
  cameras.value = (await call(() => api<LiveView>("vision/cameras/rescan", { method: "POST" })))?.cameras ?? cameras.value;
}

async function arm(armed: boolean): Promise<void> {
  busy.value = true;
  settings.value = (await call(() => send<VigilantiaRow>("vision/armed", "PUT", { armed }))) ?? settings.value;
  busy.value = false;
}
async function change(body: object): Promise<void> {
  settings.value = (await call(() => send<VigilantiaRow>("vision/settings", "PATCH", body))) ?? settings.value;
}
async function power(): Promise<void> {
  busy.value = true;
  loaded.value = (await call(() => send<{ loaded: boolean }>("vision/model", "PUT", { loaded: !loaded.value })))?.loaded ?? loaded.value;
  busy.value = false;
}
function replaced(changed: CameraRow): void {
  cameras.value = cameras.value.map((other) => (other.id === changed.id ? changed : other));
}
async function keep(camera: CameraRow, body: object): Promise<void> {
  const changed = await call(() => send<CameraRow>(`vision/cameras/${camera.id}`, "PATCH", body));
  if (changed) replaced(changed);
}
function alertType(camera: CameraRow, kind: string): void {
  const kinds = camera.alert_types.includes(kind) ? camera.alert_types.filter((other) => other !== kind) : [...camera.alert_types, kind];
  void keep(camera, { alert_types: kinds });
}

const hours = Array.from({ length: 24 }, (_, hour) => hour);
const value = (event: Event): string => (event.target as HTMLInputElement | HTMLSelectElement).value;
const checked = (event: Event): boolean => (event.target as HTMLInputElement).checked;
</script>

<template>
  <section v-if="settings" class="vision-settings">
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>

    <article class="eye" :class="{ armed: settings.armed }">
      <button
        class="eye-button"
        :disabled="busy || (!settings.plugin_enabled && !settings.armed)"
        :title="settings.plugin_enabled ? t(settings.armed ? 'vigilantia_disarm_tooltip' : 'vigilantia_arm_tooltip') : t('vigilantia_plugin_disabled_tooltip')"
        @click="arm(!settings.armed)"
      >{{ settings.armed ? "👁️" : "🙈" }}</button>
      <div>
        <strong>{{ t(settings.armed ? "vigilantia_help_eye_open_label" : "vigilantia_help_eye_closed_label") }}</strong>
        <p class="hint">{{ t(settings.armed ? "vigilantia_help_eye_open_body" : "vigilantia_help_eye_closed_body") }}</p>
      </div>
    </article>

    <article>
      <div class="line">
        <span class="name">{{ t("vision_settings_mode_label") }}</span>
        <select :value="settings.mode" :title="t('vision_settings_mode_help')" @change="change({ mode: value($event) })">
          <option v-for="mode in settings.modes" :key="mode.id" :value="mode.id">{{ mode.label }}</option>
        </select>
      </div>
      <div class="line">
        <span class="name">{{ t("vision_settings_model_label") }}</span>
        <select :value="settings.model" :title="t('vision_settings_model_help')" @change="change({ model: value($event) })">
          <option v-if="!settings.model" value="">–</option>
          <option v-for="model in settings.models" :key="model" :value="model">{{ model }}</option>
        </select>
        <button v-if="loaded !== null" :disabled="busy || !settings.model" :class="{ on: loaded }" :title="t(loaded ? 'vision_model_unload' : 'vision_model_load')" @click="power"><Power :size="14" /></button>
      </div>
    </article>

    <!-- this machine's row names the image model: another model reads the list anew -->
    <DescriberList :key="settings.model" />

    <article>
      <label class="line" :title="t('vision_settings_face_recognition_help')">
        <span class="name">{{ t("vision_settings_face_recognition_label") }}</span>
        <input type="checkbox" :checked="settings.face_enabled" @change="change({ face_enabled: checked($event) })" />
      </label>
      <label class="line" :title="t('vision_settings_face_continuous_help')">
        <span class="name">{{ t("vision_settings_face_continuous_label") }}</span>
        <input type="checkbox" :checked="settings.face_continuous" :disabled="!settings.face_enabled" @change="change({ face_continuous: checked($event) })" />
      </label>
      <label class="line" :title="t('vigilantia_help_person_body')">
        <span class="name">{{ t("vigilantia_help_person_label") }}</span>
        <input type="checkbox" :checked="settings.person_detect" @change="change({ person_detect: checked($event) })" />
      </label>
      <label class="line" :title="t('vision_settings_face_retention_help')">
        <span class="name">{{ t("vision_settings_face_retention_label") }}</span>
        <input class="days" type="number" min="1" max="3650" :value="settings.retention_days" @change="change({ retention_days: Number(value($event)) })" />
      </label>
    </article>

    <article v-if="rules" class="rules">
      <strong :title="t('alert_rules_help')">{{ t("alert_rules_title") }}</strong>
      <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th></th>
            <th>{{ t("alert_rules_col_vlm") }}</th>
            <th v-for="channel in rules.channels" :key="channel.id">{{ channel.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rules.rules" :key="row.category">
            <td class="who">{{ row.label }}</td>
            <td><input type="checkbox" :checked="row.vlm" @change="rule(row.category, { vlm: checked($event) })" /></td>
            <td v-for="channel in rules.channels" :key="channel.id">
              <input type="checkbox" :checked="row.sinks.includes(channel.id)" @change="rule(row.category, { sink: channel.id, sink_on: checked($event) })" />
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </article>

    <div class="sources-head">
      <h3>{{ t("vision_settings_sources_label") }}</h3>
      <button v-if="rtsp && connecting !== '+'" @click="connecting = '+'">＋ {{ t("vision_rtsp_add") }}</button>
    </div>
    <p class="hint">{{ t("vision_settings_sources_help") }}</p>
    <RtspForm v-if="rtsp && connecting === '+'" :camera="null" :profiles="rtsp.profiles" @saved="connected" @cancel="connecting = ''" />
    <p v-if="!cameras.length" class="hint">{{ t("vision_settings_sources_empty") }}</p>
    <article v-for="camera in cameras" :key="camera.id" class="camera" :class="{ away: !camera.available }">
      <header>
        <input class="alias" :value="camera.alias" :placeholder="camera.hardware_name" @change="keep(camera, { alias: value($event) })" />
        <span class="hardware">{{ camera.hardware_name }} · {{ camera.id }}</span>
        <button v-if="rtspOf(camera)" :title="t('vision_rtsp_edit_connection')" @click="connecting = connecting === camera.id ? '' : camera.id">🔌</button>
        <button :title="t('vision_settings_zone_mask_button')" @click="zoning = camera">🗺️</button>
      </header>
      <RtspForm v-if="rtsp && connecting === camera.id" :camera="rtspOf(camera)" :profiles="rtsp.profiles" @saved="connected" @cancel="connecting = ''" />
      <label class="line" :title="t('vision_settings_source_background_help')">
        <span class="name">{{ t("vision_preview_auto_start_label") }}</span>
        <input type="checkbox" :checked="camera.auto_start" @change="keep(camera, { auto_start: checked($event) })" />
      </label>
      <div class="line">
        <label class="name"><input type="checkbox" :checked="camera.alerts_enabled" @change="keep(camera, { alerts_enabled: checked($event) })" /> {{ t("vision_settings_source_alerts_label") }}</label>
        <label v-for="kind in settings.alert_types" :key="kind.id" class="kind">
          <input type="checkbox" :checked="camera.alert_types.includes(kind.id)" :disabled="!camera.alerts_enabled" @change="alertType(camera, kind.id)" /> {{ kind.label }}
        </label>
      </div>
      <div class="line">
        <label class="name"><input type="checkbox" :checked="camera.quiet_enabled" @change="keep(camera, { quiet_enabled: checked($event) })" /> {{ t("vision_settings_quiet_label") }}</label>
        <select :value="camera.quiet_start" :disabled="!camera.quiet_enabled" @change="keep(camera, { quiet_start: Number(value($event)) })">
          <option v-for="hour in hours" :key="hour" :value="hour">{{ hour }} {{ t("vision_settings_quiet_oclock") }}</option>
        </select>
        –
        <select :value="camera.quiet_end" :disabled="!camera.quiet_enabled" @change="keep(camera, { quiet_end: Number(value($event)) })">
          <option v-for="hour in hours" :key="hour" :value="hour">{{ hour }} {{ t("vision_settings_quiet_oclock") }}</option>
        </select>
      </div>
      <div class="line">
        <label class="name"><input type="checkbox" :checked="camera.schedule_enabled" @change="keep(camera, { schedule_enabled: checked($event) })" /> {{ t("vision_settings_schedule_label") }}</label>
        <input type="time" :value="camera.schedule_start" :disabled="!camera.schedule_enabled" @change="keep(camera, { schedule_start: value($event) })" />
        –
        <input type="time" :value="camera.schedule_end" :disabled="!camera.schedule_enabled" @change="keep(camera, { schedule_end: value($event) })" />
      </div>
      <div class="line">
        <span class="name">{{ t("vision_settings_event_interval_label") }}</span>
        <select :value="String(camera.min_event_interval_sec)" @change="keep(camera, { min_event_interval_sec: Number(value($event)) })">
          <option v-for="choice in settings.event_intervals" :key="choice.id" :value="choice.id">{{ choice.label }}</option>
        </select>
      </div>
    </article>
    <ZoneEditor v-if="zoning" :camera="zoning" @close="zoning = null" @changed="replaced" />
  </section>
</template>

<style scoped>
/* Too many columns for a phone: the table scrolls sideways in its own box instead of widening the page. */
.table-scroll { overflow-x: auto; }
.vision-settings { display: flex; flex-direction: column; gap: 12px; max-width: 960px; }
article { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.eye { flex-direction: row; align-items: center; gap: 14px; }
.eye.armed { border-color: var(--aifred-primary); }
.eye-button { font-size: 2rem; padding: 4px 12px; }
.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.name { min-width: 210px; font-weight: 600; font-size: 0.9rem; display: inline-flex; align-items: center; gap: 6px; }
.kind { font-size: 0.85rem; display: inline-flex; align-items: center; gap: 4px; }
select, input, button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; padding: 0; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
button.on { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.days { width: 90px; }
.camera header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.camera.away { opacity: 0.7; }
.alias { font-weight: 600; min-width: 200px; }
.hardware { font-size: 0.75rem; color: var(--aifred-text-muted); flex: 1; }
h3 { margin: 6px 0 0; font-size: 1rem; }
.sources-head { display: flex; align-items: center; gap: 12px; }
.rules table { border-collapse: collapse; }
.rules th { font-size: 0.75rem; font-weight: 600; color: var(--aifred-text-secondary); padding: 3px 8px; text-align: center; }
.rules td { padding: 3px 8px; text-align: center; border-top: 1px solid var(--aifred-border); }
.rules td.who { text-align: left; white-space: nowrap; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
