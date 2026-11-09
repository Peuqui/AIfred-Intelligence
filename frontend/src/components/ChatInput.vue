<script setup lang="ts">
import { Music } from "lucide-vue-next";
import { computed, nextTick, ref, watch } from "vue";
import type { Settings, SettingsUpdate, UploadedImage } from "../api/types";
import { language, t } from "../i18n";
import { enterSends } from "../lib/devicePrefs";
import * as recorder from "../lib/recorder";
import { speaking } from "../lib/speech";
import DictationBadge, { type BadgePhase } from "./DictationBadge.vue";
import DictationMenu from "./DictationMenu.vue";

const props = defineProps<{
  running: boolean;
  queued: number;
  images: UploadedImage[];
  uploading: boolean;
  transcribing: boolean;
  settings: Settings;
}>();
const emit = defineEmits<{
  send: [text: string];
  stop: [];
  upload: [files: File[]];
  removeImage: [name: string];
  editImage: [index: number];
  dictate: [file: File, source: "mic" | "file"];
  heard: [text: string];
  settings: [changes: SettingsUpdate];
}>();
// The input field's text lives in the chat store: a dictation in edit mode lands there.
const text = defineModel<string>({ required: true });

// The field grows with its text (typed, dictated, pasted) from its three rows up to its max-height, then it scrolls.
const field = ref<HTMLTextAreaElement | null>(null);
watch(
  text,
  async () => {
    await nextTick();
    const box = field.value;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight + box.offsetHeight - box.clientHeight}px`;
  },
  { immediate: true },
);
const picker = ref<HTMLInputElement | null>(null);
const audioPicker = ref<HTMLInputElement | null>(null);
const recording = recorder.recording;
const badgePhase = computed<BadgePhase>(() => (recording.value ? "listening" : props.transcribing ? "transcribing" : "ready"));
const canSend = computed(() => text.value.trim() !== "" || props.images.length > 0);

function send(): void {
  if (!canSend.value) return;
  emit("send", text.value.trim());
  text.value = "";
}

// Enter sends and Shift+Enter breaks the line, or (this device's choice) Enter breaks the line and the button sends.
function onKey(event: KeyboardEvent): void {
  if (enterSends.value && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}

// Images come from the picker (on a phone it offers the camera too), dropped onto the input or pasted.
function upload(files: FileList | null | undefined): void {
  const images = [...(files ?? [])].filter((file) => file.type.startsWith("image/"));
  if (images.length) emit("upload", images);
}

function picked(event: Event): void {
  const input = event.target as HTMLInputElement;
  upload(input.files);
  input.value = "";
}

// The dictation goes to the STT-Suite as a recording, or the browser's own recognition listens, as the
// dictation settings say. Neither stands in for the other by itself.
async function toggleRecording(): Promise<void> {
  if (recording.value) {
    recorder.stop();
    return;
  }
  try {
    if (props.settings.dictation_engine === "browser") {
      recorder.startBrowser(language.value, (text) => emit("heard", text), (error) => window.alert(`${t("dictation_browser_failed")} (${error})`));
    } else {
      await recorder.start((file) => emit("dictate", file, "mic"));
    }
  } catch (error) {
    const reason = error instanceof Error ? error.message : "";
    window.alert(
      reason === "unsupported" ? t("dictation_browser_unsupported") : reason === "insecure" ? t("mic_needs_secure_origin") : t("mic_access_denied"),
    );
  }
}

function audioPicked(event: Event): void {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) emit("dictate", file, "file");
  input.value = "";
}
</script>

<template>
  <div class="input" @dragover.prevent @drop.prevent="upload($event.dataTransfer?.files)">
    <div v-if="images.length || uploading" class="pending">
      <figure v-for="(image, index) in images" :key="image.name">
        <img :src="image.url" :alt="image.name" />
        <button class="remove" :title="t('remove_image')" @click="emit('removeImage', image.name)">×</button>
        <button class="edit" :title="t('crop_tooltip')" @click="emit('editImage', index)">✂️</button>
      </figure>
      <span v-if="uploading" class="uploading">{{ t("image_uploading") }}</span>
    </div>
    <textarea
      ref="field"
      v-model="text"
      rows="3"
      :placeholder="t('text_input_placeholder')"
      @keydown="onKey"
      @paste="upload($event.clipboardData?.files)"
    />
    <div class="actions">
      <input ref="picker" type="file" accept="image/*" multiple hidden @change="picked" />
      <input ref="audioPicker" type="file" accept="audio/*,.webm" hidden @change="audioPicked" />
      <button class="tool" :title="t('upload_image')" @click="picker?.click()">🖼️</button>
      <button
        class="mic"
        :disabled="transcribing && !recording"
        :aria-label="recording ? t('dictation_stop') : t('dictation_record')"
        :title="recording ? t('dictation_stop') : t('dictation_record')"
        @click="toggleRecording"
      >
        <DictationBadge :phase="badgePhase" />
      </button>
      <DictationMenu :settings="settings" @change="(changes) => emit('settings', changes)" />
      <button class="tool" :title="t('audio_file_upload')" @click="audioPicker?.click()"><Music :size="20" /></button>
      <span v-if="transcribing" class="transcribing">{{ t("dictation_running") }}</span>
      <span v-if="queued" class="queued">{{ t("messages_waiting", { count: queued }) }}</span>
      <label class="enter" :title="t('enter_sends_hint')">
        <input v-model="enterSends" type="checkbox" />
        {{ t("enter_sends") }}
      </label>
      <!-- The chat's tools (ChatView) and the send button as one group: it wraps as a whole, never the send button
           alone on a line of its own. -->
      <span class="send-group">
        <slot name="tools" />
        <button v-if="running || speaking" class="stop" @click="emit('stop')">⏹ {{ t("stop_turn") }}</button>
        <button class="send" :disabled="!canSend" @click="send">{{ t("send_text") }}</button>
      </span>
    </div>
  </div>
</template>

<style scoped>
.input { display: flex; flex-direction: column; gap: 6px; }
textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  max-height: calc(40 * var(--vh));
  overflow-y: auto;
  /* What one writes in the size of what one reads: the chat's (A−/A+), the interface's taken out again. */
  zoom: calc(var(--chat-zoom) / var(--ui-zoom));
  padding: 10px;
  font: inherit;
  background: var(--aifred-input-bg);
  color: var(--aifred-text-primary);
  border: 1px solid var(--aifred-border);
  border-radius: 8px;
}
.pending { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
figure { position: relative; margin: 0; }
figure img { width: 56px; height: 56px; object-fit: cover; border-radius: 6px; border: 1px solid var(--aifred-border); display: block; }
.remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 20px;
  height: 20px;
  padding: 0;
  border-radius: 50%;
  line-height: 18px;
  background: var(--aifred-danger);
  color: var(--aifred-text-on-accent);
}
.edit {
  position: absolute;
  bottom: -6px;
  right: -6px;
  width: 22px;
  height: 22px;
  padding: 0;
  border-radius: 50%;
  font-size: var(--text-small);
  line-height: 20px;
  background: var(--aifred-card-bg);
  border: 1px solid var(--aifred-border-light);
}
.uploading { font-size: var(--text-small); color: var(--aifred-text-muted); }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.send-group { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; margin-left: auto; }
.hint, .queued { font-size: var(--text-small); color: var(--aifred-text-muted); margin-right: auto; }
.enter { display: flex; align-items: center; gap: 4px; font-size: var(--text-small); color: var(--aifred-text-muted); margin-right: auto; cursor: pointer; }
.enter input { accent-color: var(--aifred-primary); }
.queued { color: var(--aifred-accent-amber); margin-right: 0; }
button { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font: inherit; }
/* An icon in the text's colour: the 🎵 emoji is drawn black on Android and vanished on the dark page. */
.tool { display: inline-flex; align-items: center; background: none; border: 1px solid var(--aifred-border); padding: 4px 10px; color: var(--aifred-text-primary); }
.mic { background: none; border: none; padding: 0; width: 40px; height: 40px; display: inline-flex; }
.mic svg { width: 100%; height: 100%; }
.mic:disabled { cursor: default; }
.transcribing { font-size: var(--text-small); color: var(--aifred-accent-cyan); }
.send { background: var(--aifred-primary); color: var(--aifred-text-on-accent); }
.send:disabled { opacity: 0.5; cursor: default; }
.stop { background: var(--aifred-danger); color: var(--aifred-text-on-accent); }
</style>
