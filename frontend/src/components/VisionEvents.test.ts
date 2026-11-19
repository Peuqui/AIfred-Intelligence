// The Casus after a burst: its events keep no single picture (they live in the film), so a row shows no picture
// address that leads nowhere, and the film button plays the burst films in the shared file viewer.
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, expect, test, vi } from "vitest";

const api = vi.fn();
vi.mock("../api/client", () => ({
  ApiError: class extends Error {},
  PROGRESS_POLL_MS: 1000,
  api: (path: string) => api(path),
  apiUrl: (path: string) => `/api/${path}`,
}));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string) => key, formatNumber: (value: number) => String(value) }));
vi.mock("vue-router", () => ({ useRoute: () => ({ query: {} }) }));
vi.mock("./help/HelpButton.vue", () => ({ default: { template: "<i />" } }));
vi.mock("./ImageLightbox.vue", () => ({ default: { template: "<i class='lightbox' />" } }));
const viewFile = vi.fn();
vi.mock("../lib/fileViewer", () => ({ viewFile: (place: unknown) => viewFile(place) }));
vi.stubGlobal("EventSource", class { addEventListener(): void {} close(): void {} });

const { default: VisionEvents } = await import("./VisionEvents.vue");

const FILM = "/_upload/vigilantia/films/cam/2026-11-04/c1_140300_wide.mp4";
const burstEvent = {
  id: 7, source_id: "cam", source_name: "Garten", event_type: "person", timestamp: "2026-11-04T14:03:00",
  date_display: "04.11.", time_display: "14:03", crop_url: "", has_frame: false, has_zoom: false, has_film: true,
  face_id: null, face_name: "", matched_name: "", description: "Ein Mann.", cluster_id: "c1", cluster_member_count: 0,
};
const pictureEvent = { ...burstEvent, id: 8, has_frame: true, has_film: false, cluster_id: "" };

function serve(film: unknown): void {
  api.mockImplementation(async (path: string) => {
    if (path.startsWith("vision/casus/events?")) return { events: [burstEvent, pictureEvent], total: 2, offset: 0, page_size: 50 };
    if (path === "vision/casus/choices") return { sources: [], types: [], faces: [], taggable_types: [] };
    if (path === "vision/casus/runs") return { analysis: { running: 0, last: 0, error: "" }, describe_all: { running: false, cancelling: false } };
    if (path.endsWith("/film")) return film;
    return {};
  });
}

beforeEach(() => {
  api.mockReset();
  viewFile.mockReset();
});

test("a row without a single picture renders no vision/frame image and no analyse button", async () => {
  serve({ clips: [], pictures: [] });
  const wrapper = mount(VisionEvents);
  await flushPromises();
  const [burstRow, pictureRow] = wrapper.findAll(".event");
  expect(burstRow?.findAll("img")).toHaveLength(0);
  expect(burstRow?.find(".none").exists()).toBe(true);
  expect(burstRow?.find("button[title*='casus_analyze']").exists()).toBe(false);
  expect(pictureRow?.find("img").attributes("src")).toBe("/api/vision/frame?id=8&w=80");
  expect(pictureRow?.find("button[title*='casus_analyze']").exists()).toBe(true);
});

test("the film button shows only where there is something to play", async () => {
  serve({ clips: [], pictures: [] });
  const wrapper = mount(VisionEvents);
  await flushPromises();
  const [burstRow, pictureRow] = wrapper.findAll(".event");
  expect(burstRow?.find("button[title='casus_film_tooltip']").exists()).toBe(true);
  expect(pictureRow?.find("button[title='casus_film_tooltip']").exists()).toBe(false);
});

test("a burst's film opens in the file viewer with its URL", async () => {
  serve({ clips: [{ label: "Weitwinkel", url: FILM }], pictures: [] });
  const wrapper = mount(VisionEvents);
  await flushPromises();
  await wrapper.find("button[title='casus_film_tooltip']").trigger("click");
  await flushPromises();
  expect(viewFile).toHaveBeenCalledTimes(1);
  expect(viewFile.mock.calls[0]?.[0]).toMatchObject({ name: "c1_140300_wide.mp4", viewUrl: FILM });
  expect(wrapper.find(".lightbox").exists()).toBe(false);
});

test("several burst films ask which one, then open it", async () => {
  const zoom = FILM.replace("wide", "zoom");
  serve({ clips: [{ label: "Weitwinkel", url: FILM }, { label: "Zoom", url: zoom }], pictures: [] });
  const wrapper = mount(VisionEvents);
  await flushPromises();
  await wrapper.find("button[title='casus_film_tooltip']").trigger("click");
  await flushPromises();
  expect(viewFile).not.toHaveBeenCalled();
  const zoomButton = wrapper.findAll(".actions button").find((button) => button.text() === "Zoom");
  await zoomButton?.trigger("click");
  expect(viewFile.mock.calls[0]?.[0]).toMatchObject({ viewUrl: zoom });
});

test("an occurrence without films runs its pictures as the slideshow", async () => {
  serve({ clips: [], pictures: [{ id: 1, has_zoom: false }, { id: 2, has_zoom: true }] });
  const wrapper = mount(VisionEvents);
  await flushPromises();
  await wrapper.find("button[title='casus_film_tooltip']").trigger("click");
  await flushPromises();
  expect(viewFile).not.toHaveBeenCalled();
  expect(wrapper.find(".lightbox").exists()).toBe(true);
});
