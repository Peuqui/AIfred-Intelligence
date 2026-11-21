// The places of chats that are gone go with them; those of the listed sessions stay.
import { afterEach, expect, test } from "vitest";
import { keepPlacesOf, readPlace, savePlace } from "./chatPlace";

afterEach(() => localStorage.clear());

test("only the places of the sessions in the list are kept, other keys of the page are left alone", () => {
  savePlace("a", { key: "k1", offset: 3 });
  savePlace("b", { key: "k2", offset: 4 });
  localStorage.setItem("aifred-enter-sends", "true");
  keepPlacesOf(["a"]);
  expect(readPlace("a")).toEqual({ key: "k1", offset: 3 });
  expect(readPlace("b")).toBeNull();
  expect(localStorage.getItem("aifred-enter-sends")).toBe("true");
});
