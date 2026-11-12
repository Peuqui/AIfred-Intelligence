<script setup lang="ts">
// Vigilantia in the chat, as on the Reflex page (vigilantia_feed.py): a one-line card with the newest event (or the
// eye's state while there is none), a click shows the step into Casus and the newest events, above the card (it sits at the page's foot); the 💡 explains it,
// the eye arms the background watch or disarms it (open and orange: armed, crossed out and grey: off). New events
// arrive over the events' stream; once a minute it asks again after the time the server names, so older events leave
// the window. Away while the vision plugin is off.
import { Eye, EyeOff, ScrollText } from "lucide-vue-next";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";
import { ApiError, api, apiUrl } from "../api/client";
import type { CasusRecent } from "../api/types";
import { formatNumber, t, tCount } from "../i18n";
import { closeWhenBeside } from "../lib/dismiss";
import HelpButton from "./help/HelpButton.vue";

const state = ref<CasusRecent | null>(null);
const open = ref(false);
const arming = ref(false);
const box = ref<HTMLElement | null>(null);
closeWhenBeside(open, box);
let stream: EventSource | null = null;
let refreshing: number | null = null;

const latest = computed(() => state.value?.events[0] ?? null);
// What the card says while no event is in the window: off, armed without a watched camera, or quiet.
const idle = computed(() =>
  !state.value?.armed ? t("vigilantia_feed_disarmed") : state.value.has_watched_camera ? t("vigilantia_feed_idle") : t("vigilantia_feed_no_sources"),
);

async function load(): Promise<void> {
  state.value = await api<CasusRecent>("vision/casus/recent");
}

async function arm(): Promise<void> {
  if (!state.value) return;
  arming.value = true;
  try {
    await api("vision/armed", { method: "PUT", body: JSON.stringify({ armed: !state.value.armed }) });
    await load();
  } catch (error) {
    window.alert(error instanceof ApiError ? error.detail : String(error));
  } finally {
    arming.value = false;
  }
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
  <span v-if="state?.plugin_enabled" ref="box" class="vigilantia">
    <button
      class="card"
      :class="{ fresh: state.recent > 0, off: !state.armed }"
      :title="tCount('vision_badge_title', state.recent, { minutes: formatNumber(state.window_minutes) })"
      @click="open = !open"
    >
      <template v-if="latest">
        <img v-if="latest.crop_url" :src="latest.crop_url" alt="" />
        <img v-else-if="latest.has_frame" :src="apiUrl(`vision/frame?id=${latest.id}&w=64`)" alt="" />
        <span class="when">{{ latest.time_display }}</span>
        <span class="kind">{{ t(`casus_badge_${latest.event_type}`) }}</span>
        <span class="what">{{ latest.face_name || latest.matched_name || latest.description || latest.source_name }}</span>
        <span v-if="state.recent" class="count">{{ formatNumber(state.recent) }}</span>
      </template>
      <span v-else class="idle" :class="{ warn: state.armed && !state.has_watched_camera }">{{ idle }}</span>
    </button>
    <HelpButton topic="vigilantia" />
    <button
      class="eye"
      :class="{ armed: state.armed }"
      :disabled="arming"
      :aria-pressed="state.armed"
      :title="t(state.armed ? 'vigilantia_disarm_tooltip' : 'vigilantia_arm_tooltip')"
      @click="arm"
    >
      <Eye v-if="state.armed" :size="16" /><EyeOff v-else :size="16" />
    </button>
    <div v-if="open" class="popover">
      <RouterLink class="all" to="/vision/events" @click="open = false"><ScrollText :size="14" /> {{ t("vigilantia_feed_open_casus") }}</RouterLink>
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
    </div>
  </span>
</template>

<style scoped>
.vigilantia { position: relative; display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: 100%; }
/* The one-line card of the Reflex page: the newest event, its words cut where the row ends. */
.card {
  display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: min(420px, 60vw); padding: 3px 10px;
  font: inherit; font-size: var(--text-small); color: inherit; cursor: pointer;
  background: var(--aifred-input-bg); border: 1px solid var(--aifred-primary); border-radius: 999px;
}
.card.off { border-color: var(--aifred-border); }
.card img { width: 20px; height: 20px; border-radius: 3px; object-fit: cover; flex: none; }
.when { flex: none; font-family: monospace; color: var(--aifred-text-muted); }
.card .kind { flex: none; color: var(--aifred-accent-blue); }
.what { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--aifred-text-secondary); }
.idle { white-space: nowrap; color: var(--aifred-text-muted); }
.idle.warn { color: var(--aifred-accent-amber); }
.count { flex: none; font-size: 0.75rem; min-width: 1.2em; padding: 0 5px; border-radius: 999px; background: var(--aifred-accent-amber); color: var(--aifred-text-on-accent); }
/* The eye: open and orange armed, crossed out and grey off (Reflex's solid / soft). */
.eye {
  display: inline-flex; align-items: center; padding: 4px 6px; border-radius: 6px; cursor: pointer;
  background: var(--aifred-input-bg); color: var(--aifred-text-muted); border: 1px solid var(--aifred-border);
}
.eye.armed { background: var(--aifred-primary); color: var(--aifred-text-on-accent); border-color: var(--aifred-primary); }
.eye:disabled { opacity: 0.6; cursor: progress; }
.popover { position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 30; width: 320px; max-height: calc(60 * var(--dvh)); overflow-y: auto; display: flex; flex-direction: column; gap: 2px; padding: 8px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light); border-radius: 10px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35); }
.event, .all { display: flex; align-items: center; gap: 8px; padding: 4px; border-radius: 6px; color: inherit; text-decoration: none; }
.event:hover, .all:hover { background: var(--aifred-input-bg); }
.event img, .event .none { width: 32px; height: 32px; border-radius: 4px; object-fit: cover; border: 1px solid var(--aifred-border); display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
.text { display: flex; flex-direction: column; min-width: 0; }
.popover .kind { font-size: 0.8rem; }
.meta { font-size: 0.72rem; color: var(--aifred-text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.all { justify-content: center; font-size: 0.82rem; border-bottom: 1px solid var(--aifred-border); margin-bottom: 4px; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
</style>
