// The server pushes every change of the hold of this machine's cards (local_hold over the session stream): the status
// line shows the new holder at once, without waiting for its poll.
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import type { LocalHoldStatus } from "../api/types";

const followed: { handle: (kind: string, data: Record<string, unknown>) => void }[] = [];

vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  api: () => Promise.resolve({ session_id: "s1", title: "T", config: {}, chat_history: [], debug_messages: [], live_events: [], last_event_id: 0, running: false }),
}));
vi.mock("../api/events", () => ({
  RESOURCES: [],
  followSession: (_id: string, _afterId: number, handle: (kind: string, data: Record<string, unknown>) => void) => {
    followed.push({ handle });
    return { close() {} };
  },
}));

const { useChat } = await import("./chat");
const { useServiceStatus } = await import("./serviceStatus");

beforeEach(() => {
  setActivePinia(createPinia());
  followed.length = 0;
});

test("a pushed local_hold event replaces the hold the status line shows", async () => {
  await useChat().open("s1");
  const status: LocalHoldStatus = {
    state: "active", machine: "mini", owner: "vllm-research", reason: "v13", since: "2026-11-22T08:00:00+01:00",
    model: null, granted_by: null, error: "", used_mib: 100, total_mib: 200,
  };
  followed[0]?.handle("local_hold", { ...status });
  expect(useServiceStatus().localHold).toEqual(status);
});
