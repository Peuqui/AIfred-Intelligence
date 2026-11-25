// An agent's rights in the editor are always an explicit list (Peuqui 25.11.2026): no state "every tool", only helpers
// that set the list (select all / clear), a sentence that says new tools do not join by themselves, and a save that
// sends the list.
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, expect, test, vi } from "vitest";

type Init = { method?: string; body?: string };
const rights = {
  groups: [
    { name: "Web", tools: [{ name: "web_search", tier: 0, allowed: false }, { name: "web_fetch", tier: 0, allowed: true }] },
    { name: "Dateien", tools: [{ name: "read_file", tier: 0, allowed: false }] },
  ],
};
const persona = { display_name: "Pater", emoji: "x", color: "#7d8590", description: "", aliases: [], prompt_keys: [], languages: ["de"] };
const api = vi.fn(async (path: string, _init?: Init): Promise<unknown> => (path.endsWith("/persona") ? persona : path.endsWith("/tools") ? rights : []));
vi.mock("../api/client", () => ({ ApiError: class extends Error {}, api: (path: string, init?: Init) => api(path, init) }));
vi.mock("../i18n", () => ({ t: (key: string) => key, tCount: (key: string) => key }));
vi.mock("./ModalDialog.vue", () => ({ default: { template: "<div><slot /></div>" } }));
vi.mock("./PromptLayers.vue", () => ({ default: { template: "<div />" } }));

const { default: AgentEditor } = await import("./AgentEditor.vue");

beforeEach(() => {
  setActivePinia(createPinia());
  api.mockClear();
});

const openRights = async () => {
  const wrapper = mount(AgentEditor, { props: { agentId: "pater" } });
  await flushPromises();
  await wrapper.get(".tabs button:nth-child(3)").trigger("click");
  return wrapper;
};
const savedList = (): string[] => {
  const saved = api.mock.calls.find(([path, init]) => path.endsWith("/tools") && init?.method === "PUT");
  const body = JSON.parse(String(saved?.[1]?.body)) as { allowed: string[] };
  return body.allowed;
};

test("there is no state 'every tool': only the group switches are checkboxes, the pills show what the list holds", async () => {
  const wrapper = await openRights();
  expect(wrapper.findAll("input[type=checkbox]")).toHaveLength(rights.groups.length);   // one switch per group, no "every tool" box
  expect(wrapper.find("label.all").exists()).toBe(false);
  expect(wrapper.text()).not.toContain("agents_all_tools");
  const pressed = wrapper.findAll(".pill").map((pill) => [pill.text(), pill.attributes("aria-pressed")]);
  expect(pressed).toEqual([["web_search", "false"], ["web_fetch", "true"], ["read_file", "false"]]);
});

test("the sentence that new tools do not join by themselves stands in the editor as text, not as a tooltip", async () => {
  const wrapper = await openRights();
  const hint = wrapper.get("[data-test=rights-hint]");
  expect(hint.text()).toBe("agents_rights_hint");
  expect(hint.attributes("title")).toBeUndefined();
});

test("select all and clear set an explicit list, which is what gets saved", async () => {
  const wrapper = await openRights();
  await wrapper.get("[data-test=rights-select-all]").trigger("click");
  expect(wrapper.findAll(".pill").every((pill) => pill.attributes("aria-pressed") === "true")).toBe(true);
  await wrapper.get("[data-test=rights-save]").trigger("click");
  await flushPromises();
  expect(savedList().sort()).toEqual(["read_file", "web_fetch", "web_search"]);

  api.mockClear();
  await wrapper.get("[data-test=rights-clear]").trigger("click");
  expect(wrapper.findAll(".pill").some((pill) => pill.attributes("aria-pressed") === "true")).toBe(false);
  await wrapper.get("[data-test=rights-save]").trigger("click");
  await flushPromises();
  expect(savedList()).toEqual([]);
});

test("a single tool is toggled and saved in the list", async () => {
  const wrapper = await openRights();
  const readFile = wrapper.findAll(".pill").find((pill) => pill.text() === "read_file");
  await readFile?.trigger("click");
  await wrapper.get("[data-test=rights-save]").trigger("click");
  await flushPromises();
  expect(savedList().sort()).toEqual(["read_file", "web_fetch"]);
});
