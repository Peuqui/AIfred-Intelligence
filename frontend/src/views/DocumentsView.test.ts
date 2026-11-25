// The documents page: beside the list the preview of the file clicked, with a divider that keeps its bounds and its
// place; where there is no room beside the list, a click opens the dialog instead.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import { defineComponent, h } from "vue";
import type { Settings } from "../api/types";

vi.mock("../i18n", async () => {
  const { ref } = await import("vue");
  const t = (key: string, values?: Record<string, string | number>) => (values ? `${key} ${JSON.stringify(values)}` : key);
  return { language: ref("de"), t, tCount: (key: string, count: number) => `${key}:${count}` };
});
const api = vi.fn(async (path: string, _options?: { body?: string }): Promise<unknown> => (path === "documents/sources" ? sources : path === "documents/index" ? finished : []));
let sources: unknown[] = [];
const finished = { running: false, current: "", done: 0, total: 0, indexed: 0, chunks: 0, failed: [] };
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  PROGRESS_POLL_MS: 1000,
  api: (path: string, options?: { body?: string }) => api(path, options),
  apiUrl: (path: string) => `/api/${path}`,
}));
// VueFinder itself is not under test: a stand-in that reports a file as selected, on a button, and shows what the page
// puts into its slots (menu bar, status bar), the label and the ids of the extra menu entries it was given.
const clearSelection = vi.fn();
const refresh = vi.fn();
vi.mock("../components/FileBrowser.vue", () => ({
  __esModule: true,
  default: defineComponent({
    props: { area: { type: String, required: true }, label: { type: String, default: "" }, menuItems: { type: Array, default: () => [] } },
    emits: ["select", "open", "pathChange", "refused", "uploaded"],
    setup: (props, { emit, expose, slots }) => {
      expose({ clearSelection, refresh });
      const file = { type: "file", basename: "Bericht.pdf", path: `${props.area}://Bericht.pdf` };
      return () => h("div", { class: "browser", "data-label": props.label, "data-menu": (props.menuItems as { id: string }[]).map((item) => item.id).join(",") }, [
        h("button", { class: "pick-file", onClick: () => emit("select", [file]) }),
        h("button", { class: "pick-folder", onClick: () => emit("select", [{ type: "dir", basename: "d", path: "d" }]) }),
        h("button", { class: "go-folder", onClick: () => emit("pathChange", `${props.area}://Unter`) }),
        h("button", { class: "refuse", onClick: () => emit("refused", "x.exe", "x.exe: Dateityp nicht erlaubt") }),
        h("button", { class: "uploaded", onClick: () => emit("uploaded") }),
        h("div", { class: "menubar" }, slots["menubar-end"]?.()),
        h("div", { class: "statusbar" }, slots["status-bar"]?.()),
      ]);
    },
  }),
}));

// The device preferences read matchMedia at import; jsdom has none.
window.matchMedia = ((query: string) => ({ matches: false, media: query, addEventListener: () => undefined, removeEventListener: () => undefined })) as unknown as typeof window.matchMedia;

const { default: DocumentsView } = await import("./DocumentsView.vue");
const { useSettings } = await import("../stores/settings");
const { viewed } = await import("../lib/fileViewer");
const { documentsSplit } = await import("../lib/devicePrefs");

async function page(sessionsColumnMinPx: number) {
  const settings = useSettings();
  settings.current = { sessions_column_min_window_px: sessionsColumnMinPx } as unknown as Settings;
  const wrapper = mount(DocumentsView, {
    global: { stubs: {
      FilePreview: { props: ["place"], template: "<i class='preview'>{{ place.name }}</i>" },
      IndexCare: { emits: ["close", "changed"], template: "<i class='index-care' @click=\"$emit('changed')\" />" },
    } },
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  viewed.value = null;
  clearSelection.mockReset();
  refresh.mockReset();
  api.mockClear();
  sources = [];
  documentsSplit.value = 0.4;
  Object.defineProperty(window, "innerWidth", { value: 1000, configurable: true });
});

test("with room, a click on a file shows it beside the list and opens no dialog; a folder leaves the preview as it is", async () => {
  const wrapper = await page(500);
  expect(wrapper.find(".none").exists()).toBe(true);
  await wrapper.find(".pick-file").trigger("click");
  expect(wrapper.find(".preview").text()).toBe("Bericht.pdf");
  await wrapper.find(".pick-folder").trigger("click");
  expect(wrapper.find(".preview").text()).toBe("Bericht.pdf");
  expect(viewed.value).toBeNull();
});

test("without room there is no second column: a click opens the dialog and frees the selection", async () => {
  const wrapper = await page(5000);
  expect(wrapper.find(".divider-handle").exists()).toBe(false);
  expect(wrapper.find(".side").exists()).toBe(false);
  await wrapper.find(".pick-file").trigger("click");
  expect(viewed.value?.name).toBe("Bericht.pdf");
  expect(clearSelection).toHaveBeenCalledOnce();
});

test("dragging the divider sets the share of the list, held between its bounds, and the keys move it by steps", async () => {
  const wrapper = await page(500);
  const workspace = wrapper.find(".workspace").element;
  workspace.getBoundingClientRect = () => ({ left: 0, width: 1000 }) as DOMRect;
  const move = (x: number) => window.dispatchEvent(new MouseEvent("pointermove", { clientX: x }));
  await wrapper.find(".divider-handle").trigger("pointerdown");
  move(600);
  expect(documentsSplit.value).toBe(0.6);
  move(10);
  expect(documentsSplit.value).toBe(0.2);
  move(990);
  expect(documentsSplit.value).toBe(0.75);
  window.dispatchEvent(new MouseEvent("pointerup"));
  move(300);
  expect(documentsSplit.value).toBe(0.75);
  await wrapper.find(".divider-handle").trigger("keydown", { key: "ArrowLeft" });
  expect(documentsSplit.value).toBeCloseTo(0.73);
});

test("the share is kept on this device and a stored share outside the bounds is not taken", async () => {
  documentsSplit.value = 0.55;
  await new Promise((resolve) => setTimeout(resolve));
  expect(localStorage.getItem("aifred-documents-split")).toBe("0.55");
  vi.resetModules();
  localStorage.setItem("aifred-documents-split", "0.99");
  expect((await import("../lib/devicePrefs")).documentsSplit.value).toBe(0.4);
  localStorage.setItem("aifred-documents-split", "0.3");
  vi.resetModules();
  expect((await import("../lib/devicePrefs")).documentsSplit.value).toBe(0.3);
});

// ── the search index ──

const running = { running: true, current: "a.md", done: 1, total: 3, indexed: 1, chunks: 4, failed: [] };
const ended = { running: false, current: "", done: 2, total: 2, indexed: 1, chunks: 5, failed: [{ path: "x.pdf", reason: "kaputt" }] };

test("the status bar shows how far a run is, then its outcome with every failed file, and the outcome can be closed", async () => {
  const wrapper = await page(500);
  const { useDocuments } = await import("../stores/documents");
  const docs = useDocuments();
  expect(wrapper.find(".statusbar").text()).toBe("");
  docs.indexing = running;
  await flushPromises();
  expect(wrapper.find(".statusbar .progress").text()).toContain('"current":2');
  expect(wrapper.find(".statusbar .progress").text()).toContain('"name":"a.md"');
  docs.indexing = ended;
  await flushPromises();
  expect(wrapper.find(".statusbar .progress").exists()).toBe(false);
  expect(wrapper.find(".statusbar .outcome").text()).toContain("doc_index_folder_done");
  expect(wrapper.find(".statusbar .outcome").text()).toContain("doc_index_folder_failed_suffix");
  expect(wrapper.find(".statusbar .failure").text()).toContain("x.pdf: kaputt");
  await wrapper.find(".statusbar .outcome button").trigger("click");
  expect(docs.indexing).toBeNull();
  expect(wrapper.find(".statusbar .outcome").exists()).toBe(false);
});

test("the list is listed again when a run has ended, not while it runs", async () => {
  await page(500);
  const { useDocuments } = await import("../stores/documents");
  const docs = useDocuments();
  docs.indexing = running;
  await flushPromises();
  expect(refresh).not.toHaveBeenCalled();
  docs.indexing = ended;
  await flushPromises();
  expect(refresh).toHaveBeenCalledOnce();
});

test("the button indexes the folder shown: the path VueFinder reports, everything below it; it waits while a run goes on", async () => {
  const wrapper = await page(500);
  const button = () => wrapper.find(".menubar .index-tools button");
  await wrapper.find(".go-folder").trigger("click");
  await button().trigger("click");
  const call = api.mock.calls.find(([path]) => path === "documents/local/index");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ folder: "", files: [], folders: ["Unter"] });
  const { useDocuments } = await import("../stores/documents");
  useDocuments().indexing = running;
  await flushPromises();
  expect(button().attributes("disabled")).toBeDefined();
});

test("a source's folder goes to that source's index endpoint, and the area's own name is the source's label", async () => {
  sources = [{ key: "local", label: "Dokumente", writable: true }, { key: "nextcloud_files", label: "Nextcloud", writable: true }];
  const wrapper = await page(500);
  expect(wrapper.find(".browser").attributes("data-label")).toBe("Dokumente");
  await wrapper.find(".source select").setValue("nextcloud_files");
  await flushPromises();
  expect(wrapper.find(".browser").attributes("data-label")).toBe("Nextcloud");
  await wrapper.find(".go-folder").trigger("click");
  await wrapper.find(".menubar .index-tools button").trigger("click");
  const call = api.mock.calls.find(([path]) => path === "documents/source/nextcloud_files/index");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({ files: [], folders: ["Unter"] });
});

test("the second button opens the index care, which lists the folder again when it changed something", async () => {
  const wrapper = await page(500);
  expect(wrapper.find(".index-care").exists()).toBe(false);
  await wrapper.findAll(".menubar .index-tools button")[1]?.trigger("click");
  expect(wrapper.find(".index-care").exists()).toBe(true);
  await wrapper.find(".index-care").trigger("click");
  expect(refresh).toHaveBeenCalledOnce();
});

test("the path is copied only in the local folder", async () => {
  sources = [{ key: "local", label: "Dokumente", writable: true }, { key: "nextcloud_files", label: "Nextcloud", writable: true }];
  const wrapper = await page(500);
  expect(wrapper.find(".browser").attributes("data-menu")).toBe("aifred_index,aifred_index_folder,aifred_deindex,aifred_copy_path");
  await wrapper.find(".source select").setValue("nextcloud_files");
  await flushPromises();
  expect(wrapper.find(".browser").attributes("data-menu")).toBe("aifred_index,aifred_index_folder,aifred_deindex");
});

test("a file the server refused on upload shows its reason once until it is closed; a finished upload asks for the run it started", async () => {
  const wrapper = await page(500);
  await wrapper.find(".refuse").trigger("click");
  await wrapper.find(".refuse").trigger("click");
  expect(wrapper.findAll(".notice")).toHaveLength(1);
  expect(wrapper.find(".notice").text()).toContain("x.exe: Dateityp nicht erlaubt");
  await wrapper.find(".notice button").trigger("click");
  expect(wrapper.find(".notice").exists()).toBe(false);
  api.mockClear();
  await wrapper.find(".uploaded").trigger("click");
  expect(api).toHaveBeenCalledWith("documents/index", undefined);
});
