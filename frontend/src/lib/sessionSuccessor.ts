// Which session opens after the open one was deleted (as the Reflex picker did it, 17.09.2026): the list is newest
// first; the nearest remaining session below the open one takes over, else the nearest above, else none (a new one).
export function successorSession(order: string[], current: string, deleted: Set<string>): string | null {
  const at = order.indexOf(current);
  const below = at >= 0 ? order.slice(at + 1) : order;
  const above = at >= 0 ? order.slice(0, at).reverse() : [];
  return [...below, ...above].find((sessionId) => !deleted.has(sessionId)) ?? null;
}
