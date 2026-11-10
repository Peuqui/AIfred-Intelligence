import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "../api/client";
import type { SessionSummary } from "../api/types";
import { onResourceChanged } from "../lib/resourceChanges";

export const useSessions = defineStore("sessions", () => {
  const list = ref<SessionSummary[]>([]);

  async function refresh(): Promise<void> {
    list.value = await api<SessionSummary[]>("sessions");
  }

  async function create(): Promise<string> {
    const { session_id } = await api<{ session_id: string }>("sessions", { method: "POST" });
    await refresh();
    return session_id;
  }

  // Several sessions at once (one of them is the single 🗑 of a line).
  async function remove(sessionIds: string[]): Promise<void> {
    await api<void>("sessions/delete", { method: "POST", body: JSON.stringify({ session_ids: sessionIds }) });
    await refresh();
  }

  function retitle(sessionId: string, title: string): void {
    const entry = list.value.find((session) => session.session_id === sessionId);
    if (entry) entry.title = title;
  }

  // A chat was saved, made or deleted (another tab, a job, a channel): the list is read anew.
  onResourceChanged("sessions", () => void refresh());

  return { list, refresh, create, remove, retitle };
});
