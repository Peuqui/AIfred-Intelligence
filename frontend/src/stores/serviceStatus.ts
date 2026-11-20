// The status line's state: the last snapshot of the server (GET /api/service-status, then every change pushed as
// service_status over the session stream) with the moment it arrived, and the machines with describers (asked when
// the bar asks for a snapshot, on refresh and after their button) and the hold of this machine's own cards. The bar counts down from that moment itself.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import type { HostStatus, LocalHoldStatus, ServiceSnapshot } from "../api/types";
import { t } from "../i18n";
import type { Action } from "../lib/serviceStatus";

// What the server answered to a click (a refusal, an error) stays under the list this long.
const NOTICE_MS = 10000;

export const useServiceStatus = defineStore("serviceStatus", () => {
  const snapshot = ref<ServiceSnapshot | null>(null);
  const receivedAt = ref(0);
  const hosts = ref<HostStatus[]>([]);
  const localHold = ref<LocalHoldStatus | null>(null);
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

  async function fetchLocalHold(): Promise<void> {
    try {
      localHold.value = await api<LocalHoldStatus>("service-status/local-hold");
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
    await Promise.all([fetchHosts(), fetchLocalHold()]);
  }

  // A load answers at once and goes on in the background; the line follows over the push.
  async function act(action: Action): Promise<void> {
    const hold = action.verb === "hold" || action.verb === "release";
    const local = action.verb === "grant" || action.verb === "end";
    const body = hold
      ? { host: action.item, hold: action.verb === "hold" }
      : local ? { owner: action.item }
      : action.verb === "load" ? { service: action.service } : { service: action.service, item: action.item };
    const path = hold ? "hold" : action.verb === "end" ? "end-hold" : action.verb;
    acting.value = true;
    try {
      await api(`service-status/${path}`, { method: "POST", body: JSON.stringify(body) });
    } catch (error) {
      say(error instanceof ApiError ? error.detail : String(error));
    } finally {
      if (hold) await fetchHosts();
      if (local) await fetchLocalHold();
      acting.value = false;
    }
  }

  // Ending a hold takes work away from somebody: ask first, with who holds the cards and why. The one place for the
  // status line and the waiting turn.
  async function endHold(owner: string, reason: string): Promise<void> {
    if (window.confirm(t("service_status_local_end_confirm", { owner, reason }))) {
      await act({ verb: "end", service: "host", item: owner });
    }
  }

  return { snapshot, receivedAt, hosts, localHold, notice, acting, receive, fetch, act, endHold };
});
