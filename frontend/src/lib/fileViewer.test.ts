// One decision for every place: the kind of a file by its name, and the dialog shows whatever place it is given
// (FilePreview decides how; the dialog opens no tab of its own).
import { beforeEach, expect, test } from "vitest";
import { areaPlace, fileKind, viewFile, viewed } from "./fileViewer";

beforeEach(() => {
  viewed.value = null;
});

test("files are told apart by their name", () => {
  expect(["a.HTML", "b.png", "c.mp3", "d.pdf", "e.docx", "f", "g.MP4", "h.md"].map(fileKind)).toEqual([
    "page", "image", "audio", "pdf", "text", "text", "video", "text",
  ]);
});

test("viewFile gives the dialog the place as it is, whatever the kind of the file", () => {
  for (const name of ["a.html", "b.pdf", "c.mp3", "d.MP4", "e.png", "f.py"]) {
    const place = { name, viewUrl: `/v/${name}` };
    viewFile(place);
    expect(viewed.value).toEqual(place);
  }
});

test("a place of a file area names the preview and the text address of the file, with the path encoded", () => {
  const place = areaPlace("documents", { basename: "Bericht 1.pdf", path: "documents://a b/Bericht 1.pdf" });
  expect(place.name).toBe("Bericht 1.pdf");
  expect(place.viewUrl).toContain("files/documents/preview?path=documents%3A%2F%2Fa%20b%2FBericht%201.pdf");
  expect(place.text).toBeTypeOf("function");
});
