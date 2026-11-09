<script setup lang="ts">
// The ⋮ beside the chat's title: what concerns the whole chat (read it aloud anew, keep it in memory, share, download,
// the sandbox's outputs, clear it), each with its icon and words. The entries come from ChatView; a click on one
// closes the menu, as a click beside it or Escape does.
import { EllipsisVertical } from "lucide-vue-next";
import { ref } from "vue";
import { t } from "../i18n";
import { closeWhenBeside } from "../lib/dismiss";

const open = ref(false);
const box = ref<HTMLElement | null>(null);
closeWhenBeside(open, box);

function picked(event: MouseEvent): void {
  if ((event.target as HTMLElement).closest("button")) open.value = false;
}
</script>

<template>
  <span ref="box" class="chat-menu">
    <button class="trigger" :title="t('chat_menu_title')" :aria-label="t('chat_menu_title')" :aria-expanded="open" @click="open = !open">
      <EllipsisVertical :size="18" />
    </button>
    <div v-if="open" class="panel" role="menu" @click="picked">
      <slot />
    </div>
  </span>
</template>

<style scoped>
.chat-menu { position: relative; display: inline-flex; }
.trigger {
  display: inline-flex; align-items: center; background: none; border: 1px solid var(--aifred-border); border-radius: 6px;
  padding: 2px 6px; color: inherit; cursor: pointer;
}
.panel {
  position: absolute; top: calc(100% + 6px); right: 0; z-index: 20; width: min(300px, calc(90 * var(--vw))); padding: 4px;
  display: flex; flex-direction: column; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light);
  border-radius: 8px; font-size: var(--text-small);
}
.panel :slotted(button) {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px; text-align: left; background: none; border: none;
  border-radius: 6px; color: var(--aifred-text-primary); font: inherit; cursor: pointer;
}
.panel :slotted(button:hover:not(:disabled)) { background: var(--aifred-input-bg); }
.panel :slotted(button:disabled) { opacity: 0.45; cursor: default; }
.panel :slotted(.danger) { color: var(--aifred-danger); border-top: 1px solid var(--aifred-border); border-radius: 0 0 6px 6px; margin-top: 4px; }
</style>
