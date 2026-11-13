// UI texts come from the server (aifred/lib/i18n/<lang>.json, one source for every client).
import { computed, ref } from "vue";
import { api } from "./api/client";

const texts = ref<Record<string, string>>({});
export const language = ref("de");
// Until the texts have come, t() gives the key itself ("new_version_loaded"), which reads like English: what shows before
// the page is logged in waits for this.
export const textsLoaded = computed(() => Object.keys(texts.value).length > 0);

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

// A number of things: the key's text for one, `<key>_plural` for any other number, with {count} filled in;
// the same rule as the server's t(key, count=…).
export function tCount(key: string, count: number, values: Record<string, string | number> = {}): string {
  return t(count === 1 ? key : `${key}_plural`, { ...values, count });
}

// A number as the language writes it (1.234,5 or 1,234.5); the browser's side of the server's format_number.
export function formatNumber(value: number, digits = 0): string {
  return value.toLocaleString(language.value, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
