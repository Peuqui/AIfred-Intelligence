<script setup lang="ts">
// What the vision keeps (events, persons), deletable while the plugin is off (data protection; PluginGate's slot `off`):
// every event at once, every person at once or one by one — each asked first. The endpoints are the plugin's own deletions, which the
// plugin's switch does not lock (api/vision.py: /vision/stored and the deletions).
import { Trash2, TriangleAlert } from "lucide-vue-next";
import { onMounted, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { StoredVisionData } from "../api/types";
import { formatNumber, t, tCount } from "../i18n";

const stored = ref<StoredVisionData | null>(null);
const notice = ref("");
const confirming = ref<"events" | "persons" | number | null>(null);

async function load(): Promise<void> {
  stored.value = await api<StoredVisionData>("vision/stored");
}

async function remove(path: string, init: RequestInit): Promise<void> {
  confirming.value = null;
  notice.value = "";
  try {
    await api(path, init);
    await load();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
  }
}
const removeEvents = (): Promise<void> => remove("vision/casus/events/delete", { method: "POST", body: "{}" });
const removePersons = (): Promise<void> => remove("vision/face/delete", { method: "POST" });
const removePerson = (id: string): Promise<void> => remove(`vision/face/${id}`, { method: "DELETE" });

onMounted(load);
</script>

<template>
  <section v-if="stored" class="stored">
    <strong>{{ t("vision_stored_title") }}</strong>
    <p class="hint">{{ t("vision_stored_hint") }}</p>
    <p v-if="notice" class="notice"><TriangleAlert :size="16" /> {{ notice }}</p>
    <div class="line">
      <span class="what">{{ tCount("vision_stored_events", stored.events, { count: formatNumber(stored.events) }) }}</span>
      <template v-if="stored.events">
        <button v-if="confirming !== 'events'" class="danger" @click="confirming = 'events'"><Trash2 :size="16" /> {{ t("delete_all") }}</button>
        <template v-else>
          <button class="danger" @click="removeEvents">{{ t("delete_all_confirm") }}</button>
          <button @click="confirming = null">{{ t("delete_all_cancel") }}</button>
        </template>
      </template>
    </div>
    <div v-if="stored.persons.length" class="line">
      <span class="what">{{ tCount("vision_stored_persons", stored.persons.length, { count: formatNumber(stored.persons.length) }) }}</span>
      <button v-if="confirming !== 'persons'" class="danger" @click="confirming = 'persons'"><Trash2 :size="16" /> {{ t("delete_all") }}</button>
      <template v-else>
        <button class="danger" @click="removePersons">{{ t("delete_all_confirm") }}</button>
        <button @click="confirming = null">{{ t("delete_all_cancel") }}</button>
      </template>
    </div>
    <div v-for="person in stored.persons" :key="person.id" class="line">
      <span class="what">{{ person.label }}</span>
      <button v-if="confirming !== Number(person.id)" class="danger" :title="t('personarium_delete')" @click="confirming = Number(person.id)"><Trash2 :size="16" /></button>
      <template v-else>
        <span class="confirm">{{ t("personarium_delete_confirm", { name: person.label }) }}</span>
        <button class="danger" @click="removePerson(person.id)">{{ t("personarium_delete") }}</button>
        <button @click="confirming = null">{{ t("delete_all_cancel") }}</button>
      </template>
    </div>
  </section>
</template>

<style scoped>
.stored { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; max-width: 520px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.what { flex: 1; min-width: 120px; }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); --icon-own: currentColor; }
.confirm { font-size: 0.85rem; }
button { padding: 3px 8px; font-size: 0.85rem; }
</style>
