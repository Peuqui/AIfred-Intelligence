// The agents' quick choices (models, persona, reasoning, thinking, speed): global settings, no choice of
// one session. The server answers every change with all rows again (lib/agent_choices.py): a model
// change of AIfred moves the agents that run on his.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { ChoicesChange, QuickChoices } from "../api/types";

export const useAgentChoices = defineStore("agentChoices", () => {
  const choices = ref<QuickChoices | null>(null);
  const busy = ref(false);
  const notice = ref("");

  async function load(): Promise<void> {
    notice.value = "";
    try {
      choices.value = await api<QuickChoices>("agents/choices");
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  }

  async function change(agent: string, update: ChoicesChange): Promise<void> {
    busy.value = true;
    notice.value = "";
    try {
      choices.value = await api<QuickChoices>(`agents/${agent}/choices`, { method: "PATCH", body: JSON.stringify(update) });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    } finally {
      busy.value = false;
    }
  }

  return { choices, busy, notice, load, change };
});
