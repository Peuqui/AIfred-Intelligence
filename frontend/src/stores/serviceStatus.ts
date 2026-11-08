// The status line's state: the last snapshot of the server (GET /api/service-status, then every change pushed as
// service_status over the session stream) with the moment it arrived, and the machines with describers (asked when
// the bar asks for a snapshot, on refresh and after their button). The bar counts down from that moment itself.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { HostStatus, ServiceSnapshot } from "../api/types";
import type { Action } from "../lib/serviceStatus";

// What the server answered to a click (a refusal, an error) stays under the list this long.
const NOTICE_MS = 10000;

export const useServiceStatus = defineStore("serviceStatus", () => {
  const snapshot = ref<ServiceSnapshot | null>(null);
  const receivedAt = ref(0);
  const hosts = ref<HostStatus[]>([]);
  const notice = ref("");
  const acting = ref(false);
  let noticeTimer = 0;

  function receive(next: ServiceSnapshot): void {
    snapshot.value = next;
    receivedAt.value = performance.now();
  }

  function say(text: string): void {
    notice.value = text;
    window.clearTimeout(noticeTimer);
    noticeTimer = window.setTimeout(() => (notice.value = ""), NOTICE_MS);
  }

  async function fetchHosts(): Promise<void> {
    try {
      hosts.value = await api<HostStatus[]>("service-status/hosts");
    } catch (error) {
      say(error instanceof ApiError ? error.detail : String(error));
    }
  }

  async function fetch(refresh: boolean): Promise<void> {
    try {
      receive(await api<ServiceSnapshot>(`service-status${refresh ? "?refresh=true" : ""}`));
    } catch (error) {
      say(error instanceof ApiError ? error.detail : String(error));
    }
    await fetchHosts();
  }

  // A load answers at once and goes on in the background; the line follows over the push.
  async function act(action: Action): Promise<void> {
    const hold = action.verb === "hold" || action.verb === "release";
    const body = hold
      ? { host: action.item, hold: action.verb === "hold" }
      : action.verb === "load" ? { service: action.service } : { service: action.service, item: action.item };
    acting.value = true;
    try {
      await api(`service-status/${hold ? "hold" : action.verb}`, { method: "POST", body: JSON.stringify(body) });
    } catch (error) {
      say(error instanceof ApiError ? error.detail : String(error));
    } finally {
      if (hold) await fetchHosts();
      acting.value = false;
    }
  }

  return { snapshot, receivedAt, hosts, notice, acting, receive, fetch, act };
});
