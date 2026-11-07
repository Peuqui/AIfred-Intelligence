import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "../api/client";
import type { SessionSummary } from "../api/types";

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

  async function remove(sessionId: string): Promise<void> {
    await api<void>(`sessions/${sessionId}`, { method: "DELETE" });
    await refresh();
  }

  function retitle(sessionId: string, title: string): void {
    const entry = list.value.find((session) => session.session_id === sessionId);
    if (entry) entry.title = title;
  }

  return { list, refresh, create, remove, retitle };
});
