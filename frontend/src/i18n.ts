// UI texts come from the server (aifred/lib/i18n/<lang>.json, one source for every client).
import { ref } from "vue";
import { api } from "./api/client";

const texts = ref<Record<string, string>>({});
export const language = ref("de");

export async function loadLanguage(lang: string): Promise<void> {
  texts.value = await api<Record<string, string>>(`app/i18n/${lang}`);
  language.value = lang;
  document.documentElement.lang = lang;
}

export function t(key: string, values: Record<string, string | number> = {}): string {
  const text = texts.value[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in values ? String(values[name]) : match,
  );
}
