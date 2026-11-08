<script setup lang="ts">
// The audio page: speech output on or off, AIfred's list of speech engines (order, switch, unit; the first
// that can speaks), each chat agent's voice on an engine and the narrator's, whether speech recognition
// answers, and the audio player's sources (AudioSources.vue). What exists (engines, places, voices) is the TTS service's catalog; when it does not answer the
// list shows from the settings and says why.
import { onMounted, ref, watch } from "vue";
import { ApiError, api } from "../api/client";
import type { AgentSpeech, NarratorVoice, SpeechChoices, SpeechList, SpeechRecognition } from "../api/types";
import { t } from "../i18n";
import AudioSources from "../components/AudioSources.vue";
import { useSettings } from "../stores/settings";

const settings = useSettings();
const list = ref<SpeechList | null>(null);
const choices = ref<SpeechChoices | null>(null);
const recognition = ref<SpeechRecognition | null>(null);
const engine = ref("");   // the engine whose voices are shown
const agents = ref<AgentSpeech[]>([]);
const narrator = ref<NarratorVoice | null>(null);
const notice = ref("");
const checking = ref(false);

async function call<T>(work: () => Promise<T>): Promise<T | null> {
  notice.value = "";
  try {
    return await work();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.detail : String(error);
    return null;
  }
}
const send = <T,>(path: string, method: string, body: object): Promise<T> => api<T>(path, { method, body: JSON.stringify(body) });

onMounted(async () => {
  [list.value, choices.value, recognition.value] = await Promise.all([
    call(() => api<SpeechList>("speech/list")),
    call(() => api<SpeechChoices>("speech/choices")),
    call(() => api<SpeechRecognition>("speech/recognition")),
  ]);
  engine.value = choices.value?.engines[0]?.id ?? "";
});

async function changeList(path: string, method: string, body: object): Promise<void> {
  const changed = await call(() => send<SpeechList>(path, method, body));
  if (changed) list.value = changed;
}

async function refresh(): Promise<void> {
  checking.value = true;
  const fresh = await call(() => api<SpeechList>("speech/list/refresh", { method: "POST" }));
  if (fresh) list.value = fresh;
  checking.value = false;
}

watch(engine, async (chosen) => {
  if (!chosen) return;
  const path = encodeURIComponent(chosen);
  const [voices, reading] = await Promise.all([
    call(() => api<AgentSpeech[]>(`speech/agents?engine=${path}`)),
    call(() => api<NarratorVoice>(`speech/narrator/${path}`)),
  ]);
  agents.value = voices ?? [];
  narrator.value = reading;
});

function replace(updated: AgentSpeech | null): void {
  if (updated) agents.value = agents.value.map((agent) => (agent.agent === updated.agent ? updated : agent));
}

async function speech(agent: AgentSpeech, change: { enabled?: boolean; language?: string }): Promise<void> {
  replace(await call(() => send<AgentSpeech>(`speech/agents/${agent.agent}?engine=${encodeURIComponent(engine.value)}`, "PATCH", change)));
}

async function voice(agent: AgentSpeech, change: { voice?: string; speed?: string; pitch?: string }): Promise<void> {
  const chosen = { voice: agent.voice, speed: agent.speed, pitch: agent.pitch, ...change };
  replace(await call(() => send<AgentSpeech>(`speech/agents/${agent.agent}/voices/${encodeURIComponent(engine.value)}`, "PUT", chosen)));
}

async function narrate(chosen: string): Promise<void> {
  narrator.value = (await call(() => send<NarratorVoice>(`speech/narrator/${encodeURIComponent(engine.value)}`, "PUT", { voice: chosen }))) ?? narrator.value;
}

const picked = (event: Event): string => (event.target as HTMLSelectElement).value;
</script>

<template>
  <main class="speech">
    <p v-if="notice" class="notice">⚠️ {{ notice }}</p>

    <section v-if="settings.current">
      <label class="line">
        <span class="name">{{ t("tts_heading") }}</span>
        <input type="checkbox" :checked="settings.current.enable_tts" @change="settings.change({ enable_tts: !settings.current.enable_tts })" />
      </label>
      <label class="line">
        <span class="name">{{ t("tts_autoplay_label") }}</span>
        <input type="checkbox" :checked="settings.current.tts_autoplay" :disabled="!settings.current.enable_tts" @change="settings.change({ tts_autoplay: !settings.current.tts_autoplay })" />
      </label>
    </section>

    <section v-if="list" class="engines">
      <div class="head">
        <strong :title="t('tts_list_tooltip')">{{ t("tts_list_heading") }}</strong>
        <button :disabled="checking" @click="refresh">🔄 {{ t("tts_check_status") }}</button>
      </div>
      <p v-if="list.service_error" class="error">{{ list.service_error }}</p>
      <div v-for="row in list.rows" :key="`${row.index}-${row.engine}`" class="entry" :class="{ off: !row.enabled, gone: row.unavailable }">
        <span class="order">
          <button :title="t('tts_entry_up')" :disabled="row.index === 0" @click="changeList('speech/list/move', 'POST', { index: row.index, delta: -1 })">▲</button>
          <button :title="t('tts_entry_down')" :disabled="row.index === list.rows.length - 1" @click="changeList('speech/list/move', 'POST', { index: row.index, delta: 1 })">▼</button>
        </span>
        <input type="checkbox" :checked="row.enabled" :disabled="row.unavailable" @change="changeList('speech/list/switch', 'POST', { index: row.index, enabled: !row.enabled })" />
        <span class="label">{{ row.label }}</span>
        <span v-if="row.reserved" class="badge" :title="t('tts_entry_reserved_tooltip')">{{ t("tts_entry_reserved") }}</span>
        <span v-if="row.warn" class="warn" :title="t('tts_entry_unreachable_warning')">⚠️</span>
        <span class="status">{{ row.status }}</span>
        <select v-if="choices && row.unit" :value="row.unit" @change="changeList(`speech/units/${encodeURIComponent(row.engine)}`, 'PUT', { unit: picked($event) })">
          <option v-for="unit in choices.units" :key="unit.id" :value="unit.id">{{ unit.label }}</option>
        </select>
        <span v-if="row.note" class="note">{{ row.note }}</span>
      </div>
    </section>

    <section v-if="choices" class="voices">
      <div class="head">
        <strong>{{ t("speech_voices_title") }}</strong>
        <select v-if="choices.engines.length" v-model="engine">
          <option v-for="offered in choices.engines" :key="offered.id" :value="offered.id">{{ offered.label }}</option>
        </select>
      </div>
      <p v-if="choices.service_error" class="error">{{ choices.service_error }}</p>
      <table v-if="agents.length">
        <thead>
          <tr>
            <th></th>
            <th>{{ t("speech_agent_speaks") }}</th>
            <th>{{ t("agent_editor_tts_language") }}</th>
            <th>{{ t("agent_editor_tts_voice") }}</th>
            <th>{{ t("agent_editor_tts_speed") }}</th>
            <th>{{ t("agent_editor_tts_pitch") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="agent in agents" :key="agent.agent" :class="{ off: !agent.enabled }">
            <td class="who">{{ agent.emoji }} {{ agent.display_name }}</td>
            <td><input type="checkbox" :checked="agent.enabled" @change="speech(agent, { enabled: !agent.enabled })" /></td>
            <td>
              <select :value="agent.language" @change="speech(agent, { language: picked($event) })">
                <option v-for="language in choices.languages" :key="language.id" :value="language.id">{{ language.label }}</option>
              </select>
            </td>
            <td>
              <select :value="agent.voice" @change="voice(agent, { voice: picked($event) })">
                <option v-if="!agent.voice" value="">–</option>
                <option v-for="name in agent.voices" :key="name" :value="name">{{ name }}</option>
              </select>
            </td>
            <td>
              <select :value="agent.speed" @change="voice(agent, { speed: picked($event) })">
                <option v-for="speed in choices.speeds" :key="speed" :value="speed">{{ speed }}</option>
              </select>
            </td>
            <td>
              <select :value="agent.pitch" @change="voice(agent, { pitch: picked($event) })">
                <option v-for="pitch in choices.pitches" :key="pitch" :value="pitch">{{ pitch }}</option>
              </select>
            </td>
          </tr>
          <tr v-if="narrator" class="narrator">
            <td class="who" :title="t('narrator_settings_subtitle')">📖 {{ t("speech_narrator") }}</td>
            <td></td>
            <td></td>
            <td>
              <select :value="narrator.voice" @change="narrate(picked($event))">
                <option v-for="name in narrator.voices" :key="name" :value="name">{{ name }}</option>
              </select>
            </td>
            <td></td>
            <td></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="recognition">
      <div class="line">
        <span class="name">🎤 {{ t("service_status_stt") }}</span>
        <span :class="recognition.available ? 'ok' : 'error'">{{ recognition.available ? `✅ ${t("speech_recognition_answers")}` : t("stt_unavailable") }}</span>
      </div>
      <p class="hint">{{ t("stt_suite_hint") }}</p>
    </section>

    <AudioSources />
  </main>
</template>

<style scoped>
.speech { overflow-y: auto; padding: 16px 0; display: flex; flex-direction: column; gap: 14px; max-width: 920px; }
section { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.head, .line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.head { justify-content: space-between; }
.name { min-width: 170px; font-weight: 600; font-size: 0.9rem; }
select, button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 3px 6px; font: inherit; font-size: 0.85rem; }
button { cursor: pointer; }
button:disabled { opacity: 0.4; cursor: default; }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; }
.entry { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 0; border-top: 1px solid var(--aifred-border); }
.entry.off .label, tr.off .who { opacity: 0.6; }
.entry.gone .label { text-decoration: line-through; }
.order { display: inline-flex; gap: 2px; }
.order button { padding: 1px 5px; }
.label { flex: 1; min-width: 160px; }
.status { font-size: 0.8rem; color: var(--aifred-text-secondary); }
.badge { font-size: 0.72rem; color: var(--aifred-accent-amber); border: 1px solid var(--aifred-accent-amber); border-radius: 999px; padding: 0 6px; }
.note { flex-basis: 100%; font-size: 0.78rem; color: var(--aifred-text-muted); padding-left: 56px; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; font-size: 0.78rem; font-weight: 600; color: var(--aifred-text-secondary); padding: 4px 6px; }
td { padding: 4px 6px; border-top: 1px solid var(--aifred-border); }
td.who { white-space: nowrap; }
tr.narrator td { border-top: 2px solid var(--aifred-border); }
.error { margin: 0; color: var(--aifred-danger); font-size: 0.85rem; }
.ok { color: var(--aifred-text-secondary); }
.hint { margin: 0; font-size: 0.8rem; color: var(--aifred-text-muted); }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
