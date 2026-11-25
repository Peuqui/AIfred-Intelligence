import { expect, test } from "vitest";
import { SPLIT, clampSplit, splitAt } from "./splitter";

test("the share never leaves its bounds", () => {
  expect(clampSplit(0.01)).toBe(SPLIT.min);
  expect(clampSplit(0.99)).toBe(SPLIT.max);
  expect(clampSplit(0.5)).toBe(0.5);
});

test("a pointer position becomes the share of the container, bounded", () => {
  const container = { left: 100, width: 1000 };
  expect(splitAt(600, container)).toBe(0.5);
  expect(splitAt(0, container)).toBe(SPLIT.min);
  expect(splitAt(5000, container)).toBe(SPLIT.max);
});
