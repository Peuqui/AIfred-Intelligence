// A list the server changed is loaded anew by the store that holds it; a burst of changes is one load.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { onResourceChanged, resourceChanged, resourceOfKind } from "./resourceChanges";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

test("a change loads the list once, however often it was announced in a burst", () => {
  const load = vi.fn();
  onResourceChanged("jobs", load);
  resourceChanged("jobs");
  resourceChanged("jobs");
  resourceChanged("jobs");
  expect(load).not.toHaveBeenCalled();
  vi.advanceTimersByTime(400);
  expect(load).toHaveBeenCalledTimes(1);
});

test("only the list that changed is loaded", () => {
  const jobs = vi.fn();
  const agents = vi.fn();
  onResourceChanged("jobs", jobs);
  onResourceChanged("agents", agents);
  resourceChanged("agents");
  vi.advanceTimersByTime(400);
  expect([jobs.mock.calls.length, agents.mock.calls.length]).toEqual([0, 1]);
});

test("an event kind names its list, any other kind none", () => {
  expect(resourceOfKind("jobs_changed")).toBe("jobs");
  expect(resourceOfKind("sessions_changed")).toBe("sessions");
  expect(resourceOfKind("message_added")).toBeUndefined();
  expect(resourceOfKind("service_status")).toBeUndefined();
});
