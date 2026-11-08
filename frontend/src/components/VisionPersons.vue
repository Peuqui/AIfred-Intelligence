<script setup lang="ts">
// The vision's persons (the Personarium): every person with its picture, how many embeddings it has and when it was
// seen last; renamed, deleted (asked first), its embeddings shown and one deleted, its events in the events tab.
// Below, the open faces (no person confirmed, nobody dismissed), one card per sighting: the unsure ones (the
// recognition guesses a person) and the unknown ones. A card is assigned to a person, an existing or a new one (the
// face model learns it from the picture), or dismissed (several at once too, all after asking). Every open face can be
// recognised again against what was learned; that is a run of the server, this page follows it.
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { RouterLink } from "vue-router";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type { FaceEmbedding, FaceSummary, OpenFace, RematchState } from "../api/types";
import { formatNumber, t } from "../i18n";
import EnrollPoses from "./EnrollPoses.vue";

const persons = ref<FaceSummary[]>([]);
const open = ref<OpenFace[]>([]);
const notice = ref("");
const renaming = reactive({ id: 0, name: "" });
const deleting = ref(0);
const shown = ref(0);                     // the person whose embeddings are shown
const embeddings = ref<FaceEmbedding[]>([]);
const assigning = reactive({ card: 0, choice: "", name: "" });
const busy = ref(0);                      // the card being learned (the face model works)
const selected = ref<number[]>([]);
const confirmAll = ref(false);
const rematch = ref<RematchState | null>(null);
const enrolling = ref<{ faceId: number; name: string } | null>(null);   // the poses dialog: a person, or 0 = a new one
let polling: number | null = null;

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
const send = <T,>(path: string, method: string, body?: object): Promise<T> =>
  api<T>(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });

async function loadPersons(): Promise<void> {
  persons.value = (await call(() => api<{ faces: FaceSummary[] }>("vision/face/list")))?.faces ?? persons.value;
}
async function loadOpen(): Promise<void> {
  open.value = (await call(() => api<OpenFace[]>("vision/face/open"))) ?? open.value;
  selected.value = selected.value.filter((id) => open.value.some((card) => card.id === id));
}
// A message of an action stays after the lists it changed are loaded again.
async function reloadThenSay(message: string | undefined): Promise<void> {
  await Promise.all([loadPersons(), loadOpen()]);
  if (message) notice.value = message;
}

async function saveRename(): Promise<void> {
  const { id, name } = renaming;
  if (!name.trim()) return;
  renaming.id = 0;
  if (await call(() => send(`vision/face/${id}/rename`, "POST", { name }))) await loadPersons();
}
async function remove(person: FaceSummary): Promise<void> {
  deleting.value = 0;
  const done = await call(() => send<{ message: string }>(`vision/face/${person.id}`, "DELETE"));
  if (shown.value === person.id) shown.value = 0;
  await reloadThenSay(done?.message);
}
async function toggleEmbeddings(person: FaceSummary): Promise<void> {
  shown.value = shown.value === person.id ? 0 : person.id;
  embeddings.value = shown.value ? (await call(() => api<FaceEmbedding[]>(`vision/face/${person.id}/embeddings`))) ?? [] : [];
}
async function removeEmbedding(embedding: FaceEmbedding): Promise<void> {
  await call(() => send(`vision/face/embedding/${embedding.id}`, "DELETE"));
  embeddings.value = (await call(() => api<FaceEmbedding[]>(`vision/face/${shown.value}/embeddings`))) ?? [];
  await loadPersons();
}

const unsure = computed(() => open.value.filter((card) => card.event_type === "face_unsure"));
const unknown = computed(() => open.value.filter((card) => card.event_type !== "face_unsure"));

function startAssign(card: OpenFace): void {
  const guessed = persons.value.find((person) => person.name === card.matched_name);
  Object.assign(assigning, { card: card.id, choice: guessed ? String(guessed.id) : "", name: "" });
}
async function saveAssign(): Promise<void> {
  const { card, choice, name } = assigning;
  if (!choice || (choice === "new" && !name.trim())) return;
  busy.value = card;
  const body = choice === "new" ? { name } : { face_id: Number(choice) };
  const done = await call(() => send<{ message: string }>(`vision/face/open/${card}/assign`, "POST", body));
  busy.value = 0;
  if (done) assigning.card = 0;
  await reloadThenSay(done?.message);
  if (shown.value) embeddings.value = (await call(() => api<FaceEmbedding[]>(`vision/face/${shown.value}/embeddings`))) ?? [];
}
async function dismiss(cards: number[]): Promise<void> {
  confirmAll.value = false;
  const done = await call(() => send<{ message: string }>("vision/face/open/dismiss", "POST", { cards }));
  if (done) selected.value = [];
  await reloadThenSay(done?.message);
}
function toggle(card: OpenFace): void {
  selected.value = selected.value.includes(card.id) ? selected.value.filter((id) => id !== card.id) : [...selected.value, card.id];
}

function follow(state: RematchState): void {
  const ended = rematch.value?.running && !state.running;
  rematch.value = state;
  if (ended) void Promise.all([loadPersons(), loadOpen()]);
  if (state.running && polling === null) {
    polling = window.setTimeout(async () => {
      polling = null;
      const fresh = await call(() => api<RematchState>("vision/face/rematch"));
      if (fresh) follow(fresh);
    }, PROGRESS_POLL_MS);
  }
}
async function enrolled(message: string): Promise<void> {
  enrolling.value = null;
  await reloadThenSay(message);
  if (shown.value) embeddings.value = (await call(() => api<FaceEmbedding[]>(`vision/face/${shown.value}/embeddings`))) ?? [];
}
async function startRematch(): Promise<void> {
  const state = await call(() => send<RematchState>("vision/face/rematch", "POST"));
  if (state) follow(state);
}

onMounted(async () => {
  await Promise.all([loadPersons(), loadOpen()]);
  const state = await call(() => api<RematchState>("vision/face/rematch"));
  if (state) follow(state);
});
onBeforeUnmount(() => {
  if (polling !== null) window.clearTimeout(polling);
});

const value = (event: Event): string => (event.target as HTMLInputElement | HTMLSelectElement).value;
</script>

<template>
  <section class="persons">
    <p v-if="notice" class="notice">{{ notice }}</p>
    <div class="line">
      <p class="hint grow">{{ t("personarium_subtitle") }}</p>
      <button @click="enrolling = { faceId: 0, name: '' }">📸 {{ t("multipose_new_person") }}</button>
    </div>
    <p v-if="!persons.length" class="hint">{{ t("personarium_empty") }}</p>
    <article v-for="person in persons" :key="person.id" class="person">
      <div class="line">
        <img v-if="person.crop_url" class="avatar" :src="person.crop_url" alt="" />
        <span v-else class="avatar none">👤</span>
        <template v-if="renaming.id === person.id">
          <input class="name-input" :value="renaming.name" @input="renaming.name = value($event)" @keydown.enter="saveRename" />
          <button @click="saveRename">{{ t("personarium_save") }}</button>
          <button @click="renaming.id = 0">{{ t("personarium_cancel") }}</button>
        </template>
        <strong v-else class="name">{{ person.name }}</strong>
        <span class="meta">{{ formatNumber(person.embedding_count) }} {{ t("personarium_embeddings_suffix") }}</span>
        <span v-if="person.last_seen_display" class="meta">{{ t("personarium_seen_last", { when: person.last_seen_display }) }}</span>
        <span class="grow"></span>
        <RouterLink class="button" :to="{ path: '/vision/events', query: { face: String(person.id) } }" :title="t('personarium_events_link')">🗂️</RouterLink>
        <button :class="{ on: shown === person.id }" :title="t('personarium_manage_embeddings')" @click="toggleEmbeddings(person)">🧬</button>
        <button :title="t('personarium_multipose_add')" @click="enrolling = { faceId: person.id, name: person.name }">📸</button>
        <button :title="t('personarium_rename')" @click="Object.assign(renaming, { id: person.id, name: person.name })">✏️</button>
        <button v-if="deleting !== person.id" class="danger" :title="t('personarium_delete')" @click="deleting = person.id">🗑️</button>
        <template v-else>
          <span class="confirm">{{ t("personarium_delete_confirm", { name: person.name }) }}</span>
          <button class="danger" @click="remove(person)">{{ t("personarium_delete") }}</button>
          <button @click="deleting = 0">{{ t("personarium_cancel") }}</button>
        </template>
      </div>
      <div v-if="shown === person.id" class="embeddings">
        <p v-if="!embeddings.length" class="hint">{{ t("personarium_no_embeddings") }}</p>
        <figure v-for="embedding in embeddings" :key="embedding.id">
          <img v-if="embedding.crop_url" :src="embedding.crop_url" alt="" loading="lazy" />
          <span v-else class="none">∅</span>
          <figcaption>{{ formatNumber(embedding.quality, 2) }} · {{ embedding.created_at }}</figcaption>
          <button class="danger" :title="t('personarium_delete_embedding')" @click="removeEmbedding(embedding)">🗑️</button>
        </figure>
      </div>
    </article>

    <div class="open-head">
      <h3 :title="t('personarium_untagged_help')">{{ t("personarium_untagged_title") }} ({{ formatNumber(open.length) }})</h3>
      <button :disabled="rematch?.running" :title="t('personarium_rematch_hint')" @click="startRematch">🔄 {{ t("personarium_rematch_button") }}</button>
      <span v-if="rematch?.message" class="hint">{{ rematch.message }}</span>
      <span class="grow"></span>
      <template v-if="open.length">
        <button @click="selected = open.map((card) => card.id)">{{ t("personarium_select_all") }}</button>
        <button v-if="selected.length" @click="dismiss(selected)">{{ t("personarium_dismiss_selected") }} ({{ formatNumber(selected.length) }})</button>
        <button v-if="!confirmAll" class="danger" @click="confirmAll = true">{{ t("delete_all") }}</button>
        <template v-else>
          <button class="danger" @click="dismiss(open.map((card) => card.id))">{{ t("delete_all_confirm") }} ({{ formatNumber(open.length) }})</button>
          <button @click="confirmAll = false">{{ t("delete_all_cancel") }}</button>
        </template>
      </template>
    </div>
    <p v-if="!open.length" class="hint">{{ t("personarium_untagged_empty") }}</p>
    <template v-for="block in [{ cards: unsure, title: 'personarium_untagged_unsure_title', help: 'personarium_untagged_unsure_help' }, { cards: unknown, title: 'personarium_untagged_unknown_title', help: 'personarium_untagged_unknown_help' }]" :key="block.title">
      <template v-if="block.cards.length">
        <h4 :title="t(block.help)">{{ t(block.title) }} ({{ formatNumber(block.cards.length) }})</h4>
        <div class="cards">
          <article v-for="card in block.cards" :key="card.id" class="card" :class="{ chosen: selected.includes(card.id) }">
            <label class="pick"><input type="checkbox" :checked="selected.includes(card.id)" @change="toggle(card)" /></label>
            <img :src="card.crop_url" alt="" loading="lazy" />
            <span v-if="card.matched_name" class="guess">{{ card.matched_name }}?</span>
            <span class="meta">{{ card.date_display }} {{ card.time_display }}</span>
            <span class="meta">{{ card.source_name }}<template v-if="card.member_ids.length > 1"> · ×{{ formatNumber(card.member_ids.length) }}</template></span>
            <template v-if="assigning.card === card.id">
              <select :value="assigning.choice" @change="assigning.choice = value($event)">
                <option value="" disabled>{{ t("personarium_tag_placeholder") }}</option>
                <option v-for="person in persons" :key="person.id" :value="String(person.id)">{{ person.name }}</option>
                <option value="new">{{ t("personarium_tag_new_person") }}</option>
              </select>
              <input v-if="assigning.choice === 'new'" :placeholder="t('personarium_tag_new_name_placeholder')" :value="assigning.name" @input="assigning.name = value($event)" />
              <span class="actions">
                <button :disabled="busy === card.id" @click="saveAssign">{{ busy === card.id ? "⏳" : t("personarium_tag_save") }}</button>
                <button :disabled="busy === card.id" @click="assigning.card = 0">{{ t("personarium_tag_cancel") }}</button>
              </span>
            </template>
            <span v-else class="actions">
              <button :disabled="busy !== 0" :title="t('personarium_tag_button')" @click="startAssign(card)">👤＋</button>
              <button class="danger" :title="t('personarium_untagged_dismiss')" @click="dismiss([card.id])">✕</button>
            </span>
          </article>
        </div>
      </template>
    </template>
    <EnrollPoses v-if="enrolling" :face-id="enrolling.faceId" :name="enrolling.name" @close="enrolling = null" @saved="enrolled" />
  </section>
</template>

<style scoped>
.persons { display: flex; flex-direction: column; gap: 10px; max-width: 1100px; }
article.person { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.line, .open-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.grow { flex: 1; }
.avatar { width: 48px; height: 48px; border-radius: 50%; object-fit: cover; border: 1px solid var(--aifred-border); }
.avatar.none { display: inline-flex; align-items: center; justify-content: center; }
.name { font-size: 1rem; }
.meta { font-size: 0.78rem; color: var(--aifred-text-muted); }
.confirm { font-size: 0.85rem; color: var(--aifred-danger); }
select, input, button, .button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 8px; font: inherit; font-size: 0.85rem; }
.button { text-decoration: none; }
button { cursor: pointer; }
button:disabled { opacity: 0.5; cursor: default; }
button.on { border-color: var(--aifred-primary); color: var(--aifred-primary); }
button.danger { color: var(--aifred-danger); }
input[type="checkbox"] { accent-color: var(--aifred-primary); }
.embeddings { display: flex; gap: 8px; flex-wrap: wrap; }
.embeddings figure { margin: 0; display: flex; flex-direction: column; align-items: center; gap: 3px; }
.embeddings img, .embeddings .none { width: 64px; height: 64px; object-fit: cover; border-radius: 6px; border: 1px solid var(--aifred-border); display: inline-flex; align-items: center; justify-content: center; }
.embeddings figcaption { font-size: 0.7rem; color: var(--aifred-text-muted); }
h3 { margin: 6px 0 0; font-size: 1rem; }
h4 { margin: 4px 0 0; font-size: 0.9rem; color: var(--aifred-text-secondary); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.card { position: relative; display: flex; flex-direction: column; gap: 4px; align-items: stretch; padding: 8px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 8px; }
.card.chosen { border-color: var(--aifred-primary); }
.card img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 6px; }
.pick { position: absolute; top: 12px; left: 12px; }
.guess { font-size: 0.8rem; color: var(--aifred-accent-warning); }
.card .actions { display: flex; gap: 4px; flex-wrap: wrap; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
