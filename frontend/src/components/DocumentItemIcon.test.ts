// The icon in VueFinder's list: the badge with the number of sections appears only for a file that has sections in the
// index, in the grid and in the list; a folder or a file not in the index has none.
import { mount } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import type { IndexedEntry } from "../lib/documentIndex";

vi.mock("../i18n", () => ({ tCount: (key: string, count: number) => `${key}:${count}` }));

const { default: DocumentItemIcon } = await import("./DocumentItemIcon.vue");

const entry = (extra: Partial<IndexedEntry>): IndexedEntry =>
  ({ basename: "a.pdf", extension: "pdf", path: "documents://a.pdf", type: "file", ...extra }) as IndexedEntry;
const badge = (item: IndexedEntry, view: "grid" | "list") => mount(DocumentItemIcon, { props: { item, view } }).find(".index-badge");

test("the badge shows the number of sections of an indexed file, in the grid and in the list", () => {
  for (const view of ["grid", "list"] as const) expect(badge(entry({ index_chunks: 3 }), view).text()).toBe("doc_chunk_count:3");
});

test("no badge for a file that is not in the index, whether the server says 0 or says nothing", () => {
  for (const view of ["grid", "list"] as const) {
    expect(badge(entry({ index_chunks: 0 }), view).exists()).toBe(false);
    expect(badge(entry({}), view).exists()).toBe(false);
  }
});

test("a folder draws the folder, a file the file with its type in the grid only", () => {
  const folder = mount(DocumentItemIcon, { props: { item: entry({ type: "dir" }), view: "grid" } });
  expect(folder.find("svg.vuefinder__item-icon__folder").exists()).toBe(true);
  expect(folder.find(".vuefinder__item-icon__extension").exists()).toBe(false);
  expect(mount(DocumentItemIcon, { props: { item: entry({}), view: "grid" } }).find(".vuefinder__item-icon__extension").text()).toBe("pdf");
  expect(mount(DocumentItemIcon, { props: { item: entry({}), view: "list" } }).find(".vuefinder__item-icon__extension").exists()).toBe(false);
});
