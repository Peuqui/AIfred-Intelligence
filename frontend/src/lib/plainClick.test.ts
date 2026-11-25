import { expect, test } from "vitest";
import { plainClick } from "./plainClick";

const down = { x: 100, y: 100, at: 1000, plain: true };
const up = (dx: number, dy: number, after: number) => ({ clientX: 100 + dx, clientY: 100 + dy, timeStamp: 1000 + after });

test("a short, still press of the primary button is a click", () => {
  expect(plainClick(down, up(2, 3, 120), -Infinity)).toBe(true);
});

test("a long press (the context menu), a moved pointer (frame, scrolling) or Ctrl/Shift is none", () => {
  expect(plainClick(down, up(0, 0, 600), -Infinity)).toBe(false);
  expect(plainClick(down, up(30, 0, 120), -Infinity)).toBe(false);
  expect(plainClick({ ...down, plain: false }, up(0, 0, 120), -Infinity)).toBe(false);
});

test("the second click of a double click right after a folder opened is none", () => {
  expect(plainClick(down, up(0, 0, 80), 800)).toBe(false);
  expect(plainClick(down, up(0, 0, 80), 300)).toBe(true);
});
