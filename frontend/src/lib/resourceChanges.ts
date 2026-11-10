// A list of the server's data changed (an agent's tool, a scheduled job, another tab): the page loads it anew, wherever it
// stands. The server says which (lib/browser_push.py: notify_changed, event "<name>_changed"; the store of the list
// listens here, stores/*.ts). A burst of changes (a session saved several times in a turn) is one load.
import { RESOURCES, type Resource } from "../api/events";

const BURST_MS = 300;

const listeners = new Map<Resource, Set<() => void>>(RESOURCES.map((resource) => [resource, new Set()]));
const timers = new Map<Resource, number>();

export function onResourceChanged(resource: Resource, load: () => void): void {
  listeners.get(resource)?.add(load);
}

export function resourceChanged(resource: Resource): void {
  window.clearTimeout(timers.get(resource));
  timers.set(
    resource,
    window.setTimeout(() => listeners.get(resource)?.forEach((load) => load()), BURST_MS),
  );
}

// The resource an event kind names, or undefined for any other kind.
export function resourceOfKind(kind: string): Resource | undefined {
  return RESOURCES.find((resource) => kind === `${resource}_changed`);
}
