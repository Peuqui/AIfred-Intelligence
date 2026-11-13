<script setup lang="ts">
// An image of the next message, turned or cut before it is sent (as the crop dialog of the Reflex UI):
// the box with its grips lies over the image, its position goes to the server in percent of the image.
import { Check, Crop, RotateCcw, RotateCw } from "lucide-vue-next";
import { computed, ref, watch } from "vue";
import type { UploadedImage } from "../api/types";
import { t } from "../i18n";
import { GRIPS, WHOLE, pulled, type Box, type Grip } from "../lib/cropBox";
import ModalDialog from "./ModalDialog.vue";

const props = defineProps<{ image: UploadedImage; busy: boolean }>();
const emit = defineEmits<{ rotate: [clockwise: boolean]; crop: [box: Box]; close: [] }>();

const box = ref<Box>({ ...WHOLE });
const frame = ref<HTMLElement | null>(null);
let pull: { grip: Grip; x: number; y: number; start: Box } | null = null;

// A turned image comes back as another file: its box starts over the whole image.
watch(
  () => props.image.url,
  () => {
    box.value = { ...WHOLE };
  },
);

const boxStyle = computed(() => ({
  left: `${box.value.x}%`,
  top: `${box.value.y}%`,
  width: `${box.value.width}%`,
  height: `${box.value.height}%`,
}));

// The frame takes every move of the pointer until it is let go, also outside the image.
function grab(event: PointerEvent, grip: Grip): void {
  if (props.busy || !frame.value) return;
  pull = { grip, x: event.clientX, y: event.clientY, start: { ...box.value } };
  frame.value.setPointerCapture(event.pointerId);
  event.preventDefault();
  event.stopPropagation();
}

function move(event: PointerEvent): void {
  if (!pull || !frame.value) return;
  const rect = frame.value.getBoundingClientRect();
  box.value = pulled(pull.start, pull.grip, ((event.clientX - pull.x) / rect.width) * 100, ((event.clientY - pull.y) / rect.height) * 100);
}

function release(): void {
  pull = null;
}
</script>

<template>
  <ModalDialog @close="emit('close')">
    <div class="editor">
      <header><Crop :size="16" /> {{ t("crop_modal_title") }}</header>
      <div ref="frame" class="frame" @pointermove="move" @pointerup="release" @pointercancel="release">
        <img :src="image.url" :alt="image.name" draggable="false" />
        <div class="box" :style="boxStyle" @pointerdown="grab($event, '')">
          <span v-for="grip in GRIPS" :key="grip" :class="['grip', grip]" @pointerdown="grab($event, grip)" />
        </div>
      </div>
      <p class="hint">{{ t("crop_modal_hint") }}</p>
      <div class="buttons">
        <button @click="emit('close')">{{ t("crop_cancel") }}</button>
        <button :disabled="busy" :title="t('crop_rotate_left')" @click="emit('rotate', false)"><RotateCcw :size="16" /></button>
        <button :disabled="busy" :title="t('crop_rotate_right')" @click="emit('rotate', true)"><RotateCw :size="16" /></button>
        <button class="apply" :disabled="busy" @click="emit('crop', box)"><Check :size="16" /> {{ t("crop_apply") }}</button>
      </div>
    </div>
  </ModalDialog>
</template>

<style scoped>
.editor { display: flex; flex-direction: column; align-items: center; gap: 14px; }
header { font-weight: 600; }
.frame { position: relative; touch-action: none; user-select: none; }
.frame img { display: block; max-width: calc(90 * var(--vw)); max-height: calc(60 * var(--dvh)); border-radius: 8px; }
.box { position: absolute; border: 2px dashed var(--aifred-accent-success); cursor: move; box-sizing: border-box; }
.grip { position: absolute; background: var(--aifred-accent-success); }
.grip.nw, .grip.ne, .grip.sw, .grip.se { width: 16px; height: 16px; border-radius: 2px; }
.grip.nw { top: -8px; left: -8px; cursor: nw-resize; }
.grip.ne { top: -8px; right: -8px; cursor: ne-resize; }
.grip.sw { bottom: -8px; left: -8px; cursor: sw-resize; }
.grip.se { bottom: -8px; right: -8px; cursor: se-resize; }
.grip.n, .grip.s { left: 50%; width: 32px; height: 8px; margin-left: -16px; cursor: ns-resize; }
.grip.n { top: -5px; }
.grip.s { bottom: -5px; }
.grip.w, .grip.e { top: 50%; width: 8px; height: 32px; margin-top: -16px; cursor: ew-resize; }
.grip.w { left: -5px; }
.grip.e { right: -5px; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-secondary); }
.buttons { display: flex; gap: 10px; }
.buttons button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 6px 14px; cursor: pointer; font: inherit; }
.buttons button:disabled { opacity: 0.6; cursor: progress; }
.buttons .apply { background: var(--aifred-accent-success); color: var(--aifred-text-on-accent); border-color: var(--aifred-accent-success); }
</style>
