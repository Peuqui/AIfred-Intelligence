// Which session opens after the open one was deleted (lib/sessionSuccessor.ts; until 08.11.2026 the Vue page always
// opened the newest).
import { expect, test } from "vitest";
import { successorSession } from "./sessionSuccessor";

test("the next one below takes over, the last one falls back to the one above", () => {
  expect(successorSession(["a", "b", "c"], "b", new Set(["b"]))).toBe("c");
  expect(successorSession(["a", "b", "c"], "c", new Set(["c"]))).toBe("b");
});

test("deleting several skips the deleted neighbours", () => {
  const order = ["a", "b", "c", "d", "e"];
  expect(successorSession(order, "b", new Set(["b", "c", "d"]))).toBe("e");
  expect(successorSession(order, "d", new Set(["d", "e", "c"]))).toBe("b");
});

test("nothing left means a new session; an open session not in the list takes the first remaining", () => {
  expect(successorSession(["a", "b"], "a", new Set(["a", "b"]))).toBeNull();
  expect(successorSession(["a", "b"], "x", new Set(["x", "a"]))).toBe("b");
});
