<script setup lang="ts">
// A newer build is installed: the bar at the top says so and the page loads it as soon as nothing would be lost (a draft,
// images, a dictation, a running answer); until then the bar waits, and a click loads it at once. After the reload a
// short notice confirms that the new version is the one running.
import { computed, onMounted, ref, watch } from "vue";
import { newVersion } from "../api/client";
import { t } from "../i18n";
import { UPDATED_NOTICE_MS, UPDATE_BANNER_MS, reloadForUpdate, takeUpdatedMark } from "../lib/update";
import { useChat } from "../stores/chat";

const chat = useChat();
const blocked = computed(
  () => !!chat.draft.trim() || chat.pendingImages.length > 0 || chat.dictations.length > 0 || chat.running,
);
const loaded = ref(false);

let reloadTimer: ReturnType<typeof setTimeout> | undefined;
watch(
  [newVersion, blocked],
  ([available, held]) => {
    clearTimeout(reloadTimer);
    if (available && !held) reloadTimer = setTimeout(reloadForUpdate, UPDATE_BANNER_MS);
  },
  { immediate: true },
);

onMounted(() => {
  if (!takeUpdatedMark()) return;
  loaded.value = true;
  setTimeout(() => (loaded.value = false), UPDATED_NOTICE_MS);
});
</script>

<template>
  <button v-if="newVersion" class="notice loading" @click="reloadForUpdate">
    {{ t(blocked ? "new_version_waiting" : "new_version_loading") }}
  </button>
  <div v-else-if="loaded" class="notice loaded">{{ t("new_version_loaded") }}</div>
</template>

<style scoped>
.notice {
  position: fixed; inset-inline: 0; top: 0; z-index: 60; padding: 8px 16px; border: none; text-align: center; font: inherit;
  font-weight: 600; color: var(--aifred-text-on-accent);
}
.notice { background: var(--aifred-danger); }
.loading { cursor: pointer; }
</style>
