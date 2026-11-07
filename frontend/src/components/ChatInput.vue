<script setup lang="ts">
import { computed, ref } from "vue";
import type { UploadedImage } from "../api/types";
import { t } from "../i18n";
import { speaking } from "../lib/speech";

const props = defineProps<{ running: boolean; queued: number; images: UploadedImage[]; uploading: boolean }>();
const emit = defineEmits<{ send: [text: string]; stop: []; upload: [files: File[]]; removeImage: [name: string] }>();
const text = ref("");
const picker = ref<HTMLInputElement | null>(null);
const canSend = computed(() => text.value.trim() !== "" || props.images.length > 0);

function send(): void {
  if (!canSend.value) return;
  emit("send", text.value.trim());
  text.value = "";
}

// Enter sends, Shift+Enter breaks the line (as in the Reflex UI).
function onKey(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
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
</script>

<template>
  <div class="input" @dragover.prevent @drop.prevent="upload($event.dataTransfer?.files)">
    <div v-if="images.length || uploading" class="pending">
      <figure v-for="image in images" :key="image.name">
        <img :src="image.url" :alt="image.name" />
        <button class="remove" :title="t('remove_image')" @click="emit('removeImage', image.name)">×</button>
      </figure>
      <span v-if="uploading" class="uploading">{{ t("image_uploading") }}</span>
    </div>
    <textarea
      v-model="text"
      rows="3"
      :placeholder="t('text_input_placeholder')"
      @keydown="onKey"
      @paste="upload($event.clipboardData?.files)"
    />
    <div class="actions">
      <input ref="picker" type="file" accept="image/*" multiple hidden @change="picked" />
      <button class="image" :title="t('upload_image')" @click="picker?.click()">🖼️</button>
      <span v-if="queued" class="queued">{{ t("messages_waiting", { count: queued }) }}</span>
      <span class="hint">{{ t("enter_sends_hint") }}</span>
      <button v-if="running || speaking" class="stop" @click="emit('stop')">⏹ {{ t("stop_turn") }}</button>
      <button class="send" :disabled="!canSend" @click="send">{{ t("send_text") }}</button>
    </div>
  </div>
</template>

<style scoped>
.input { display: flex; flex-direction: column; gap: 6px; }
textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
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
.uploading { font-size: 0.75rem; color: var(--aifred-text-muted); }
.actions { display: flex; align-items: center; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.hint, .queued { font-size: 0.75rem; color: var(--aifred-text-muted); margin-right: auto; }
.queued { color: var(--aifred-accent-amber); margin-right: 0; }
button { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font: inherit; }
.image { background: none; border: 1px solid var(--aifred-border); padding: 4px 10px; }
.send { background: var(--aifred-primary); color: var(--aifred-text-on-accent); }
.send:disabled { opacity: 0.5; cursor: default; }
.stop { background: var(--aifred-danger); color: var(--aifred-text-on-accent); }
</style>
