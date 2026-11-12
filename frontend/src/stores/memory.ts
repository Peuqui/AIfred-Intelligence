// The memory page: every agent with its number of memories, and the entries of the agent open. Every change
// answers with the agent's entries and the counts again.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import { onResourceChanged } from "../lib/resourceChanges";
import type { MemoryAgent, MemoryEntry } from "../api/types";

export const useMemory = defineStore("memory", () => {
  const agents = ref<MemoryAgent[]>([]);
  const agent = ref("");
  const entries = ref<MemoryEntry[]>([]);
  const notice = ref("");

  async function call(work: () => Promise<unknown>): Promise<boolean> {
    notice.value = "";
    try {
      await work();
      agents.value = await api<MemoryAgent[]>("memory");
      if (agent.value) entries.value = await api<MemoryEntry[]>(`memory/${encodeURIComponent(agent.value)}`);
      return true;
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return false;
    }
  }

  const load = (): Promise<boolean> => call(async () => undefined);

  function open(id: string): Promise<boolean> {
    agent.value = id;
    entries.value = [];
    return load();
  }

  const path = (id: string): string => `memory/${encodeURIComponent(agent.value)}/${encodeURIComponent(id)}`;
  const edit = (id: string, summary: string, content: string): Promise<boolean> =>
    call(() => api(path(id), { method: "PATCH", body: JSON.stringify({ summary, content }) }));
  const remove = (id: string): Promise<boolean> => call(() => api(path(id), { method: "DELETE" }));
  const clear = (): Promise<boolean> => call(() => api(`memory/${encodeURIComponent(agent.value)}`, { method: "DELETE" }));

  // An agent stored, changed or deleted a memory (its tool, another tab): the counts and the open entries are read anew.
  onResourceChanged("memory", () => void load());

  return { agents, agent, entries, notice, load, open, edit, remove, clear };
});
