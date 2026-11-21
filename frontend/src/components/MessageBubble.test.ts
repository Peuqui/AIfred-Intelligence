// A picture of a bubble that is gone from the server (its event deleted) says so instead of showing the
// broken-image icon; a picture from elsewhere is left alone.

import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { describe, expect, it, vi } from "vitest";
import MessageBubble from "./MessageBubble.vue";

// jsdom has no layout: the bubble measures its folded question with a ResizeObserver.
vi.stubGlobal("ResizeObserver", class { observe(): void {} disconnect(): void {} });

function bubble(content: string): ReturnType<typeof mount> {
  setActivePinia(createPinia());
  return mount(MessageBubble, {
    props: { entry: {
      role: "assistant", content, agent: "vision", agent_display_name: "Vision", agent_emoji: "📷", mode: "", timestamp: "",
      time_display: "14:44", has_audio: false, tts_note: "",
    }, speechOn: false },
  });
}

describe("MessageBubble pictures", () => {
  it("replaces an uploaded picture that does not load with a note", async () => {
    const wrapper = bubble("Person erkannt\n\n![Person](/_upload/vigilantia/motion/cam/2026-11-15/a.jpg)");
    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find(".gone").exists()).toBe(true);
  });

  it("leaves a picture from another place alone", async () => {
    const wrapper = bubble("![x](https://example.com/a.png)");
    await wrapper.find("img").trigger("error");
    expect(wrapper.find("img").exists()).toBe(true);
    expect(wrapper.find(".gone").exists()).toBe(false);
  });
});

function userBubble(content: string, images: { name: string; url: string }[] = []): ReturnType<typeof mount> {
  setActivePinia(createPinia());
  return mount(MessageBubble, {
    props: { entry: {
      role: "user", content, agent: "", agent_display_name: "", agent_emoji: "", mode: "", timestamp: "",
      time_display: "12:43", has_audio: false, tts_note: "", metadata: { images },
    }, speechOn: false },
  });
}

describe("MessageBubble user text", () => {
  it("shows tags in the user's text as text, and all of it", () => {
    const text = 'Dann <script type="module"> mit\nimport * as THREE from "three";\nABLAUF: acht Phasen';
    const wrapper = userBubble(text);
    expect(wrapper.find(".content").text()).toBe(text);
    expect(wrapper.find("script").exists()).toBe(false);
  });

  it("shows the images of the message above the text and keeps the text as typed", () => {
    const wrapper = userBubble("Was ist das?", [{ name: "hund.jpg", url: "/_upload/images/s/hund.jpg" }]);
    expect(wrapper.find(".thumbs img").attributes("src")).toBe("/_upload/images/s/hund.jpg");
    expect(wrapper.find(".content").text()).toBe("Was ist das?");
  });

  it("shows a long text clipped, with a link that shows all of it and one that folds it again", async () => {
    const lines = Array.from({ length: 30 }, (_, at) => `Zeile ${at + 1}`);
    const wrapper = userBubble(lines.join("\n"));
    expect(wrapper.find(".content").text()).toBe(`${lines.slice(0, 8).join("\n")}…`);
    await wrapper.find(".more").trigger("click");
    expect(wrapper.find(".content").text()).toBe(lines.join("\n"));
    await wrapper.find(".more").trigger("click");
    expect(wrapper.find(".content").text()).toBe(`${lines.slice(0, 8).join("\n")}…`);
  });

  it("has no link for a short text", () => {
    expect(userBubble("Kurz").find(".more").exists()).toBe(false);
  });
});

function spokenBubble(readingRequested: boolean): ReturnType<typeof mount> {
  setActivePinia(createPinia());
  return mount(MessageBubble, {
    props: { entry: {
      role: "assistant", content: "Antwort", agent: "aifred", agent_display_name: "AIfred", agent_emoji: "🎩", mode: "",
      timestamp: "t1", time_display: "15:33", has_audio: false, tts_note: "",
    }, speechOn: true, readingRequested },
  });
}

describe("MessageBubble reading button", () => {
  it("asks for the reading of its bubble", async () => {
    const wrapper = spokenBubble(false);
    const button = wrapper.find("button.replay");
    expect(button.classes()).not.toContain("requested");
    await button.trigger("click");
    expect(wrapper.emitted("readAgain")).toEqual([["t1"]]);
  });

  it("turns while the reading is asked for and takes no second click", async () => {
    const wrapper = spokenBubble(true);
    const button = wrapper.find("button.replay");
    expect(button.classes()).toContain("requested");
    expect(button.attributes("disabled")).toBeDefined();
    await button.trigger("click");
    expect(wrapper.emitted("readAgain")).toBeUndefined();
  });
});
