<script setup lang="ts">
// The live view: the cameras shown, each with its picture (a stream at the chosen frame rate, or a still image
// renewed by hand), its resolution and briefing, the head of a camera that can move (held keys, home) and photos
// in full resolution. What is shown and how often is kept by the server (data/vision_preview.json).
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ApiError, api, apiUrl } from "../api/client";
import type { CameraRow, ChoiceOption, LiveView } from "../api/types";
import { t } from "../i18n";
import ImageLightbox from "./ImageLightbox.vue";

const live = ref<LiveView | null>(null);
const notice = ref("");
const focus = ref("");                                    // the camera shown large; "" = all
const renewed = ref(0);                                   // the still images' counter
const resolutions = reactive<Record<string, ChoiceOption[]>>({});
const photos = reactive<Record<string, string[]>>({});    // taken in this view
const speed = ref("");
const opened = ref<{ images: string[]; start: number } | null>(null);
const busy = ref("");

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
const send = <T,>(path: string, method: string, body?: object): Promise<T> =>
  api<T>(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

async function show(view: LiveView | null): Promise<void> {
  if (!view) return;
  live.value = view;
  speed.value ||= view.ptz_default_speed;
  for (const id of view.visible) {
    if (!(id in resolutions)) resolutions[id] = (await call(() => api<ChoiceOption[]>(`vision/cameras/${id}/resolutions`))) ?? [];
  }
}
onMounted(async () => show(await call(() => api<LiveView>("vision/live"))));

const shown = computed<CameraRow[]>(() => {
  const view = live.value;
  if (!view) return [];
  const visible = view.cameras.filter((camera) => view.visible.includes(camera.id));
  return focus.value ? visible.filter((camera) => camera.id === focus.value) : visible;
});

const change = async (body: object): Promise<void> => show(await call(() => send<LiveView>("vision/live", "PATCH", body)));
function toggle(id: string): void {
  const visible = live.value?.visible ?? [];
  void change({ visible_sources: visible.includes(id) ? visible.filter((other) => other !== id) : [...visible, id] });
}

// The server takes the camera's stored resolution; it is in the address only so a change loads the picture anew.
function picture(camera: CameraRow): string {
  const fps = live.value?.settings.fps ?? 0;
  const version = `${camera.resolution}-${renewed.value}`;
  return apiUrl(fps > 0 ? `vision/stream/${camera.id}?fps=${fps}&v=${version}` : `vision/snapshot/${camera.id}?v=${version}`);
}

async function keep(camera: CameraRow, body: object): Promise<void> {
  const changed = await call(() => send<CameraRow>(`vision/cameras/${camera.id}`, "PATCH", body));
  if (changed && live.value) live.value.cameras = live.value.cameras.map((other) => (other.id === changed.id ? changed : other));
}

// ── The head: moves while a key is held, the server stops it without a sign of life ──
let heartbeat: number | undefined;
let held = "";
async function press(camera: CameraRow, direction: string): Promise<void> {
  held = camera.id;
  await call(() => send<void>(`vision/ptz/${camera.id}/press`, "POST", { direction, speed: speed.value }));
  window.clearInterval(heartbeat);
  heartbeat = window.setInterval(() => void api(`vision/ptz/${held}/keepalive`, { method: "POST" }), live.value?.ptz_heartbeat_ms);
}
async function release(): Promise<void> {
  window.clearInterval(heartbeat);
  if (!held) return;
  const camera = held;
  held = "";
  await call(() => send<void>(`vision/ptz/${camera}/release`, "POST"));
}
onBeforeUnmount(() => void release());
const home = (camera: CameraRow): Promise<unknown> => call(() => send<void>(`vision/ptz/${camera.id}/home`, "POST"));

async function photo(camera: CameraRow): Promise<void> {
  busy.value = camera.id;
  const taken = await call(() => send<{ url: string }>(`vision/photo/${camera.id}`, "POST"));
  busy.value = "";
  if (taken) photos[camera.id] = [taken.url, ...(photos[camera.id] ?? [])];
}

const pad = [
  { direction: "up", label: "▲", tip: "vision_preview_ptz_up" },
  { direction: "left", label: "◀", tip: "vision_preview_ptz_left" },
  { direction: "right", label: "▶", tip: "vision_preview_ptz_right" },
  { direction: "down", label: "▼", tip: "vision_preview_ptz_down" },
  { direction: "zoom_in", label: "＋", tip: "vision_preview_ptz_zoom_in" },
  { direction: "zoom_out", label: "－", tip: "vision_preview_ptz_zoom_out" },
];
const picked = (event: Event): string => (event.target as HTMLSelectElement).value;
</script>

<template>
  <section v-if="live" class="live">
    <div class="bar">
      <button v-for="camera in live.cameras" :key="camera.id" class="chip" :class="{ on: live.visible.includes(camera.id), away: !camera.available }" @click="toggle(camera.id)">
        {{ camera.label }}{{ camera.available ? "" : " ✗" }}
      </button>
      <label class="fps">
        {{ t("vision_preview_fps_label") }}
        <select :value="String(live.settings.fps)" @change="change({ fps: Number(picked($event)) })">
          <option v-for="choice in live.fps_choices" :key="choice.id" :value="choice.id">{{ choice.label }}</option>
        </select>
      </label>
      <button v-if="live.settings.fps === 0" :title="t('vision_preview_refresh_tooltip')" @click="renewed++">🔄</button>
      <button :title="t('vision_preview_rescan_tooltip')" @click="call(() => send<LiveView>('vision/cameras/rescan', 'POST')).then(show)">🔍</button>
    </div>
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
    <p v-if="!live.cameras.length" class="empty">{{ t("vision_preview_no_source") }}</p>

    <div class="grid" :class="{ single: focus || shown.length === 1 }">
      <article v-for="camera in shown" :key="camera.id" class="camera">
        <header>
          <strong>{{ camera.label }}</strong>
          <span v-if="camera.alias" class="hardware">{{ camera.hardware_name }}</span>
        </header>
        <img
          :src="picture(camera)"
          :alt="camera.label"
          :title="t(focus ? 'vision_preview_focus_shrink' : 'vision_preview_focus_enlarge')"
          @click="focus = focus ? '' : camera.id"
        />
        <div class="tools">
          <span v-if="camera.ptz" class="pad">
            <button
              v-for="key in pad"
              :key="key.direction"
              :title="t(key.tip)"
              @pointerdown.prevent="press(camera, key.direction)"
              @pointerup="release"
              @pointerleave="release"
              @pointercancel="release"
            >{{ key.label }}</button>
            <button :title="t('vision_preview_ptz_home')" @click="home(camera)">⌂</button>
            <select v-model="speed">
              <option v-for="choice in live.ptz_speeds" :key="choice.id" :value="choice.id">{{ choice.label }}</option>
            </select>
          </span>
          <button :title="t('vision_preview_photo')" :disabled="busy === camera.id" @click="photo(camera)">📸</button>
          <select v-if="(resolutions[camera.id] ?? []).length > 1" :value="camera.resolution" @change="keep(camera, { resolution: picked($event) })">
            <option v-for="choice in resolutions[camera.id]" :key="choice.id" :value="choice.id">{{ choice.label }}</option>
          </select>
        </div>
        <label class="briefing">
          <span>{{ t("vision_preview_briefing_label") }}</span>
          <textarea
            :value="camera.prompt_context"
            :placeholder="t('vision_preview_briefing_placeholder')"
            rows="2"
            @blur="($event.target as HTMLTextAreaElement).value !== camera.prompt_context && keep(camera, { prompt_context: ($event.target as HTMLTextAreaElement).value })"
          ></textarea>
        </label>
        <div v-if="photos[camera.id]?.length" class="photos">
          <img
            v-for="(url, index) in photos[camera.id]"
            :key="url"
            :src="url"
            alt=""
            :title="t('vision_preview_photo_open')"
            @click="opened = { images: photos[camera.id] ?? [], start: index }"
          />
        </div>
      </article>
    </div>
    <ImageLightbox v-if="opened" :images="opened.images" :start="opened.start" @close="opened = null" />
  </section>
</template>

<style scoped>
.live { display: flex; flex-direction: column; gap: 10px; }
.bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
button, select, textarea { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
.chip.on { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.chip.away { opacity: 0.6; }
.fps { display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 12px; }
.grid.single { grid-template-columns: 1fr; }
.camera { display: flex; flex-direction: column; gap: 8px; padding: 10px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.camera header { display: flex; align-items: baseline; gap: 8px; }
.hardware { font-size: 0.75rem; color: var(--aifred-text-muted); }
.camera > img { width: 100%; border-radius: 6px; background: #000; cursor: zoom-in; min-height: 120px; }
.grid.single .camera > img { cursor: zoom-out; }
.tools { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.pad { display: inline-flex; flex-wrap: wrap; gap: 3px; align-items: center; }
.pad button { touch-action: none; user-select: none; min-width: 32px; }
.briefing { display: flex; flex-direction: column; gap: 3px; font-size: 0.8rem; color: var(--aifred-text-secondary); }
.briefing textarea { resize: vertical; color: var(--aifred-text-primary); }
.photos { display: flex; gap: 6px; flex-wrap: wrap; }
.photos img { height: 60px; border-radius: 4px; cursor: zoom-in; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
.empty { margin: 0; color: var(--aifred-text-muted); }
</style>
