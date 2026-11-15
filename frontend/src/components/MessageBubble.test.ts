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
