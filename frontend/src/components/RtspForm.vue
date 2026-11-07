<script setup lang="ts">
// A network camera's connection: new (with its name) or changed (the name stays, it is the base of the source id).
// User and password are never shown; left empty when changing, the stored ones stay. Fields the form does not
// show stay in the camera's entry (the server merges).
import { reactive, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { RtspCamera, RtspCameras } from "../api/types";
import { t } from "../i18n";

const props = defineProps<{ camera: RtspCamera | null; profiles: string[] }>();
const emit = defineEmits<{ saved: [RtspCameras]; cancel: [] }>();
const camera = props.camera;
const form = reactive({
  name: "",
  host: camera?.host ?? "",
  port: camera?.port ?? 554,
  path: camera?.path ?? "",
  cred: camera?.cred ?? "",
  profile: camera?.profile ?? props.profiles[0] ?? "webcam",
  api_port: camera?.api_port ?? 443,
  face_channel: camera?.face_channel ?? null,
  onvif_port: camera?.onvif_port ?? null,
  snap_channel: camera?.snap_channel ?? null,
  ptz: camera?.ptz ?? false,
  user: "",
  password: "",
});
const notice = ref("");
const deleting = ref(false);

async function save(): Promise<void> {
  notice.value = "";
  const { host, port, path, cred, profile, ptz, user, password } = form;
  // The fields of a camera with its own detection only with that profile (a plain stream has no API port).
  const detection = profile === "ai_camera"
    ? { api_port: form.api_port, face_channel: form.face_channel, snap_channel: form.snap_channel, onvif_port: form.onvif_port }
    : {};
  const connection = { host, port, path, cred, profile, ptz, user, password, ...detection };
  const body = camera ? connection : { name: form.name, ...connection };
  try {
    emit("saved", await api<RtspCameras>(camera ? `vision/rtsp-cameras/${encodeURIComponent(camera.name)}` : "vision/rtsp-cameras", {
      method: camera ? "PUT" : "POST",
      body: JSON.stringify(body),
    }));
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}

async function remove(): Promise<void> {
  if (!camera) return;
  try {
    emit("saved", await api<RtspCameras>(`vision/rtsp-cameras/${encodeURIComponent(camera.name)}`, { method: "DELETE" }));
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const optional = (event: Event): number | null => ((event.target as HTMLInputElement).value === "" ? null : Number((event.target as HTMLInputElement).value));
</script>

<template>
  <form class="rtsp" @submit.prevent="save">
    <strong>{{ t(camera ? "vision_rtsp_form_edit_header" : "vision_rtsp_form_new_header") }}</strong>
    <div class="grid">
      <label v-if="!camera">{{ t("vision_rtsp_field_name") }}<input v-model="form.name" required /></label>
      <label>{{ t("vision_rtsp_field_host") }}<input v-model="form.host" required /></label>
      <label>{{ t("vision_rtsp_field_port") }}<input v-model.number="form.port" type="number" min="1" max="65535" /></label>
      <label>{{ t("vision_rtsp_field_path") }}<input v-model="form.path" /></label>
      <label>{{ t("vision_rtsp_field_profile") }}
        <select v-model="form.profile"><option v-for="profile in profiles" :key="profile" :value="profile">{{ profile }}</option></select>
      </label>
      <label>{{ t("vision_rtsp_field_cred") }}<input v-model="form.cred" pattern="[a-z0-9_]*" /></label>
      <label>{{ t("vision_rtsp_field_user") }}<input v-model="form.user" autocomplete="off" :placeholder="camera?.user_set ? '••••' : ''" /></label>
      <label>{{ t("vision_rtsp_field_password") }}<input v-model="form.password" type="password" autocomplete="new-password" :placeholder="camera?.password_set ? '••••' : ''" /></label>
      <template v-if="form.profile === 'ai_camera'">
        <label>{{ t("vision_rtsp_field_apiport") }}<input v-model.number="form.api_port" type="number" min="1" max="65535" /></label>
        <label>{{ t("vision_rtsp_field_facechannel") }}<input :value="form.face_channel ?? ''" type="number" min="0" max="15" @input="form.face_channel = optional($event)" /></label>
        <label>{{ t("vision_rtsp_field_snapchannel") }}<input :value="form.snap_channel ?? ''" type="number" min="0" max="15" @input="form.snap_channel = optional($event)" /></label>
        <label>{{ t("vision_rtsp_field_onvifport") }}<input :value="form.onvif_port ?? ''" type="number" min="1" max="65535" @input="form.onvif_port = optional($event)" /></label>
      </template>
      <label class="check"><input v-model="form.ptz" type="checkbox" /> {{ t("vision_rtsp_field_ptz") }}</label>
    </div>
    <p class="hint">{{ t("vision_rtsp_cred_hint") }}</p>
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>
    <div class="actions">
      <template v-if="camera">
        <button v-if="!deleting" type="button" class="danger" @click="deleting = true">🗑️ {{ t("vision_rtsp_delete") }}</button>
        <template v-else>
          <span class="confirm">{{ t("vision_rtsp_delete_confirm") }}</span>
          <button type="button" @click="deleting = false">{{ t("vision_rtsp_cancel") }}</button>
          <button type="button" class="danger" @click="remove">{{ t("vision_rtsp_delete") }}</button>
        </template>
      </template>
      <span class="space"></span>
      <button type="button" @click="emit('cancel')">{{ t("vision_rtsp_cancel") }}</button>
      <button type="submit">{{ t("vision_rtsp_save") }}</button>
    </div>
  </form>
</template>

<style scoped>
.rtsp { display: flex; flex-direction: column; gap: 8px; padding: 10px; border: 1px dashed var(--aifred-border); border-radius: 8px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
label { display: flex; flex-direction: column; gap: 3px; font-size: 0.8rem; color: var(--aifred-text-secondary); }
label.check { flex-direction: row; align-items: center; gap: 6px; }
input, select, button { background: var(--aifred-input-bg); color: var(--aifred-text-primary); border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; }
button { cursor: pointer; }
.danger { border-color: var(--aifred-danger); color: var(--aifred-danger); }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.space { flex: 1; }
.confirm { font-size: 0.85rem; color: var(--aifred-accent-amber); }
.hint { margin: 0; font-size: 0.75rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
