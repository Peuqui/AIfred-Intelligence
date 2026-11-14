<script setup lang="ts">
// One position of a row large, the neighbours one key or click away (← →, Esc closes; it stops at both ends):
// photos of a camera, the events of the Casus over every page, the film of an occurrence, faces. A position may
// hold several pictures of the same moment (the wide and the tele picture of an event), shown side by side. The
// pictures of a position are asked for when it is shown, so a long row never travels to the browser as a whole.
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import { t } from "../i18n";

const props = defineProps<{ count: number; start: number; pictures: (position: number) => Promise<string[]> | string[] }>();
const emit = defineEmits<{ close: [] }>();
const position = ref(props.start);
const shown = ref<string[]>([]);

async function show(at: number): Promise<void> {
  const pictures = await props.pictures(at);
  if (at === position.value) shown.value = pictures;   // a quicker step meanwhile wins
}
watch(position, (at) => void show(at), { immediate: true });

function step(delta: number): void {
  const target = position.value + delta;
  if (target >= 0 && target < props.count) position.value = target;
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
    <button v-if="count > 1" class="step back" :disabled="position === 0" @click="step(-1)">‹</button>
    <div class="pictures" :class="{ pair: shown.length > 1 }" @click.self="emit('close')">
      <a v-for="url in shown" :key="url" :href="url" target="_blank" rel="noopener"><img :src="url" alt="" /></a>
    </div>
    <button v-if="count > 1" class="step forward" :disabled="position === count - 1" @click="step(1)">›</button>
    <span v-if="count > 1" class="counter">{{ position + 1 }} / {{ count }}</span>
  </div>
</template>

<style scoped>
.lightbox { position: fixed; inset: 0; z-index: 50; background: rgba(0, 0, 0, 0.88); display: flex; align-items: center; justify-content: center; }
.pictures { display: flex; gap: 10px; align-items: center; justify-content: center; }
img { display: block; max-width: calc(92 * var(--vw)); max-height: calc(88 * var(--dvh)); border-radius: 6px; }
.pair img { max-width: calc(44 * var(--vw)); }
button { position: absolute; background: none; border: none; color: #fff; font-size: 2rem; cursor: pointer; padding: 8px 14px; }
.close { top: 8px; right: 12px; }
.back { left: 8px; }
.forward { right: 8px; }
.counter { position: absolute; bottom: 12px; color: #ddd; font-size: 0.85rem; }
</style>
