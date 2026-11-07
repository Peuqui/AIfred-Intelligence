// The agents page: every agent a chat can address, switched on or off, and what may be deleted. Switching
// and deleting answer with the cards again; the quick choices (models, persona, reasoning …) of the agents
// switched on come from the agentChoices store, the same rows the quick-choice modal shows.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { AgentCard } from "../api/types";
import { useAgentChoices } from "./agentChoices";

export const useAgents = defineStore("agents", () => {
  const cards = ref<AgentCard[]>([]);
  const notice = ref("");
  const busy = ref(false);
  const choices = useAgentChoices();

  async function call(work: () => Promise<unknown>): Promise<void> {
    busy.value = true;
    notice.value = "";
    try {
      await work();
      cards.value = await api<AgentCard[]>("agents");
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    } finally {
      busy.value = false;
    }
  }

  async function load(): Promise<void> {
    await Promise.all([call(async () => undefined), choices.load()]);
  }

  // A switched agent leaves (or joins) the quick choices: they are loaded again with the cards.
  async function switchAgent(id: string, enabled: boolean): Promise<void> {
    await call(() => api<AgentCard>(`agents/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }));
    await choices.load();
  }

  async function remove(id: string): Promise<void> {
    await call(() => api<void>(`agents/${encodeURIComponent(id)}`, { method: "DELETE" }));
    await choices.load();
  }

  return { cards, notice, busy, load, switchAgent, remove };
});
