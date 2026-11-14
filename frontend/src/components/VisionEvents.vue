<script setup lang="ts">
// The vision's events (the Casus), newest first: filtered by source, type and person, every event or one row per
// occurrence (grouped), page by page. Per row the pictures (face crop or wide picture, the tele picture beside it),
// the type, the source, the person assigned or guessed, what the image model said; a person assigned or taken
// back, the occurrence's film, the event analysed by the image model, the event deleted. Every event the filter
// lists can be deleted at once (asked first); every event without a description can be described (by occurrence,
// with progress, cancelled at will). Both analyses are runs of the server, one at a time; this page follows them. A click on a picture opens the slideshow over every page (grouped: the occurrence's film). New events
// arrive over a stream; the list loads again then, unless a picture or an assignment is open (it waits for that).
import { Check, Film, Hourglass, RefreshCw, Sparkles, Trash2, User, X } from "lucide-vue-next";
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ApiError, PROGRESS_POLL_MS, api, apiUrl } from "../api/client";
import type { CasusChoices, CasusEvent, CasusPage, CasusRuns, FilmPicture } from "../api/types";
import { formatNumber, t, tCount } from "../i18n";
import ImageLightbox from "./ImageLightbox.vue";
import HelpButton from "./help/HelpButton.vue";

const choices = ref<CasusChoices | null>(null);
// A person's or a camera's events open from elsewhere: #/vision/events?face=<id> or ?source=<id>.
const opened = useRoute().query;
const chosen = reactive({ source: String(opened.source ?? ""), type: "all", face: String(opened.face ?? "all") });
const grouped = ref(false);
const offset = ref(0);
const list = ref<CasusPage | null>(null);
const notice = ref("");
const expanded = ref(0);
const tagging = reactive({ event: 0, face: "" });
const confirming = ref(false);
const slideshow = ref<{ count: number; start: number; pictures: (position: number) => Promise<string[]> | string[] } | null>(null);
const runs = ref<CasusRuns | null>(null);
let stream: EventSource | null = null;
let polling: number | null = null;
let latest: number | null = null;
let missed = false;          // new events while a picture or an assignment was open

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
const query = (at: number, limit?: number): string =>
  new URLSearchParams({ ...chosen, grouped: String(grouped.value), offset: String(at), ...(limit ? { limit: String(limit) } : {}) }).toString();

async function load(): Promise<void> {
  list.value = (await call(() => api<CasusPage>(`vision/casus/events?${query(offset.value)}`))) ?? list.value;
}
// Another filter starts at the first page again.
watch([chosen, grouped], () => {
  offset.value = 0;
  confirming.value = false;
  void load();
});
const clearFilters = (): void => void Object.assign(chosen, { source: "", type: "all", face: "all" });
function turn(delta: number): void {
  if (!list.value) return;
  offset.value = Math.max(0, offset.value + delta * list.value.page_size);
  void load();
}
const pageLabel = computed(() => {
  if (!list.value?.total) return "0";
  const first = list.value.offset + 1;
  const last = Math.min(list.value.offset + list.value.page_size, list.value.total);
  return t("casus_page_label", { first: formatNumber(first), last: formatNumber(last), total: formatNumber(list.value.total) });
});

const frame = (id: number, zoom: boolean, width = 0): string =>
  apiUrl(`vision/frame?id=${id}${zoom ? "&zoom=1" : ""}${width ? `&w=${width}` : ""}`);
const pictures = (id: number, hasZoom: boolean): string[] => [frame(id, false), ...(hasZoom ? [frame(id, true)] : [])];

// The slideshow runs from the oldest to the newest event of the filter (→ = newer), the list newest first.
function openSlideshow(index: number): void {
  const total = list.value?.total ?? 0;
  slideshow.value = {
    count: total,
    start: total - 1 - (offset.value + index),
    pictures: async (position) => {
      const at = await api<CasusPage>(`vision/casus/events?${query(total - 1 - position, 1)}`);
      const event = at.events[0];
      return event ? pictures(event.id, event.has_zoom) : [];
    },
  };
}
async function openFilm(event: CasusEvent): Promise<void> {
  const film = await call(() => api<FilmPicture[]>(`vision/casus/events/${event.id}/film`));
  if (!film?.length) return;
  slideshow.value = {
    count: film.length,
    start: 0,
    pictures: (position) => {
      const picture = film[position];
      return picture ? pictures(picture.id, picture.has_zoom) : [];
    },
  };
}
function open(event: CasusEvent, index: number): void {
  if (grouped.value) void openFilm(event);
  else openSlideshow(index);
}
function closeSlideshow(): void {
  slideshow.value = null;
  catchUp();
}

function startTag(event: CasusEvent): void {
  Object.assign(tagging, { event: event.id, face: event.face_id === null ? "" : String(event.face_id) });
}
async function saveTag(): Promise<void> {
  const face = tagging.face === "" ? null : Number(tagging.face);
  const event = tagging.event;
  tagging.event = 0;
  await call(() => api(`vision/casus/events/${event}/face`, { method: "PUT", body: JSON.stringify({ face_id: face }) }));
  await load();
  catchUp();
}
function cancelTag(): void {
  tagging.event = 0;
  catchUp();
}

async function remove(event: CasusEvent): Promise<void> {
  await call(() => api(`vision/casus/events/${event.id}`, { method: "DELETE" }));
  await load();
}
async function removeAll(): Promise<void> {
  confirming.value = false;
  const done = await call(() => api<{ deleted: number }>("vision/casus/events/delete", { method: "POST", body: JSON.stringify(chosen) }));
  choices.value = (await call(() => api<CasusChoices>("vision/casus/choices"))) ?? choices.value;
  if (chosen.source && !choices.value?.sources.some((source) => source.id === chosen.source)) {
    // A source without events left is no choice any more: the filter falls back to every source; its watcher
    // loads the list (and clears the line of notices first, so the count comes after it).
    chosen.source = "";
    await nextTick();
  } else {
    offset.value = 0;
    await load();
  }
  if (done) notice.value = tCount("casus_deleted", done.deleted);
}

// A new event: the list loads again, unless the user is looking at a picture or assigning a person.
function catchUp(): void {
  if (missed && !slideshow.value && !tagging.event) {
    missed = false;
    void load();
  }
}
function follow(): void {
  stream = new EventSource(apiUrl("vision/casus/stream"));
  stream.addEventListener("stored", (message) => {
    const newest = Number(JSON.parse((message as MessageEvent).data).latest);
    if (latest !== null && newest !== latest) {
      missed = true;
      catchUp();
    }
    latest = newest;
  });
}

// While an analysis runs its state is asked again; at its end the list loads again (the new description).
function followRuns(state: CasusRuns): void {
  const before = runs.value;
  runs.value = state;
  const ended = (before?.analysis.running && !state.analysis.running) || (before?.describe_all.running && !state.describe_all.running);
  if (ended) {
    // load() clears the line of notices first: the analysis' error comes after it.
    const error = before?.analysis.running && state.analysis.error ? state.analysis.error : "";
    void load().then(() => {
      if (error) notice.value = `⚠️ ${error}`;
    });
  }
  if ((state.analysis.running || state.describe_all.running) && polling === null) {
    polling = window.setTimeout(async () => {
      polling = null;
      const fresh = await call(() => api<CasusRuns>("vision/casus/runs"));
      if (fresh) followRuns(fresh);
    }, PROGRESS_POLL_MS);
  }
}
async function startRun(path: string, method = "POST"): Promise<void> {
  const state = await call(() => api<CasusRuns>(path, { method }));
  if (state) followRuns(state);
}

onMounted(async () => {
  choices.value = await call(() => api<CasusChoices>("vision/casus/choices"));
  await load();
  follow();
  const state = await call(() => api<CasusRuns>("vision/casus/runs"));
  if (state) followRuns(state);     // a run another page started shows here too
});
onBeforeUnmount(() => {
  stream?.close();
  if (polling !== null) window.clearTimeout(polling);
});

const taggable = (event: CasusEvent): boolean => choices.value?.taggable_types.includes(event.event_type) ?? false;
const value = (event: Event): string => (event.target as HTMLSelectElement).value;
</script>

<template>
  <section class="casus">
    <div v-if="choices" class="filters">
      <select v-model="chosen.source">
        <option value="">{{ t("casus_source_all") }}</option>
        <option v-for="source in choices.sources" :key="source.id" :value="source.id">{{ source.label }}</option>
      </select>
      <select v-model="chosen.type">
        <option v-for="kind in choices.types" :key="kind.id" :value="kind.id">{{ kind.label }}</option>
      </select>
      <select v-model="chosen.face">
        <option value="all">{{ t("casus_face_all") }}</option>
        <option value="unknown">{{ t("casus_face_unconfirmed") }}</option>
        <option v-for="face in choices.faces" :key="face.id" :value="face.id">{{ face.label }}</option>
      </select>
      <label class="switch" :title="t('casus_help_grouped_body')"><input v-model="grouped" type="checkbox" /> {{ t("casus_cluster_mode_label") }}</label>
      <button :title="t('casus_refresh')" @click="load"><RefreshCw :size="16" /></button>
      <button @click="clearFilters">{{ t("casus_filter_clear") }}</button>
      <HelpButton topic="casus" />
      <span class="grow"></span>
      <template v-if="list?.total">
        <button v-if="!confirming" class="danger" @click="confirming = true"><Trash2 :size="16" /> {{ t("delete_all") }}</button>
        <template v-else>
          <button class="danger" @click="removeAll">{{ t("delete_all_confirm") }} ({{ formatNumber(list.total) }})</button>
          <button @click="confirming = false">{{ t("delete_all_cancel") }}</button>
        </template>
      </template>
    </div>
    <div v-if="runs" class="bulk">
      <button
        v-if="!runs.describe_all.running"
        :title="`${t('casus_bulk_start_tooltip')} — ${t('casus_help_bulk_body')}`"
        @click="startRun('vision/casus/describe-all')"
      ><Sparkles :size="16" /> {{ t("casus_bulk_start") }}</button>
      <template v-else>
        <progress :max="runs.describe_all.clusters || 1" :value="runs.describe_all.processed"></progress>
        <button :disabled="runs.describe_all.cancelling" @click="startRun('vision/casus/describe-all', 'DELETE')">{{ t("casus_bulk_cancel") }}</button>
      </template>
      <span class="hint">{{ runs.describe_all.message }}</span>
    </div>
    <p v-if="notice" class="notice">{{ notice }}</p>

    <div v-if="list" class="pages">
      <button :disabled="list.offset === 0" @click="turn(-1)">‹</button>
      <span>{{ pageLabel }}</span>
      <button :disabled="list.offset + list.page_size >= list.total" @click="turn(1)">›</button>
    </div>
    <p v-if="list && !list.events.length" class="hint">{{ t("casus_empty") }}</p>

    <div v-for="(event, index) in list?.events ?? []" :key="event.id" class="event">
      <span class="when">{{ event.date_display }}<br />{{ event.time_display }}</span>
      <span class="thumbs">
        <img v-if="event.crop_url" :src="event.crop_url" alt="" loading="lazy" @click="open(event, index)" />
        <img v-else-if="event.has_frame" :src="frame(event.id, false, 80)" alt="" loading="lazy" @click="open(event, index)" />
        <span v-else class="none">∿</span>
        <img v-if="event.has_zoom" :src="frame(event.id, true, 80)" alt="" loading="lazy" @click="open(event, index)" />
      </span>
      <span class="badge" :class="event.event_type">{{ t(`casus_badge_${event.event_type}`) }}</span>
      <span v-if="grouped && event.cluster_member_count > 1" class="members">+{{ formatNumber(event.cluster_member_count - 1) }}</span>
      <span class="source" :title="event.source_id">{{ event.source_name }}</span>
      <span class="who">
        <span v-if="event.face_name">{{ event.face_name }}</span>
        <span v-else-if="event.matched_name" class="guess">{{ event.matched_name }}</span>
        <span
          v-if="event.description"
          class="description"
          :class="{ open: expanded === event.id }"
          :title="t('casus_expand_hint')"
          @click="expanded = expanded === event.id ? 0 : event.id"
        >{{ event.description }}</span>
      </span>
      <span class="actions">
        <template v-if="tagging.event === event.id">
          <select :value="tagging.face" @change="tagging.face = value($event)">
            <option value="">{{ t("casus_tag_clear") }}</option>
            <option v-for="face in choices?.faces ?? []" :key="face.id" :value="face.id">{{ face.label }}</option>
          </select>
          <button :title="t('casus_tag_save')" @click="saveTag"><Check :size="16" /></button>
          <button :title="t('casus_tag_cancel')" @click="cancelTag"><X :size="16" /></button>
        </template>
        <button v-else-if="taggable(event)" :title="t('casus_tag_button')" @click="startTag(event)"><User :size="16" /></button>
        <button v-if="event.cluster_id" :title="t('casus_film_tooltip')" @click="openFilm(event)"><Film :size="16" /></button>
        <button
          v-if="event.has_frame && runs"
          :class="{ done: event.description }"
          :disabled="runs.analysis.running !== 0"
          :title="`${t(event.description ? 'casus_analyze_again' : 'casus_analyze')} — ${t('casus_help_sparkles_body')}`"
          @click="startRun(`vision/casus/events/${event.id}/analyze`)"
        ><Hourglass v-if="runs.analysis.running === event.id" :size="16" /><Sparkles v-else :size="16" /></button>
        <button class="danger" :title="t('casus_delete')" @click="remove(event)"><Trash2 :size="16" /></button>
      </span>
    </div>

    <ImageLightbox v-if="slideshow" :count="slideshow.count" :start="slideshow.start" :pictures="slideshow.pictures" @close="closeSlideshow" />
  </section>
</template>

<style scoped>
.casus { display: flex; flex-direction: column; gap: 8px; max-width: 1100px; }
.filters, .pages { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pages { justify-content: center; font-size: 0.85rem; color: var(--aifred-text-secondary); }
.grow { flex: 1; }
select { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
button { padding: 3px 8px; font-size: 0.85rem; }
.switch { display: inline-flex; align-items: center; gap: 4px; font-size: 0.85rem; }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.event { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-top: 1px solid var(--aifred-border); flex-wrap: wrap; }
.when { font-family: monospace; font-size: 0.75rem; color: var(--aifred-text-muted); min-width: 6.5em; line-height: 1.2; }
.thumbs { display: inline-flex; gap: 4px; }
.thumbs img, .thumbs .none { width: 40px; height: 40px; border-radius: 4px; object-fit: cover; border: 1px solid var(--aifred-border); cursor: zoom-in; }
.thumbs .none { display: inline-flex; align-items: center; justify-content: center; color: var(--aifred-text-muted); cursor: default; }
.badge { font-size: 0.72rem; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--aifred-border); color: var(--aifred-text-secondary); }
.badge.person { color: var(--aifred-accent-purple); border-color: var(--aifred-accent-purple); }
.badge.vehicle { color: var(--aifred-accent-cyan); border-color: var(--aifred-accent-cyan); }
.badge.animal, .badge.vlm_analysis { color: var(--aifred-accent-amber); border-color: var(--aifred-accent-amber); }
.badge.motion { color: var(--aifred-accent-blue); border-color: var(--aifred-accent-blue); }
.badge.face_known { color: var(--aifred-accent-success); border-color: var(--aifred-accent-success); }
.badge.face_unsure { color: var(--aifred-accent-warning); border-color: var(--aifred-accent-warning); }
.members { font-size: 0.72rem; color: var(--aifred-accent-purple); }
.source { font-size: 0.8rem; color: var(--aifred-text-muted); min-width: 6em; max-width: 10em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.who { flex: 1 1 240px; min-width: 0; display: flex; flex-direction: column; font-size: 0.88rem; }
.guess { font-style: italic; color: var(--aifred-text-muted); }
.description { font-size: 0.8rem; font-style: italic; color: var(--aifred-text-muted); cursor: pointer; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.description.open { display: block; white-space: pre-wrap; }
.actions { display: inline-flex; gap: 4px; align-items: center; }
.actions button.done { border-color: var(--aifred-accent-purple); --icon-own: currentColor; }
.bulk { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.bulk progress { width: 220px; accent-color: var(--aifred-primary); --icon-own: currentColor; }
.hint { margin: 0; font-size: 0.85rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
