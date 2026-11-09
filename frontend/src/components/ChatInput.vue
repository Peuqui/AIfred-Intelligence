<script setup lang="ts">
import { Music, Send, Square } from "lucide-vue-next";
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
    <!-- The microphone left of the field, as in Agent-Orc: both one line high, the field grows with its text. -->
    <div class="field-row">
      <button
        class="mic"
        :disabled="transcribing && !recording"
        :aria-label="recording ? t('dictation_stop') : t('dictation_record')"
        :title="recording ? t('dictation_stop') : t('dictation_record')"
        @click="toggleRecording"
      >
        <DictationBadge :phase="badgePhase" />
      </button>
      <textarea
        ref="field"
        v-model="text"
        rows="1"
        :placeholder="t('text_input_placeholder')"
        @keydown="onKey"
        @paste="upload($event.clipboardData?.files)"
      />
    </div>
    <div class="actions">
      <input ref="picker" type="file" accept="image/*" multiple hidden @change="picked" />
      <input ref="audioPicker" type="file" accept="audio/*,.webm" hidden @change="audioPicked" />
      <button class="tool" :title="t('upload_image')" @click="picker?.click()">🖼️</button>
      <DictationMenu :settings="settings" @change="(changes) => emit('settings', changes)" />
      <button class="tool" :title="t('audio_file_upload')" @click="audioPicker?.click()"><Music :size="20" /></button>
      <!-- The chat's own tools (ChatView): help, the agents' choices, read aloud anew. -->
      <slot name="tools" />
      <span v-if="transcribing" class="transcribing">{{ t("dictation_running") }}</span>
      <span v-if="queued" class="queued">{{ t("messages_waiting", { count: queued }) }}</span>
      <label class="enter" :title="t('enter_sends_hint')">
        <input v-model="enterSends" type="checkbox" />
        {{ t("enter_sends") }}
      </label>
    </div>
    <!-- Sending and what one does with the chat (ChatView), in one row as under Reflex. -->
    <div class="action-row">
      <button v-if="running || speaking" class="bar-button red" :title="t('stop_turn')" @click="emit('stop')">
        <Square :size="16" /><span class="label">{{ t("stop_turn") }}</span>
      </button>
      <button class="bar-button send wide" :disabled="!canSend" :title="t('send_text')" @click="send">
        <Send :size="18" /><span class="label">{{ t("send_text") }}</span>
      </button>
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.input { display: flex; flex-direction: column; gap: 6px; }
/* Microphone and field in the size of what one reads (the chat's A−/A+, the interface's taken out again), and the
   microphone exactly as high as the field's one line: both from the same line height, padding and border. */
.field-row {
  --field-line: 1.4;
  --field-pad: 10px;
  --field-border: 1px;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  zoom: calc(var(--chat-zoom) / var(--ui-zoom));
}
textarea {
  flex: 1;
  min-width: 0;
  box-sizing: border-box;
  resize: vertical;
  max-height: calc(40 * var(--vh));
  overflow-y: auto;
  padding: var(--field-pad);
  font: inherit;
  line-height: var(--field-line);
  background: var(--aifred-input-bg);
  color: var(--aifred-text-primary);
  border: var(--field-border) solid var(--aifred-border);
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
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
/* The query container of .bar-button (App.vue): its words show only where the row has room. */
.action-row { display: flex; gap: 8px; container: action-row / inline-size; }
.enter { display: flex; align-items: center; gap: 4px; font-size: var(--text-small); color: var(--aifred-text-muted); margin-left: auto; cursor: pointer; }
.enter input { accent-color: var(--aifred-primary); }
.queued { font-size: var(--text-small); color: var(--aifred-accent-amber); }
/* Not the action row's buttons: they take their look from .bar-button (App.vue). :where keeps this as weak as a plain
   button selector, so .tool and .mic below still win. */
:where(button:not(.bar-button)) { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font: inherit; }
/* An icon in the text's colour: the 🎵 emoji is drawn black on Android and vanished on the dark page. */
.tool { display: inline-flex; align-items: center; background: none; border: 1px solid var(--aifred-border); padding: 4px 10px; color: var(--aifred-text-primary); }
.mic {
  --mic-size: calc(var(--field-line) * 1em + 2 * var(--field-pad) + 2 * var(--field-border));
  flex: none; background: none; border: none; padding: 0; width: var(--mic-size); height: var(--mic-size); display: inline-flex;
}
.mic svg { width: 100%; height: 100%; }
.mic:disabled { cursor: default; }
.transcribing { font-size: var(--text-small); color: var(--aifred-accent-cyan); }
</style>
