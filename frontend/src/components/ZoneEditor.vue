<script setup lang="ts">
// The zone mask of one camera, painted over its live picture: where the background watch ignores motion, what it
// blacks out, where alone it looks. The stream burns the live motion in blue; the page measures how much of the
// watched picture moves, against the mask as painted, next to the camera's trigger threshold. Saving takes effect in
// a running watch at once (POST vision/zone-mask; the threshold is a camera setting, PATCH vision/cameras/{id}).
import { Lightbulb, MapIcon, TriangleAlert } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { ApiError, api, apiUrl } from "../api/client";
import type { CameraRow, ZoneMask } from "../api/types";
import { formatNumber, t } from "../i18n";
import { BLACKOUT, type Cell, IGNORE, type Mask, NOTHING, ONLY_HERE, emptyMask, fromCells, isEmpty, motionShare, paint, rowsFor, toCells } from "../lib/zoneMask";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ camera: CameraRow }>();
const emit = defineEmits<{ close: []; changed: [camera: CameraRow] }>();

const CANVAS_WIDTH = 900;                 // the canvas' own resolution; the page scales it to the dialog
const GRID = { min: 16, max: 96, step: 8, start: 48 };
const BRUSH = { min: 1, max: 10, start: 3 };
const THRESHOLD = { min: 0.1, max: 25, step: 0.1 };   // percent of the picture, inside the server's range
const STREAM_FPS = 4;
const MEASURE_MS = 300;
const STATUS_MS = 3500;
const KINDS = [
  { value: IGNORE, label: "zone_editor_mode_motion", help: "zone_editor_help_red", color: "--aifred-zone-ignore" },
  { value: BLACKOUT, label: "zone_editor_mode_blackout", help: "zone_editor_help_black", color: "--aifred-zone-blackout" },
  { value: ONLY_HERE, label: "zone_editor_mode_roi", help: "zone_editor_help_green", color: "--aifred-zone-only-here" },
] as const;

const canvas = ref<HTMLCanvasElement | null>(null);
const stream = ref<HTMLImageElement | null>(null);
const mask = shallowRef<Mask>(emptyMask(GRID.start, rowsFor(GRID.start, 9 / 16)));
const aspect = ref(9 / 16);
const pictureOk = ref(false);
const version = ref(Date.now());
const active = ref(true);
const kind = ref<Cell>(IGNORE);
const brush = ref(BRUSH.start);
const showMotion = ref(true);
const threshold = ref(props.camera.motion_min_area_ratio * 100);
const measured = ref<number | null>(null);
const dirty = ref(false);
const closing = ref(false);
const help = ref(false);
const status = ref({ text: t("zone_editor_loading"), failed: false });
let painting: Cell | null = null;
let statusTimer: number | undefined;
let measureTimer: number | undefined;

const streamUrl = computed(() =>
  apiUrl(`vision/stream/${props.camera.id}?fps=${STREAM_FPS}&v=${version.value}${showMotion.value ? "&overlay=motion" : ""}`),
);
const stageWidth = computed(() => `min(calc(88 * var(--vw)), calc(${(60 / aspect.value).toFixed(1)} * var(--dvh)))`);
const percent = (value: number): string => `${formatNumber(value, 1)} %`;
const sliderGrid = computed(() => Math.min(GRID.max, Math.max(GRID.min, Math.round(mask.value.cols / GRID.step) * GRID.step)));

function say(text: string, failed = false, flash = false): void {
  window.clearTimeout(statusTimer);
  status.value = { text, failed };
  if (flash) statusTimer = window.setTimeout(() => (status.value = { text: t("zone_editor_live"), failed: false }), STATUS_MS);
}
const reason = (error: unknown): string => (error instanceof ApiError ? error.detail : String(error));

function draw(): void {
  const element = canvas.value;
  const context = element?.getContext("2d");
  if (!element || !context) return;
  const style = getComputedStyle(element);
  const colors = new Map<Cell, string>(KINDS.map((entry) => [entry.value, style.getPropertyValue(entry.color)]));
  const { cols, rows, cells } = mask.value;
  const cellWidth = element.width / cols;
  const cellHeight = element.height / rows;
  context.clearRect(0, 0, element.width, element.height);
  cells.forEach((cell, index) => {
    if (cell === NOTHING) return;
    context.fillStyle = colors.get(cell) ?? "";
    context.fillRect((index % cols) * cellWidth, Math.floor(index / cols) * cellHeight, cellWidth + 0.5, cellHeight + 0.5);
  });
  context.strokeStyle = style.getPropertyValue("--aifred-zone-grid");
  context.lineWidth = 1;
  context.beginPath();
  for (let col = 0; col <= cols; col++) { context.moveTo(col * cellWidth, 0); context.lineTo(col * cellWidth, element.height); }
  for (let row = 0; row <= rows; row++) { context.moveTo(0, row * cellHeight); context.lineTo(element.width, row * cellHeight); }
  context.stroke();
}

function sizeCanvas(): void {
  if (!canvas.value) return;
  canvas.value.width = CANVAS_WIDTH;
  canvas.value.height = Math.round(CANVAS_WIDTH * aspect.value);
  draw();
}

// The picture's shape comes from one still image; the stream follows it. Without a picture the grid still paints.
function loadPicture(): void {
  say(t("zone_editor_loading"));
  version.value = Date.now();
  const probe = new Image();
  probe.onload = () => {
    aspect.value = probe.naturalHeight / probe.naturalWidth;
    pictureOk.value = true;
    sizeCanvas();
    say(t("zone_editor_live"));
  };
  probe.onerror = () => {
    pictureOk.value = false;
    sizeCanvas();
    say(t("zone_editor_offline"), true);
  };
  probe.src = apiUrl(`vision/snapshot/${props.camera.id}?v=${version.value}`);
}

function cellAt(event: PointerEvent): [number, number] {
  const box = (event.currentTarget as HTMLCanvasElement).getBoundingClientRect();
  return [
    Math.floor(((event.clientX - box.left) / box.width) * mask.value.cols),
    Math.floor(((event.clientY - box.top) / box.height) * mask.value.rows),
  ];
}
function stroke(event: PointerEvent): void {
  if (painting === null) return;
  const [col, row] = cellAt(event);
  paint(mask.value, col, row, brush.value, painting);
  dirty.value = true;
  draw();
}
function lift(): void {
  painting = null;
}
function down(event: PointerEvent): void {
  event.preventDefault();
  painting = event.button === 2 ? NOTHING : kind.value;
  (event.currentTarget as HTMLCanvasElement).setPointerCapture(event.pointerId);
  stroke(event);
}

function regrid(cols: number): void {
  mask.value = emptyMask(cols, rowsFor(cols, aspect.value));
  dirty.value = true;
  draw();
  say(t("zone_editor_grid_changed"));
}
function clear(): void {
  mask.value = emptyMask(mask.value.cols, mask.value.rows);
  dirty.value = true;
  draw();
  say(t("zone_editor_cleared"));
}

async function save(): Promise<void> {
  say(t("zone_editor_saving"));
  const { cols, rows } = mask.value;
  try {
    await api("vision/zone-mask", {
      method: "POST",
      body: JSON.stringify({ source_id: props.camera.id, cols, rows, cells: toCells(mask.value), enabled: active.value }),
    });
  } catch (error) {
    say(t("zone_editor_save_failed") + reason(error), true);
    return;
  }
  dirty.value = false;
  say(t(isEmpty(mask.value) ? "zone_editor_removed" : active.value ? "zone_editor_saved" : "zone_editor_saved_off"), false, true);
}

async function keepThreshold(): Promise<void> {
  try {
    const changed = await api<CameraRow>(`vision/cameras/${props.camera.id}`, {
      method: "PATCH",
      body: JSON.stringify({ motion_min_area_ratio: threshold.value / 100 }),
    });
    emit("changed", changed);
    say(t("zone_editor_motion_saved"), false, true);
  } catch (error) {
    say(t("zone_editor_motion_save_failed") + reason(error), true);
  }
}

// How much of the watched picture moves: the blue the server burns in, counted in the cells the watch looks at.
const scratch = document.createElement("canvas");
const scratchContext = scratch.getContext("2d", { willReadFrequently: true });
function measure(): void {
  const picture = stream.value;
  const element = canvas.value;
  if (!showMotion.value || !pictureOk.value || !picture?.naturalWidth || !element || !scratchContext) {
    measured.value = null;
    return;
  }
  const { width, height } = element;
  if (scratch.width !== width || scratch.height !== height) {
    scratch.width = width;
    scratch.height = height;
  }
  scratchContext.drawImage(picture, 0, 0, width, height);
  measured.value = motionShare(scratchContext.getImageData(0, 0, width, height).data, width, height, mask.value, active.value);
}

function requestClose(): void {
  if (dirty.value) closing.value = true;
  else emit("close");
}

onMounted(async () => {
  try {
    const stored = await api<ZoneMask>(`vision/zone-mask?source_id=${encodeURIComponent(props.camera.id)}`);
    if (stored.exists && stored.cells.length === stored.cols * stored.rows) {
      mask.value = fromCells(stored.cols, stored.rows, stored.cells);
      active.value = stored.enabled;
    }
  } catch (error) {
    say(reason(error), true);
  }
  loadPicture();
  measureTimer = window.setInterval(measure, MEASURE_MS);
});
onBeforeUnmount(() => {
  window.clearInterval(measureTimer);
  window.clearTimeout(statusTimer);
  if (stream.value) stream.value.src = "";   // ends the stream's connection at once
});

const value = (event: Event): string => (event.target as HTMLInputElement).value;
function switchMask(event: Event): void {
  active.value = (event.target as HTMLInputElement).checked;
  void save();
}
</script>

<template>
  <ModalDialog @close="requestClose">
    <header class="head">
      <strong><MapIcon :size="16" /> {{ t("zone_editor_title") }}</strong>
      <span class="camera">{{ camera.label }}</span>
      <button class="icon help" :title="t('zone_editor_help_title')" :class="{ on: help }" @click="help = !help"><Lightbulb :size="14" /></button>
      <button class="icon" :title="t('dialog_close')" @click="requestClose">×</button>
    </header>

    <section v-if="help" class="help">
      <p>{{ t("zone_editor_help_intro") }}</p>
      <p v-for="entry in KINDS" :key="entry.value" class="kind-help">
        <span class="swatch" :style="{ background: `var(${entry.color})` }" /><b>{{ t(entry.label) }}</b> — {{ t(entry.help) }}
      </p>
      <p><b>{{ t("zone_editor_show_motion") }}</b> — {{ t("zone_editor_help_blue") }}</p>
      <p><b>{{ t("zone_editor_help_threshold_label") }}</b> — {{ t("zone_editor_help_threshold") }}</p>
      <p><b>{{ t("zone_editor_help_ego_label") }}</b> — {{ t("zone_editor_help_ego") }}</p>
      <p>{{ t("zone_editor_help_controls") }}</p>
    </section>

    <p v-if="camera.detects_itself" class="warning"><TriangleAlert :size="16" /> {{ t("zone_editor_ai_cam_hint") }}</p>

    <div class="controls">
      <span class="muted">{{ t("zone_editor_paint_with") }}</span>
      <label v-for="entry in KINDS" :key="entry.value" class="kind">
        <input v-model="kind" type="radio" name="zone-kind" :value="entry.value" />
        <span class="swatch" :style="{ background: `var(${entry.color})` }" />{{ t(entry.label) }}
      </label>
      <label><input type="checkbox" :checked="active" @change="switchMask" /> {{ t("zone_editor_active") }}</label>
    </div>
    <div class="controls">
      <label>{{ t("zone_editor_brush") }} <input v-model.number="brush" type="range" :min="BRUSH.min" :max="BRUSH.max" /> {{ brush }}</label>
      <label>
        {{ t("zone_editor_grid") }}
        <input type="range" :min="GRID.min" :max="GRID.max" :step="GRID.step" :value="sliderGrid" @change="regrid(Number(value($event)))" />
        {{ mask.cols }}×{{ mask.rows }}
      </label>
      <button @click="loadPicture">{{ t("zone_editor_reload") }}</button>
      <button @click="clear">{{ t("zone_editor_clear") }}</button>
      <button class="primary" @click="save">{{ t("zone_editor_save") }}</button>
    </div>
    <div class="controls">
      <label><input v-model="showMotion" type="checkbox" /> {{ t("zone_editor_show_motion") }}</label>
      <label class="threshold">
        {{ t("zone_editor_motion_threshold") }}
        <input v-model.number="threshold" type="range" :min="THRESHOLD.min" :max="THRESHOLD.max" :step="THRESHOLD.step" @change="keepThreshold" />
        <span class="number">{{ percent(threshold) }}</span>
      </label>
      <span class="muted">
        {{ t("zone_editor_motion_current") }}
        <b class="number" :class="measured === null ? '' : measured >= threshold ? 'over' : 'under'">{{ measured === null ? "–" : percent(measured) }}</b>
      </span>
    </div>
    <p class="muted">{{ t("zone_editor_paint_hint") }}</p>

    <div class="stage" :style="{ width: stageWidth }">
      <img v-if="pictureOk" ref="stream" :src="streamUrl" alt="" />
      <canvas ref="canvas" @pointerdown="down" @pointermove="stroke" @pointerup="lift" @pointercancel="lift" @contextmenu.prevent />
    </div>
    <p class="status" :class="{ failed: status.failed }">{{ status.text }}</p>

    <div v-if="closing" class="unsaved">
      <span>{{ t("zone_editor_unsaved") }}</span>
      <button class="danger" @click="emit('close')">{{ t("zone_editor_discard") }}</button>
      <button @click="closing = false">{{ t("zone_editor_keep_editing") }}</button>
    </div>
  </ModalDialog>
</template>

<style scoped>
.head { display: flex; align-items: center; gap: 10px; }
.head strong { color: var(--aifred-primary); --icon-own: currentColor; }
.camera { flex: 1; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.icon { background: none; border: none; color: inherit; font-size: 1.2rem; cursor: pointer; padding: 0 4px; }
.icon.on { filter: drop-shadow(0 0 4px var(--aifred-accent-gold)); }
/* The help bulb looks like HelpButton's. */
.icon.help { padding: 0; color: var(--aifred-accent-amber); transition: transform 0.2s ease; --icon-own: currentColor; }
.icon.help:hover { transform: scale(1.15); }
.help { display: flex; flex-direction: column; gap: 6px; font-size: 0.85rem; padding: 10px 12px; background: var(--aifred-input-bg); border-radius: 8px; max-width: 760px; }
.help p { margin: 0; line-height: 1.45; }
.kind-help { display: flex; gap: 8px; align-items: baseline; }
.warning { margin: 0; padding: 7px 10px; font-size: 0.85rem; color: var(--aifred-accent-amber); border: 1px solid var(--aifred-accent-amber); border-radius: 6px; background: var(--aifred-warning-bg); --icon-own: currentColor; }
.controls { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 0.85rem; }
.controls label { display: inline-flex; align-items: center; gap: 6px; }
.kind .swatch, .kind-help .swatch { width: 14px; height: 14px; border-radius: 3px; border: 1px solid var(--aifred-border-light); flex-shrink: 0; }
.threshold { flex: 1; min-width: 18em; }
.threshold input { flex: 1; }
.number { font-variant-numeric: tabular-nums; min-width: 4.2em; text-align: right; }
.over { color: var(--aifred-danger); }
.under { color: var(--aifred-accent-success); }
.muted { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
input[type="radio"], input[type="checkbox"] { accent-color: var(--aifred-primary); }
input[type="range"] { accent-color: var(--aifred-primary); }
button { padding: 4px 12px; font-size: 0.85rem; }
button.primary { font-weight: 600; }
/* The canvas gives the stage its size (its own width and height keep the picture's shape); the picture lies under it. */
.stage { position: relative; max-width: 100%; line-height: 0; background: var(--aifred-media-bg); border: 1px solid var(--aifred-border); border-radius: 8px; overflow: hidden; }
.stage img { position: absolute; inset: 0; width: 100%; height: 100%; }
.stage canvas { position: relative; display: block; width: 100%; height: auto; cursor: crosshair; touch-action: none; }
.status { margin: 0; font-size: 0.85rem; min-height: 1.2em; }
.status.failed { color: var(--aifred-danger); }
.unsaved { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--aifred-accent-amber); border-radius: 8px; font-size: 0.85rem; }
.unsaved span { flex: 1; }
</style>
