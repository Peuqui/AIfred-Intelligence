// The status line's state: the last snapshot of the server (GET /api/service-status, then every change pushed as
// service_status over the session stream) with the moment it arrived, and the machines with describers (asked when
// the bar asks for a snapshot, on refresh and after their button) and the hold of this machine's own cards. The bar counts down from that moment itself.
import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { ApiError, api } from "../api/client";
import type { HostStatus, LocalHoldStatus, ServiceSnapshot } from "../api/types";
import { language, t } from "../i18n";
import { type Action, localHoldRow } from "../lib/serviceStatus";

// What the server answered to a click (a refusal, an error) stays under the list this long.
const NOTICE_MS = 10000;
// The endpoints of the local hold's two ways to end it: ask the owner, or end it at once.
const LOCAL_PATHS: Partial<Record<Action["verb"], string>> = { end: "end-hold", release_now: "release-hold" };

export const useServiceStatus = defineStore("serviceStatus", () => {
  const snapshot = ref<ServiceSnapshot | null>(null);
  const receivedAt = ref(0);
  const hosts = ref<HostStatus[]>([]);
  const localHold = ref<LocalHoldStatus | null>(null);
  const notice = ref("");
  const acting = ref(false);
  let noticeTimer = 0;
  // The hold as the status line shows it: state, detail and the buttons with their reasons.
  const localHoldView = computed(() => (localHold.value ? localHoldRow(localHold.value, {
    lang: language.value,
    hostUsed: t("service_status_host_used"),
    free: t("service_status_local_free"),
    requested: t("service_status_local_requested"),
    granted: t("service_status_local_granted"),
    active: t("service_status_local_active"),
    reason: t("service_status_local_reason"),
    model: t("service_status_local_model"),
    since: t("service_status_local_since"),
    off: t("service_status_local_off"),
    unreachable: t("service_status_gpu_orc_unreachable"),
    notRequested: t("service_status_local_not_requested"),
    noHold: t("service_status_local_no_hold"),
    endWaiting: t("service_status_local_end_waiting"),
    releaseNeedsRequest: t("service_status_local_release_needs_request"),
  }) : null));

  function receive(next: ServiceSnapshot): void {
    snapshot.value = next;
    receivedAt.value = performance.now();
  }

  // The server pushes every change of the hold (local_hold over the session stream), the page need not wait for its poll.
  function receiveLocalHold(next: LocalHoldStatus): void {
    localHold.value = next;
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
    const local = action.verb === "grant" || action.verb === "end" || action.verb === "release_now";
    const body = hold
      ? { host: action.item, hold: action.verb === "hold" }
      : local ? { owner: action.item }
      : action.verb === "load" ? { service: action.service } : { service: action.service, item: action.item };
    const path = hold ? "hold" : LOCAL_PATHS[action.verb] ?? action.verb;
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

  // Asking the holder to end its hold (a requested one is withdrawn at once): ask first, with who holds the cards and
  // why. The one place for the status line, the banner and the waiting turn.
  async function endHold(owner: string, reason: string): Promise<void> {
    if (window.confirm(t("service_status_local_end_confirm", { owner, reason }))) {
      await act({ verb: "end", service: "host", item: owner });
    }
  }

  // Ending the hold at once takes the work of its owner away: a stronger question.
  async function releaseHoldNow(owner: string): Promise<void> {
    if (window.confirm(t("service_status_local_release_now_confirm", { owner }))) {
      await act({ verb: "release_now", service: "host", item: owner });
    }
  }

  return { snapshot, receivedAt, hosts, localHold, localHoldView, notice, acting, receive, fetch, act, endHold, releaseHoldNow, receiveLocalHold };
});
