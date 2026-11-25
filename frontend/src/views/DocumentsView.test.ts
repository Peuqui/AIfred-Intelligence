// The documents page: beside the list the preview of the file clicked, with a divider that keeps its bounds and its
// place; where there is no room beside the list, a click opens the dialog instead.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";
import { defineComponent, h } from "vue";
import type { Settings } from "../api/types";

vi.mock("../i18n", async () => {
  const { ref } = await import("vue");
  return { language: ref("de"), t: (key: string) => key };
});
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  PROGRESS_POLL_MS: 1000,
  api: async () => [],
  apiUrl: (path: string) => `/api/${path}`,
}));
// VueFinder itself is not under test: a stand-in that reports a file as selected, on a button.
const clearSelection = vi.fn();
vi.mock("../components/FileBrowser.vue", () => ({
  __esModule: true,
  default: defineComponent({
    props: { area: { type: String, required: true } },
    emits: ["select", "open"],
    setup: (props, { emit, expose }) => {
      expose({ clearSelection });
      const file = { type: "file", basename: "Bericht.pdf", path: `${props.area}://Bericht.pdf` };
      return () => h("div", { class: "browser" }, [
        h("button", { class: "pick-file", onClick: () => emit("select", [file]) }),
        h("button", { class: "pick-folder", onClick: () => emit("select", [{ type: "dir", basename: "d", path: "d" }]) }),
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
  const wrapper = mount(DocumentsView, { global: { stubs: { FilePreview: { props: ["place"], template: "<i class='preview'>{{ place.name }}</i>" } } } });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  viewed.value = null;
  clearSelection.mockReset();
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
