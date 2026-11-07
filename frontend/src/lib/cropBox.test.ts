import { expect, test } from "vitest";
import { MIN_SIDE, WHOLE, pulled } from "./cropBox";

test("a corner moves its two edges, the opposite ones stay", () => {
  expect(pulled(WHOLE, "nw", 20, 30)).toEqual({ x: 20, y: 30, width: 80, height: 70 });
  expect(pulled(WHOLE, "se", -25, -40)).toEqual({ x: 0, y: 0, width: 75, height: 60 });
  expect(pulled(WHOLE, "e", -10, 50)).toEqual({ x: 0, y: 0, width: 90, height: 100 });   // an edge ignores the other axis
});

test("the box stays inside the image and keeps its smallest side", () => {
  expect(pulled(WHOLE, "w", 200, 0)).toEqual({ x: 100 - MIN_SIDE, y: 0, width: MIN_SIDE, height: 100 });
  expect(pulled(WHOLE, "n", -30, 0)).toEqual(WHOLE);
  const small = { x: 10, y: 10, width: 30, height: 30 };
  expect(pulled(small, "s", 0, 500)).toEqual({ x: 10, y: 10, width: 30, height: 90 });
  expect(pulled(small, "e", -500, 0)).toEqual({ x: 10, y: 10, width: MIN_SIDE, height: 30 });
});

test("the box itself moves as a whole, up to the image's edges", () => {
  const small = { x: 10, y: 10, width: 30, height: 30 };
  expect(pulled(small, "", 20, 5)).toEqual({ x: 30, y: 15, width: 30, height: 30 });
  expect(pulled(small, "", 500, -500)).toEqual({ x: 70, y: 0, width: 30, height: 30 });
});
