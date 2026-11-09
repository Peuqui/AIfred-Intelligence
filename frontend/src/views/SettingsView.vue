<script setup lang="ts">
// AIfred's general settings: language and the user, debate, the input and dictation, the LLM backend with GPUs and calibration, the plugins
// (channels and tools with their settings), the local files nothing deletes by itself and the audit log.
// Speech output has its own page (🔊), the agents' models and switches are their quick choices (🎛️ in the chat);
// switching the backend, starting a calibration and the restarts follow.
import BackendSettings from "../components/BackendSettings.vue";
import HousekeepingSettings from "../components/HousekeepingSettings.vue";
import InputSettings from "../components/InputSettings.vue";
import PluginSettings from "../components/PluginSettings.vue";
import { t } from "../i18n";
import { useSettings } from "../stores/settings";

const settings = useSettings();

function named(event: Event): void {
  const name = (event.target as HTMLInputElement).value.trim();
  if (settings.current && name !== settings.current.user_name) void settings.change({ user_name: name });
}
</script>

<template>
  <main v-if="settings.current" class="settings">
    <p v-if="settings.notice" class="notice">⚠️ {{ settings.notice }}</p>

    <section>
      <label class="line">
        <span class="name">{{ t("ui_language") }}</span>
        <select :value="settings.current.ui_language" @change="settings.change({ ui_language: ($event.target as HTMLSelectElement).value as 'de' | 'en' })">
          <option value="de">de</option>
          <option value="en">en</option>
        </select>
      </label>
      <div class="line">
        <span class="name">👤</span>
        <input class="user-name" :value="settings.current.user_name" :placeholder="t('your_name')" @blur="named" @keydown.enter="named" />
        <span class="gender" role="radiogroup">
          <button :class="{ on: settings.current.user_gender === 'male' }" :aria-pressed="settings.current.user_gender === 'male'" @click="settings.change({ user_gender: 'male' })">♂</button>
          <button :class="{ on: settings.current.user_gender === 'female' }" :aria-pressed="settings.current.user_gender === 'female'" @click="settings.change({ user_gender: 'female' })">♀</button>
        </span>
      </div>
      <!-- The sign beside the user's chat bubble. -->
      <div class="signs" role="radiogroup">
        <button
          v-for="sign in settings.current.user_emoji_choices"
          :key="sign"
          :class="{ on: settings.current.user_emoji === sign }"
          :aria-pressed="settings.current.user_emoji === sign"
          @click="settings.change({ user_emoji: sign })"
        >
          {{ sign }}
        </button>
      </div>
    </section>

    <section>
      <div class="line">
        <span class="name">{{ t("max_debate_rounds") }}</span>
        <button :disabled="settings.current.max_debate_rounds <= settings.current.debate_rounds_min" @click="settings.change({ max_debate_rounds: settings.current.max_debate_rounds - 1 })">−</button>
        <span class="count">{{ settings.current.max_debate_rounds }}</span>
        <button :disabled="settings.current.max_debate_rounds >= settings.current.debate_rounds_max" @click="settings.change({ max_debate_rounds: settings.current.max_debate_rounds + 1 })">+</button>
      </div>
      <label class="line">
        <span class="name">{{ t("consensus_type_label") }}</span>
        <select :value="settings.current.consensus_type" @change="settings.change({ consensus_type: ($event.target as HTMLSelectElement).value as 'majority' | 'unanimous' })">
          <option value="majority">{{ t("consensus_majority") }}</option>
          <option value="unanimous">{{ t("consensus_unanimous") }}</option>
        </select>
      </label>
    </section>
    <InputSettings :settings="settings.current" @change="settings.change" />
    <BackendSettings />
    <PluginSettings />
    <HousekeepingSettings />
  </main>
</template>

<style scoped>
.settings { overflow-y: auto; padding: 16px 0; display: flex; flex-direction: column; gap: 14px; max-width: 720px; }
section { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
.line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.name { min-width: 170px; font-weight: 600; font-size: 0.9rem; }
select, input.user-name, button { background: var(--aifred-input-bg); color: inherit; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 4px 8px; font: inherit; }
button { cursor: pointer; min-width: 32px; }
button:disabled { opacity: 0.5; cursor: default; }
.gender { display: inline-flex; gap: 4px; }
.gender button.on { border-color: var(--aifred-primary); color: var(--aifred-primary); }
.signs { display: flex; flex-wrap: wrap; gap: 4px; }
.signs button { font-size: 1.2rem; padding: 2px 6px; background: none; border: 1px solid var(--aifred-border); border-radius: 6px; cursor: pointer; }
.signs button.on { border-color: var(--aifred-primary); background: var(--aifred-input-bg); }
.count { min-width: 2ch; text-align: center; }
input[type="checkbox"] { accent-color: var(--aifred-primary); width: 16px; height: 16px; }
.notice { margin: 0; color: var(--aifred-accent-amber); }
</style>
