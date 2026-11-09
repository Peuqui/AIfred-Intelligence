<script setup lang="ts">
import { Send, Square, X } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import type { Settings, UploadedImage } from "../api/types";
import { language, t } from "../i18n";
import { enterSends } from "../lib/devicePrefs";
import * as recorder from "../lib/recorder";
import { speaking } from "../lib/speech";
import AttachMenu from "./AttachMenu.vue";
import DictationBadge, { type BadgePhase } from "./DictationBadge.vue";

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

// Whatever comes through the paperclip, is dropped onto the input or pasted, by its kind: pictures are attached to the
// message, audio files are transcribed (each a dictation of its own). Other kinds the chat does not take yet.
function attach(files: FileList | File[] | null | undefined): void {
  const all = [...(files ?? [])];
  const images = all.filter((file) => file.type.startsWith("image/"));
  const audio = all.filter((file) => file.type.startsWith("audio/") || file.name.endsWith(".webm"));
  const other = all.filter((file) => !images.includes(file) && !audio.includes(file));
  if (images.length) emit("upload", images);
  for (const file of audio) emit("dictate", file, "file");
  if (other.length) window.alert(t("attach_unsupported", { names: other.map((file) => file.name).join(", ") }));
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

// While a dictation is taken in, the × in the paperclip's place or Escape throws it away (as in Agent-Orc).
function discardOnEscape(event: KeyboardEvent): void {
  if (event.key === "Escape") recorder.cancel();
}
watch(recording, (on) => (on ? window.addEventListener("keydown", discardOnEscape) : window.removeEventListener("keydown", discardOnEscape)));
onBeforeUnmount(() => window.removeEventListener("keydown", discardOnEscape));
</script>

<template>
  <div class="input" @dragover.prevent @drop.prevent="attach($event.dataTransfer?.files)">
    <div v-if="images.length || uploading" class="pending">
      <figure v-for="(image, index) in images" :key="image.name">
        <img :src="image.url" :alt="image.name" />
        <button class="remove" :title="t('remove_image')" @click="emit('removeImage', image.name)">×</button>
        <button class="edit" :title="t('crop_tooltip')" @click="emit('editImage', index)">✂️</button>
      </figure>
      <span v-if="uploading" class="uploading">{{ t("image_uploading") }}</span>
    </div>
    <p v-if="queued" class="queued">{{ t("messages_waiting", { count: queued }) }}</p>
    <!-- As in Agent-Orc: paperclip (× while a dictation is taken in), microphone, field, send; the buttons one line
         high, the field grows with its text. Stop shows while a turn runs or a reading plays. -->
    <div class="field-row">
      <button v-if="recording" class="discard" :title="t('dictation_cancel')" :aria-label="t('dictation_cancel')" @click="recorder.cancel()">
        <X :size="22" />
      </button>
      <AttachMenu v-else @files="attach" />
      <span class="mic-box">
        <button
          class="mic"
          :disabled="transcribing && !recording"
          :aria-label="transcribing && !recording ? t('dictation_running') : recording ? t('dictation_stop') : t('dictation_record')"
          :title="transcribing && !recording ? t('dictation_running') : recording ? t('dictation_stop') : t('dictation_record')"
          @click="toggleRecording"
        >
          <DictationBadge :phase="badgePhase" />
        </button>
        <!-- While a dictation is transcribed: Agent-Orc's spinner at the microphone's lower right. -->
        <span v-if="transcribing" class="spinner" role="status" :aria-label="t('dictation_running')" />
      </span>
      <textarea
        ref="field"
        v-model="text"
        rows="1"
        :placeholder="t('text_input_placeholder')"
        @keydown="onKey"
        @paste="attach($event.clipboardData?.files)"
      />
      <button v-if="running || speaking" class="line-button stop" :title="t('stop_turn')" :aria-label="t('stop_turn')" @click="emit('stop')">
        <Square :size="18" />
      </button>
      <button class="line-button send" :disabled="!canSend" :title="t('send_text')" :aria-label="t('send_text')" @click="send">
        <Send :size="20" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.input { display: flex; flex-direction: column; gap: 6px; }
/* Paperclip, microphone, field and the buttons in the size of what one reads (the chat's A−/A+, the interface's taken
   out again), the buttons exactly as high as the field's one line: from the same line height, padding and border
   (--line-size, also the paperclip's in AttachMenu; its em is the field's). */
.field-row {
  --field-line: 1.4;
  --field-pad: 10px;
  --field-border: 1px;
  --line-size: calc(var(--field-line) * 1em + 2 * var(--field-pad) + 2 * var(--field-border));
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
figure button { cursor: pointer; font: inherit; border: none; }
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
.queued { margin: 0; font-size: var(--text-small); color: var(--aifred-accent-amber); }
.mic-box { position: relative; display: inline-flex; flex: none; }
.mic, .discard, .line-button { flex: none; width: var(--line-size); height: var(--line-size); padding: 0; font-size: inherit; cursor: pointer; }
.mic { display: inline-flex; background: none; border: none; }
.mic svg { width: 100%; height: 100%; }
.mic:disabled { cursor: default; }
/* In the working colour, which tells it from the red rim of the recording microphone beside it (as in Agent-Orc). */
.discard { display: inline-flex; align-items: center; justify-content: center; background: none; border: none; color: var(--aifred-primary); }
/* Agent-Orc's spinner: an arc that turns in four steps a turn rather than smoothly. */
.spinner {
  position: absolute; right: -2px; bottom: -2px; width: 14px; height: 14px; box-sizing: border-box; pointer-events: none;
  border-radius: 50%; border: 2px solid var(--aifred-primary); border-top-color: transparent;
  background: var(--aifred-page-bg); animation: spin 2s steps(4) infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
/* Send and stop: an icon in its colour, framed (the look of the Reflex page's action buttons). */
.line-button {
  display: inline-flex; align-items: center; justify-content: center; border-radius: 6px;
  border: 1px solid var(--line-color); color: var(--line-color); background: color-mix(in srgb, var(--line-color) 22%, transparent);
}
.line-button:hover:not(:disabled) { background: color-mix(in srgb, var(--line-color) 38%, transparent); }
.line-button:disabled { opacity: 0.45; cursor: default; }
.send { --line-color: var(--aifred-accent-warning); }
.stop { --line-color: var(--aifred-danger); }
</style>
