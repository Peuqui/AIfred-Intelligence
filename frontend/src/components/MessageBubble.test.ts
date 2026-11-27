// A picture of a bubble that is gone from the server (its event deleted) says so instead of showing the
// broken-image icon; a picture from elsewhere is left alone.

import { flushPromises, mount } from "@vue/test-utils";
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

  it("shows a long text as its first lines, with a link that shows all of it and one that folds it again", async () => {
    // jsdom has no layout: the text needs 400 px, the first lines show 100.
    const restore = layout(400, 100);
    try {
      const lines = Array.from({ length: 30 }, (_, at) => `Zeile ${at + 1}`);
      const wrapper = userBubble(lines.join("\n"));
      await flushPromises();
      expect(wrapper.find(".content").classes()).toContain("clamped");
      expect(wrapper.find(".content").text()).toBe(lines.join("\n"));
      await wrapper.find(".more").trigger("click");
      expect(wrapper.find(".content").classes()).not.toContain("clamped");
      await wrapper.find(".more").trigger("click");
      expect(wrapper.find(".content").classes()).toContain("clamped");
    } finally {
      restore();
    }
  });

  it("has no link for a text that fits in its first lines", async () => {
    const restore = layout(60, 60);
    try {
      const wrapper = userBubble("Kurz");
      await flushPromises();
      expect(wrapper.find(".more").exists()).toBe(false);
    } finally {
      restore();
    }
  });

  it("shows a job's long request as its first lines too", async () => {
    const restore = layout(900, 100);
    try {
      setActivePinia(createPinia());
      const wrapper = mount(MessageBubble, {
        props: { entry: {
          role: "user", content: "Du führst einen geplanten Job aus.\n".repeat(20), agent: "scheduler",
          agent_display_name: "Horarium · Gebet", agent_emoji: "⏰", mode: "", timestamp: "", time_display: "06:45",
          has_audio: false, tts_note: "", metadata: { images: [] },
        }, speechOn: false },
      });
      await flushPromises();
      expect(wrapper.find(".content").classes()).toContain("clamped");
      expect(wrapper.find(".more").exists()).toBe(true);
    } finally {
      restore();
    }
  });
});

// The text's full height and the height its box shows, for every element (jsdom has no layout).
function layout(scrollHeight: number, clientHeight: number): () => void {
  const scroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
  const client = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, get: () => clientHeight });
  return () => {
    if (scroll) Object.defineProperty(HTMLElement.prototype, "scrollHeight", scroll);
    if (client) Object.defineProperty(HTMLElement.prototype, "clientHeight", client);
  };
}

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

describe("MessageBubble pinned question", () => {
  it("drops the space the fold left when pinning is switched off while the question sticks", async () => {
    // jsdom has no layout: the bubble is 300 high in full, 80 when folded.
    let height = 300;
    const offset = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => height });
    try {
      setActivePinia(createPinia());
      const entry = {
        role: "user", content: "Eine lange Frage", agent: "", agent_display_name: "", agent_emoji: "", mode: "", timestamp: "",
        time_display: "12:43", has_audio: false, tts_note: "", metadata: { images: [] },
      };
      const wrapper = mount(MessageBubble, { props: { entry, speechOn: false, pinned: true, stuck: false } });
      await flushPromises();
      height = 80;
      await wrapper.setProps({ stuck: true });
      await flushPromises();
      expect((wrapper.element as HTMLElement).style.marginBottom).toBe("220px");
      await wrapper.setProps({ pinned: false });
      await flushPromises();
      expect((wrapper.element as HTMLElement).style.marginBottom).toBe("");
    } finally {
      if (offset) Object.defineProperty(HTMLElement.prototype, "offsetHeight", offset);
    }
  });
});

describe("MessageBubble request of someone else", () => {
  it("stands on the left under its sender, as plain text, and not under the user's name", () => {
    setActivePinia(createPinia());
    const wrapper = mount(MessageBubble, {
      props: { entry: {
        role: "user", content: "Prüfe **die** Preise.", agent: "scheduler", agent_display_name: "Horarium · Deal-Check", agent_emoji: "⏰",
        mode: "", timestamp: "", time_display: "19:00", has_audio: false, tts_note: "", metadata: { images: [] },
      }, speechOn: false },
    });
    expect(wrapper.classes()).toContain("assistant");
    expect(wrapper.find(".user-head").exists()).toBe(false);
    expect(wrapper.find("header").text()).toContain("⏰ Horarium · Deal-Check");
    expect(wrapper.find(".content.plain").text()).toBe("Prüfe **die** Preise.");
  });
});
