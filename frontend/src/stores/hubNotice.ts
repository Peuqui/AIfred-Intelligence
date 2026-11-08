// The hint of a channel's message (lib/message_processor.py, push_hub_notification): what came in from whom, that it
// is being answered, that it is done or failed. One hint at a time, a later phase replaces the earlier one; it goes by
// itself after its time (done and error soon, the phases before when nothing follows for long).
import { defineStore } from "pinia";
import { ref } from "vue";

export interface HubNotice {
  session_id: string;
  session_title: string;
  channel: string;
  sender: string;
  status: "received" | "processing" | "done" | "error";
}

// How long a hint of each phase stays (ms), as the Reflex page had them.
const SHOWN_MS: Record<HubNotice["status"], number> = { received: 120000, processing: 120000, done: 5000, error: 8000 };

export const useHubNotice = defineStore("hubNotice", () => {
  const notice = ref<HubNotice | null>(null);
  let timer = 0;

  function receive(next: HubNotice): void {
    notice.value = next;
    window.clearTimeout(timer);
    timer = window.setTimeout(() => (notice.value = null), SHOWN_MS[next.status] ?? SHOWN_MS.done);
  }

  function dismiss(): void {
    window.clearTimeout(timer);
    notice.value = null;
  }

  return { notice, receive, dismiss };
});
