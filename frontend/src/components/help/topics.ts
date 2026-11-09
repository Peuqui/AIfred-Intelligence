// The helps of the page (until 08.11.2026 the help modals of Reflex): each topic's title and the tooltip of its 💡.
import type { InjectionKey } from "vue";
import { t } from "../../i18n";

export type HelpTopic = "chat" | "debate" | "thinking" | "lifecycle" | "vigilantia" | "casus";

export const HELP_TOPICS: Record<HelpTopic, { title: () => string; tooltip: () => string }> = {
  chat: { title: () => t("chat_toolbar_help_title"), tooltip: () => t("chat_toolbar_help_tooltip") },
  debate: { title: () => t("multi_agent_help_title"), tooltip: () => t("discussion_mode_tooltip") },
  thinking: { title: () => t("reasoning_thinking_help_title"), tooltip: () => t("reasoning_thinking_help_lightbulb_tooltip") },
  lifecycle: { title: () => t("model_lifecycle_help_title"), tooltip: () => t("model_lifecycle_help_tooltip") },
  vigilantia: { title: () => t("vigilantia_help_title"), tooltip: () => t("vigilantia_help_tooltip") },
  casus: { title: () => t("casus_help_title"), tooltip: () => t("casus_help_tooltip") },
};

// A help that leads to another topic shows it in the same dialog (HelpDialog provides it).
export const OPEN_HELP: InjectionKey<(topic: HelpTopic) => void> = Symbol("open help");
