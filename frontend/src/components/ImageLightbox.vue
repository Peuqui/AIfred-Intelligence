<script setup lang="ts">
// One picture large, with the others of its row one key or click away (← →, Esc closes): photos of a
// camera, the frames of an event, faces. The same box everywhere a picture opens.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { t } from "../i18n";

const props = defineProps<{ images: string[]; start: number }>();
const emit = defineEmits<{ close: [] }>();
const index = ref(props.start);
const current = computed(() => props.images[index.value] ?? "");

function step(delta: number): void {
  index.value = (index.value + delta + props.images.length) % props.images.length;
}

function key(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
  else if (event.key === "ArrowLeft") step(-1);
  else if (event.key === "ArrowRight") step(1);
}
onMounted(() => window.addEventListener("keydown", key));
onBeforeUnmount(() => window.removeEventListener("keydown", key));
</script>

<template>
  <div class="lightbox" role="dialog" @click.self="emit('close')">
    <button class="close" :title="t('dialog_close')" @click="emit('close')">×</button>
    <button v-if="images.length > 1" class="step back" @click="step(-1)">‹</button>
    <a :href="current" target="_blank" rel="noopener"><img :src="current" alt="" /></a>
    <button v-if="images.length > 1" class="step forward" @click="step(1)">›</button>
    <span v-if="images.length > 1" class="counter">{{ index + 1 }} / {{ images.length }}</span>
  </div>
</template>

<style scoped>
.lightbox { position: fixed; inset: 0; z-index: 50; background: rgba(0, 0, 0, 0.88); display: flex; align-items: center; justify-content: center; }
img { max-width: 92vw; max-height: 88dvh; border-radius: 6px; }
button { position: absolute; background: none; border: none; color: #fff; font-size: 2rem; cursor: pointer; padding: 8px 14px; }
.close { top: 8px; right: 12px; }
.back { left: 8px; }
.forward { right: 8px; }
.counter { position: absolute; bottom: 12px; color: #ddd; font-size: 0.85rem; }
</style>
