// One decision for every place: page, PDF, audio and video → a tab at the view address, image → the dialog, anything
// else → the server's text preview.
import { beforeEach, expect, test, vi } from "vitest";
import { fileKind, viewFile, viewed } from "./fileViewer";

const open = vi.fn();
vi.stubGlobal("window", { open });

beforeEach(() => {
  viewed.value = null;
  open.mockClear();
});

test("files are told apart by their name", () => {
  expect(["a.HTML", "b.png", "c.mp3", "d.pdf", "e.docx", "f"].map(fileKind)).toEqual(["page", "image", "media", "media", "text", "text"]);
});

test("a page, a PDF and a sound open in a tab at the view address, the text preview is not asked", async () => {
  const text = vi.fn();
  for (const name of ["a.html", "b.pdf", "c.mp3", "d.MP4"]) await viewFile({ name, viewUrl: `/v/${name}`, text });
  expect(open.mock.calls.map((call) => call[0])).toEqual(["/v/a.html", "/v/b.pdf", "/v/c.mp3", "/v/d.MP4"]);
  expect(open).toHaveBeenCalledWith("/v/a.html", "_blank", "noopener");
  expect(text).not.toHaveBeenCalled();
  expect(viewed.value).toBeNull();
});

test("an image shows in the dialog from the view address", async () => {
  await viewFile({ name: "plot.png", viewUrl: "/api/x", text: vi.fn() });
  expect(viewed.value).toEqual({ name: "plot.png", imageUrl: "/api/x" });
  expect(open).not.toHaveBeenCalled();
});

test("any other file shows as the text preview; a failed one shows nothing and the place says why", async () => {
  await viewFile({ name: "a.py", viewUrl: "/v", text: async () => ({ name: "a.py", text: "print(1)" }) });
  expect(viewed.value).toEqual({ name: "a.py", text: "print(1)" });
  viewed.value = null;
  await viewFile({ name: "kaputt.docx", viewUrl: "/v", text: async () => null });
  expect(viewed.value).toBeNull();
  expect(open).not.toHaveBeenCalled();
});
