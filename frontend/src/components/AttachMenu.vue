<script setup lang="ts">
// The paperclip beside the microphone, as in Agent-Orc (AttachMenu.vue there): take a photo, capture the screen, choose
// files. Every file goes to the input (``files``), which knows what to do with each kind (ChatInput.attach): a picture
// is attached to the message, an audio file is transcribed.
import { Camera, FileUp, Monitor, Paperclip } from "lucide-vue-next";
import { ref } from "vue";
import { t } from "../i18n";
import { closeWhenBeside } from "../lib/dismiss";

const emit = defineEmits<{ files: [files: File[]] }>();
const open = ref(false);
const box = ref<HTMLElement | null>(null);
closeWhenBeside(open, box);

const photoInput = ref<HTMLInputElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
// Capturing the screen is a desktop browser's feature; phone browsers have none.
const screenCaptureSupported = typeof navigator.mediaDevices?.getDisplayMedia === "function";
const SCREENSHOT_NAME = "screenshot.png";

function choose(input: HTMLInputElement | null): void {
  open.value = false;
  input?.click();
}

function chosen(event: Event): void {
  const input = event.target as HTMLInputElement;
  const files = [...(input.files ?? [])];
  // Cleared, so choosing the same file again still counts as a change.
  input.value = "";
  if (files.length) emit("files", files);
}

/** The browser asks which screen, window or tab; its current picture is attached. */
async function captureScreen(): Promise<void> {
  open.value = false;
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
  } catch (error) {
    // The user closed the browser's choice: nothing to attach, nothing to report.
    if (!(error instanceof DOMException && error.name === "NotAllowedError")) window.alert(`${t("attach_screenshot_failed")} (${String(error)})`);
    return;
  }
  const video = document.createElement("video");
  video.srcObject = stream;
  video.muted = true;
  await video.play();
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d")?.drawImage(video, 0, 0);
  for (const track of stream.getTracks()) track.stop();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (blob) emit("files", [new File([blob], SCREENSHOT_NAME, { type: "image/png" })]);
}
</script>

<template>
  <span ref="box" class="attach">
    <button class="trigger" :title="t('attach_title')" :aria-label="t('attach_title')" :aria-expanded="open" @click="open = !open">
      <Paperclip :size="20" />
    </button>
    <input ref="photoInput" type="file" accept="image/*" capture="environment" hidden @change="chosen" />
    <input ref="fileInput" type="file" accept="image/*,audio/*,.webm" multiple hidden @change="chosen" />
    <div v-if="open" class="panel" role="menu" :aria-label="t('attach_title')">
      <button role="menuitem" @click="choose(photoInput)"><Camera :size="18" />{{ t("attach_photo") }}</button>
      <button v-if="screenCaptureSupported" role="menuitem" @click="captureScreen"><Monitor :size="18" />{{ t("attach_screenshot") }}</button>
      <button role="menuitem" @click="choose(fileInput)"><FileUp :size="18" />{{ t("attach_file") }}</button>
      <p class="hint">{{ t("attach_file_hint") }}</p>
    </div>
  </span>
</template>

<style scoped>
.attach { position: relative; display: inline-flex; flex: none; }
/* As high as the field's one line (ChatInput's --line-size). */
.trigger {
  display: inline-flex; align-items: center; justify-content: center; width: var(--line-size); height: var(--line-size);
  padding: 0; font-size: inherit; background: none; border: none; color: var(--aifred-text-primary); cursor: pointer;
}
.panel {
  position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 10; width: 240px; padding: 4px;
  display: flex; flex-direction: column; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light);
  border-radius: 8px; font-size: var(--text-small);
}
.panel button {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px; text-align: left; background: none; border: none;
  border-radius: 6px; color: var(--aifred-text-primary); font: inherit; cursor: pointer;
}
.panel button:hover { background: var(--aifred-input-bg); }
.hint { margin: 2px 10px 6px; color: var(--aifred-text-muted); }
</style>
