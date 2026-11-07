// AIfred's general settings (settings.json over GET/PATCH /api/settings): language, name, speech output,
// dictation, debate. One source for every page; a change of the language loads the interface's texts again.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { Settings, SettingsUpdate } from "../api/types";
import { loadLanguage } from "../i18n";

export const useSettings = defineStore("settings", () => {
  const current = ref<Settings | null>(null);
  const notice = ref("");

  async function load(): Promise<void> {
    current.value = await api<Settings>("settings");
  }

  async function change(update: SettingsUpdate): Promise<void> {
    notice.value = "";
    const language = current.value?.ui_language;
    try {
      current.value = await api<Settings>("settings", { method: "PATCH", body: JSON.stringify(update) });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return;
    }
    if (current.value.ui_language !== language) await loadLanguage(current.value.ui_language);
  }

  return { current, notice, load, change };
});
