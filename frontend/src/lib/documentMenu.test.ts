// The context-menu entries of the index: which appear for which selection, and what each does with it.
import type { DirEntry, Item } from "vuefinder";
import { expect, test, vi } from "vitest";

vi.mock("../i18n", () => ({ t: (key: string) => key }));

const { indexMenuItems } = await import("./documentMenu");

type Entry = DirEntry & { indexable?: boolean; index_chunks?: number };
const entry = (name: string, extra: Partial<Entry> = {}): Entry =>
  ({ basename: name, path: `documents://${name}`, type: "file", dir: "documents://", ...extra }) as Entry;
const app = {} as Parameters<Item["show"]>[0];

function menu(copyPath?: (path: string) => void) {
  const actions = { index: vi.fn(), deindex: vi.fn(), copyPath };
  const items = indexMenuItems(actions);
  const shown = (selected: Entry[]) =>
    items.filter((item) => item.show(app, { searchQuery: "", items: selected, target: selected[0] ?? null })).map((item) => item.id);
  const run = (id: string, selected: Entry[]) => items.find((item) => item.id === id)?.action(app, selected);
  return { actions, shown, run };
}

const text = entry("a.md", { indexable: true });
const picture = entry("b.png", { indexable: false });
const known = entry("c.pdf", { indexable: true, index_chunks: 4 });
const folder = entry("Ordner", { type: "dir" });

test("a file the index reads is offered for indexing, one it cannot read is not", () => {
  const { shown } = menu();
  expect(shown([text])).toContain("aifred_index");
  expect(shown([picture])).not.toContain("aifred_index");
  expect(shown([picture, text])).toContain("aifred_index");
});

test("taking out of the index is offered only where a file has sections in it", () => {
  const { shown } = menu();
  expect(shown([text])).not.toContain("aifred_deindex");
  expect(shown([known])).toContain("aifred_deindex");
  expect(shown([text, known])).toContain("aifred_deindex");
});

test("a folder offers indexing of the folder, not of files", () => {
  const { shown } = menu();
  expect(shown([folder])).toEqual(["aifred_index_folder"]);
  expect(shown([text])).not.toContain("aifred_index_folder");
});

test("indexing hands over only what the index reads; the folder entry only folders; taking out only what is indexed", () => {
  const { actions, run } = menu();
  run("aifred_index", [text, picture, known]);
  expect(actions.index).toHaveBeenCalledWith([text, known]);
  run("aifred_index_folder", [folder, text]);
  expect(actions.index).toHaveBeenLastCalledWith([folder]);
  run("aifred_deindex", [text, known]);
  expect(actions.deindex).toHaveBeenCalledWith([known]);
});

test("the path of a file is offered only where the page gives a way to copy it (the local folder), for one file", () => {
  expect(menu().shown([text])).not.toContain("aifred_copy_path");
  const copy = vi.fn();
  const { shown, run } = menu(copy);
  expect(shown([text])).toContain("aifred_copy_path");
  expect(shown([text, known])).not.toContain("aifred_copy_path");
  expect(shown([folder])).not.toContain("aifred_copy_path");
  run("aifred_copy_path", [entry("Unter/a.md", { indexable: true })]);
  expect(copy).toHaveBeenCalledWith("Unter/a.md");
});
