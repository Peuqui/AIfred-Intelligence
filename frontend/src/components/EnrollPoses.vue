<script setup lang="ts">
// Learning a person from several poses in front of a camera: for a new person (its name) or more poses of one. The
// camera's live picture (its tele lens where it has one), the instruction for the pose asked for, capture, take the
// last one back, skip; the crops taken so far; save (the person made if new, the recognition knows the poses at once)
// or cancel. The session, its crops and embeddings live in the server; this dialog shows them.
import { Camera, Hourglass, TriangleAlert } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ApiError, api, apiUrl } from "../api/client";
import type { CameraRow, EnrollSession, LiveView } from "../api/types";
import { formatNumber, t } from "../i18n";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ faceId: number; name: string }>();
const emit = defineEmits<{ close: []; saved: [message: string] }>();
const session = ref<EnrollSession | null>(null);
const cameras = ref<CameraRow[]>([]);
const source = ref("");
const name = ref(props.name);
const notice = ref("");
const busy = ref(false);
const tick = ref(0);
let waiting: number | null = null;

async function call(work: () => Promise<EnrollSession>): Promise<void> {
  notice.value = "";
  try {
    session.value = await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const post = (path: string, body: object = {}): Promise<EnrollSession> =>
  api<EnrollSession>(`vision/enroll/${session.value?.token}${path}`, { method: "POST", body: JSON.stringify(body) });

onMounted(async () => {
  await call(() => api<EnrollSession>("vision/enroll", { method: "POST", body: JSON.stringify({ face_id: props.faceId }) }));
  try {
    cameras.value = (await api<LiveView>("vision/live")).cameras;
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
  source.value = cameras.value.find((camera) => camera.available)?.id ?? "";
});
onBeforeUnmount(() => {
  if (waiting !== null) window.clearTimeout(waiting);
});

// The next live picture once this one has arrived, after the interval the server names.
const preview = computed(() => (source.value ? apiUrl(`vision/enroll/preview/${source.value}?n=${tick.value}`) : ""));
function arrived(): void {
  if (waiting === null) waiting = window.setTimeout(() => {
    waiting = null;
    if (!busy.value) tick.value += 1;
  }, session.value?.preview_interval_ms ?? 0);
}
const instruction = computed(() => {
  const at = session.value?.poses[session.value.step];
  return at ? at.label : t("multipose_pose_done");
});
const progress = computed(() => session.value ? `${formatNumber(Math.min(session.value.step + 1, session.value.poses.length))} / ${formatNumber(session.value.poses.length)}` : "");

async function capture(): Promise<void> {
  busy.value = true;
  await call(() => post("/capture", { source_id: source.value }));
  busy.value = false;
  tick.value += 1;
}
async function undo(): Promise<void> {
  await call(() => api<EnrollSession>(`vision/enroll/${session.value?.token}/captures/last`, { method: "DELETE" }));
}
async function save(): Promise<void> {
  notice.value = "";
  try {
    const done = await api<{ message: string }>(`vision/enroll/${session.value?.token}/finish`, { method: "POST", body: JSON.stringify({ name: name.value }) });
    emit("saved", done.message);
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
// The dialog closes at once; the server ends the session meanwhile (a failure shows in the console).
function cancel(): void {
  if (session.value) void api(`vision/enroll/${session.value.token}`, { method: "DELETE" });
  emit("close");
}
const canSave = computed(() => Boolean(session.value?.captures.length) && (props.faceId > 0 || Boolean(name.value.trim())));
</script>

<template>
  <ModalDialog @close="cancel">
    <div class="enroll">
      <header>
        <strong>{{ t("multipose_title") }}</strong>
        <span v-if="faceId" class="who">{{ name }}</span>
      </header>
      <p class="hint">{{ t("multipose_subtitle") }}</p>
      <label v-if="!faceId" class="line">{{ t("multipose_name_label") }}<input v-model="name" :placeholder="t('multipose_name_placeholder')" /></label>
      <label class="line">{{ t("multipose_source_label") }}
        <select v-model="source">
          <option v-for="camera in cameras" :key="camera.id" :value="camera.id" :disabled="!camera.available">{{ camera.label }}</option>
        </select>
      </label>
      <img v-if="preview" class="live" :src="preview" alt="" @load="arrived" @error="arrived" />
      <p v-else class="hint">{{ t("multipose_no_preview") }}</p>
      <p v-if="session" class="pose"><span class="step">{{ progress }}</span> {{ instruction }}</p>
      <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
      <div v-if="session?.captures.length" class="captures">
        <span class="hint">{{ t("multipose_captures_label") }}</span>
        <img v-for="taken in session.captures" :key="taken.url" :src="apiUrl(taken.url)" :title="`${taken.pose} · ${formatNumber(taken.score, 2)}`" alt="" />
      </div>
      <div class="actions">
        <button :disabled="busy || !source || !session" @click="capture"><Hourglass v-if="busy" :size="16" /><Camera v-else :size="16" /> {{ t("multipose_capture") }}</button>
        <button :disabled="busy || !session?.captures.length" @click="undo">{{ t("multipose_retry") }}</button>
        <button :disabled="busy || !session || session.step >= session.poses.length" @click="call(() => post('/skip'))">{{ t("multipose_skip") }}</button>
        <span class="grow"></span>
        <button @click="cancel">{{ t("multipose_cancel") }}</button>
        <button class="primary" :disabled="busy || !canSave" @click="save">{{ t("multipose_save") }}</button>
      </div>
    </div>
  </ModalDialog>
</template>

<style scoped>
.enroll { display: flex; flex-direction: column; gap: 10px; width: min(720px, calc(90 * var(--vw))); }
header { display: flex; align-items: baseline; gap: 10px; }
.who { color: var(--aifred-text-secondary); }
.line { display: flex; align-items: center; gap: 10px; font-size: 0.9rem; }
.live { width: 100%; max-height: calc(50 * var(--dvh)); object-fit: contain; border-radius: 8px; border: 1px solid var(--aifred-border); background: #000; }
.pose { margin: 0; font-size: 1rem; }
.step { font-size: 0.8rem; color: var(--aifred-text-muted); margin-right: 6px; }
.captures { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.captures img { width: 56px; height: 56px; object-fit: cover; border-radius: 4px; border: 1px solid var(--aifred-border); }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.grow { flex: 1; }
select, input { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 10px; font: inherit; font-size: 0.88rem; }
button { padding: 4px 10px; font-size: 0.88rem; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
</style>
