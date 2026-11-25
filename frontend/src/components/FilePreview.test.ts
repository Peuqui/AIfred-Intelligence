// A preview shows each kind of file as it is meant to be seen, and always offers the file in a tab of its own.
import { flushPromises, mount } from "@vue/test-utils";
import { expect, test, vi } from "vitest";
import type { ViewPlace } from "../lib/fileViewer";

vi.mock("../i18n", async () => {
  const { ref } = await import("vue");
  return { language: ref("de"), t: (key: string) => key };
});
vi.mock("../api/client", () => ({ ApiError: class extends Error { detail = "serverseitiger Grund"; } }));
const failure = async (): Promise<never> => { const { ApiError } = await import("../api/client"); throw new (ApiError as unknown as new () => Error)(); };

const { default: FilePreview } = await import("./FilePreview.vue");

const place = (name: string, text?: ViewPlace["text"]): ViewPlace => ({ name, viewUrl: `/v/${name}`, text });
const show = async (given: ViewPlace) => {
  const wrapper = mount(FilePreview, { props: { place: given } });
  await flushPromises();
  return wrapper;
};

test("every kind shows in its own element at the view address", async () => {
  expect((await show(place("a.pdf"))).find("iframe").attributes("src")).toBe("/v/a.pdf");
  expect((await show(place("a.html"))).find("iframe").attributes("src")).toBe("/v/a.html");
  expect((await show(place("a.png"))).find("img").attributes("src")).toBe("/v/a.png");
  expect((await show(place("a.mp3"))).find("audio").attributes("src")).toBe("/v/a.mp3");
  expect((await show(place("a.mp4"))).find("video").attributes("src")).toBe("/v/a.mp4");
});

test("every kind has the button that opens a tab of its own at the view address", async () => {
  for (const name of ["a.pdf", "a.html", "a.png", "a.mp3", "a.mp4", "a.py"]) {
    const link = (await show(place(name, async () => ({ name, text: "x" })))).find("a.tab");
    expect(link.attributes("href"), name).toBe(`/v/${name}`);
    expect(link.attributes("target"), name).toBe("_blank");
    expect(link.text(), name).toContain("doc_open_page");
  }
});

test("a text file shows as text, Markdown and tables formatted; the embedded kinds ask for no text", async () => {
  const text = vi.fn(async () => ({ name: "a.py", text: "print(1)" }));
  const code = await show(place("a.py", text));
  expect(code.find("pre").text()).toBe("print(1)");
  const table = await show(place("t.csv", async () => ({ name: "t.csv", text: "| a | b |\n|---|---|\n| 1 | 2 |" })));
  expect(table.find("table").exists()).toBe(true);
  const unused = vi.fn();
  await show(place("a.pdf", unused));
  expect(unused).not.toHaveBeenCalled();
});

test("a text that fails shows its reason, and a file without a text preview says so", async () => {
  const failing = await show(place("a.docx", failure));
  expect(failing.find(".notice").text()).toContain("serverseitiger Grund");
  expect(failing.find("pre").exists()).toBe(false);
  expect((await show(place("a.docx"))).find(".notice").text()).toContain("preview_no_text");
});

test("the next file replaces the shown one, and a slower answer of the earlier one is dropped", async () => {
  let release: (value: { name: string; text: string }) => void = () => {};
  const slow = new Promise<{ name: string; text: string }>((resolve) => { release = resolve; });
  const wrapper = mount(FilePreview, { props: { place: place("a.txt", () => slow) } });
  await wrapper.setProps({ place: place("b.txt", async () => ({ name: "b.txt", text: "zweite" })) });
  await flushPromises();
  release({ name: "a.txt", text: "erste" });
  await flushPromises();
  expect(wrapper.find("pre").text()).toBe("zweite");
});
