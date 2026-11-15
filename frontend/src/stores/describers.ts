// The describer list as the server has it (lib/describer_list.py): who describes images, in which order, what
// holds an entry back, and the burn-in that runs there (one at a time, for every page). The settings page shows
// and orders it, the maintenance page burns in and unloads; both read this one list. While a burn-in runs it is
// asked again, its end brings the row's new note.

import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, PROGRESS_POLL_MS, api } from "../api/client";
import type { Describers } from "../api/types";

export const useDescribers = defineStore("describers", () => {
  const list = ref<Describers | null>(null);
  const notice = ref("");
  let polling: number | null = null;

  function follow(describers: Describers): void {
    list.value = describers;
    if (describers.measuring && polling === null) {
      polling = window.setTimeout(() => {
        polling = null;
        void call(() => api<Describers>("describers"));
      }, PROGRESS_POLL_MS);
    }
  }

  async function call(work: () => Promise<Describers>): Promise<void> {
    notice.value = "";
    try {
      follow(await work());
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  }

  const load = (): Promise<void> => call(() => api<Describers>("describers"));
  const post = (path: string, body: object): Promise<void> =>
    call(() => api<Describers>(`describers${path}`, { method: "POST", body: JSON.stringify(body) }));

  function stop(): void {
    if (polling !== null) window.clearTimeout(polling);
    polling = null;
  }

  return { list, notice, load, post, stop };
});
