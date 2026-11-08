<script setup lang="ts">
// The vision in the chat: the eye (armed or not) and how many events the last minutes brought; a click shows the
// newest events, each a step into the events tab. New events arrive over the events' stream; once a minute it asks
// again after the time the server names, so older events leave the window. Away while the vision plugin is off.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { api, apiUrl } from "../api/client";
import type { CasusRecent } from "../api/types";
import { formatNumber, t, tCount } from "../i18n";

const state = ref<CasusRecent | null>(null);
const open = ref(false);
let stream: EventSource | null = null;
let refreshing: number | null = null;

async function load(): Promise<void> {
  state.value = await api<CasusRecent>("vision/casus/recent");
}

onMounted(async () => {
  await load();
  stream = new EventSource(apiUrl("vision/casus/stream"));
  stream.addEventListener("stored", () => void load());
  refreshing = window.setInterval(() => void load(), (state.value?.refresh_seconds ?? 0) * 1000);
});
onBeforeUnmount(() => {
  stream?.close();
  if (refreshing !== null) window.clearInterval(refreshing);
});
</script>

<template>
  <span v-if="state?.plugin_enabled" class="badge-box">
    <button
      class="badge"
      :class="{ armed: state.armed, fresh: state.recent > 0 }"
      :title="tCount('vision_badge_title', state.recent, { minutes: formatNumber(state.window_minutes) })"
      @click="open = !open"
    >{{ state.armed ? "👁️" : "🙈" }}<span v-if="state.recent" class="count">{{ formatNumber(state.recent) }}</span></button>
    <div v-if="open" class="popover" @mouseleave="open = false">
      <p v-if="!state.events.length" class="hint">{{ t("vision_badge_empty") }}</p>
      <RouterLink
        v-for="event in state.events"
        :key="event.id"
        class="event"
        :to="{ path: '/vision/events', query: { source: event.source_id } }"
        @click="open = false"
      >
        <img v-if="event.crop_url" :src="event.crop_url" alt="" loading="lazy" />
        <img v-else-if="event.has_frame" :src="apiUrl(`vision/frame?id=${event.id}&w=64`)" alt="" loading="lazy" />
        <span v-else class="none">∿</span>
        <span class="text">
          <span class="kind">{{ t(`casus_badge_${event.event_type}`) }}</span>
          <span class="meta">{{ event.time_display }} · {{ event.source_name }}<template v-if="event.face_name || event.matched_name"> · {{ event.face_name || event.matched_name }}</template></span>
        </span>
      </RouterLink>
      <RouterLink class="all" to="/vision/events" @click="open = false">🗂️ {{ t("vision_badge_all") }}</RouterLink>
    </div>
  </span>
</template>

<style scoped>
.badge-box { position: relative; }
.badge { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; }
.badge.armed { border-color: var(--aifred-primary); }
.count { font-size: 0.75rem; min-width: 1.2em; padding: 0 5px; border-radius: 999px; background: var(--aifred-accent-amber); color: var(--aifred-text-on-accent); }
.popover { position: absolute; right: 0; top: calc(100% + 6px); z-index: 30; width: 320px; max-height: 60dvh; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; padding: 8px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light); border-radius: 10px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35); }
.event, .all { display: flex; align-items: center; gap: 8px; padding: 4px; border-radius: 6px; color: inherit; text-decoration: none; }
.event:hover, .all:hover { background: var(--aifred-input-bg); }
.event img, .event .none { width: 32px; height: 32px; border-radius: 4px; object-fit: cover; border: 1px solid var(--aifred-border); display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
.text { display: flex; flex-direction: column; min-width: 0; }
.kind { font-size: 0.8rem; }
.meta { font-size: 0.72rem; color: var(--aifred-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.all { justify-content: center; font-size: 0.82rem; border-top: 1px solid var(--aifred-border); margin-top: 4px; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
</style>
