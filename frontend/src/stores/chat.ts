// The open session: its history, the turn that runs, its debug lines. The server is the truth;
// this store only follows its event stream (src/api/events.ts) and sends commands.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import { followSession } from "../api/events";
import type { ChatEntry, MessageAccepted, SessionDetail, UploadedImage } from "../api/types";
import * as speech from "../lib/speech";
import { useSessions } from "./sessions";

interface Streaming {
  agent: string;
  displayName: string;
  emoji: string;
  text: string;
}

export interface SessionConfig {
  active_agent: string;
  multi_agent_mode: string;
  symposion_agents: string[];
}

export const useChat = defineStore("chat", () => {
  const sessionId = ref<string | null>(null);
  const title = ref<string | null>(null);
  const history = ref<ChatEntry[]>([]);
  const debugLines = ref<string[]>([]);
  const config = ref<SessionConfig>({ active_agent: "aifred", multi_agent_mode: "standard", symposion_agents: [] });
  const running = ref(false);
  const streaming = ref<Streaming | null>(null);
  const toolStatus = ref("");
  const notice = ref("");
  const queued = ref<string[]>([]);
  // Images uploaded for the next message (they sit in the session's upload folder until it is sent).
  const pendingImages = ref<UploadedImage[]>([]);
  const uploading = ref(false);
  let source: EventSource | null = null;

  async function open(id: string): Promise<void> {
    source?.close();
    speech.stop(); // the other session's reading goes on for its other tabs, not here
    const detail = await api<SessionDetail>(`sessions/${id}`);
    sessionId.value = id;
    title.value = detail.title ?? null;
    history.value = detail.chat_history;
    debugLines.value = detail.debug_messages;
    config.value = detail.config as unknown as SessionConfig;
    running.value = detail.running;
    streaming.value = null;
    toolStatus.value = "";
    notice.value = "";
    queued.value = [];
    pendingImages.value = [];
    source = followSession(id, detail.last_event_id, handle);
  }

  function close(): void {
    source?.close();
    speech.stop();
    source = null;
    sessionId.value = null;
  }

  function handle(kind: string, data: Record<string, unknown>): void {
    switch (kind) {
      case "turn_started":
        running.value = true;
        notice.value = "";
        queued.value = queued.value.slice(1);
        history.value = [...history.value, data.user_entry as ChatEntry];
        break;
      case "message_queued":
        queued.value = [...queued.value, String(data.text)];
        break;
      case "agent_started":
        streaming.value = {
          agent: String(data.agent),
          displayName: String(data.display_name),
          emoji: String(data.emoji),
          text: "",
        };
        break;
      case "token":
        if (streaming.value) streaming.value.text += String(data.text);
        break;
      case "tool_status":
        toolStatus.value = String(data.text);
        break;
      case "message_added":
        history.value = [...history.value, data.entry as ChatEntry];
        streaming.value = null;
        toolStatus.value = "";
        break;
      case "message_updated": {
        // The bubble with what was spoken of it, once its reading is over.
        const entry = data.entry as ChatEntry;
        history.value = history.value.map((old) => (old.role === entry.role && old.timestamp === entry.timestamp ? entry : old));
        break;
      }
      case "history_replaced":
        history.value = data.chat_history as ChatEntry[];
        break;
      case "session_config":
        config.value = data as unknown as SessionConfig;
        break;
      case "session_title":
        title.value = String(data.title);
        if (sessionId.value) useSessions().retitle(sessionId.value, title.value);
        break;
      case "turn_rejected":
        notice.value = String(data.message);
        break;
      case "notice":
        notice.value = String(data.text);
        break;
      case "tts":
      case "tts_pause":
      case "tts_speak":
        speech.enqueue(kind, data);
        break;
      case "stop":
        speech.stop();
        break;
      case "turn_finished":
        running.value = false;
        streaming.value = null;
        toolStatus.value = "";
        void useSessions().refresh();
        break;
      case "debug":
        debugLines.value = [...debugLines.value, String(data.line)];
        break;
    }
  }

  async function send(text: string): Promise<void> {
    if (!sessionId.value) return;
    const images = pendingImages.value.map((image) => image.url);
    await api<MessageAccepted>(`sessions/${sessionId.value}/messages`, {
      method: "POST",
      body: JSON.stringify({ text, images }),
    });
    pendingImages.value = [];
  }

  async function uploadImages(files: File[]): Promise<void> {
    if (!sessionId.value || files.length === 0) return;
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    uploading.value = true;
    try {
      const stored = await api<UploadedImage[]>(`sessions/${sessionId.value}/images`, { method: "POST", body: form });
      pendingImages.value = [...pendingImages.value, ...stored];
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    } finally {
      uploading.value = false;
    }
  }

  async function removeImage(name: string): Promise<void> {
    if (!sessionId.value) return;
    await api<void>(`sessions/${sessionId.value}/images/${encodeURIComponent(name)}`, { method: "DELETE" });
    pendingImages.value = pendingImages.value.filter((image) => image.name !== name);
  }

  // Ends the turn and the reading aloud, in every tab of the session (the server sends "stop").
  async function stop(): Promise<void> {
    if (!sessionId.value) return;
    speech.stop();
    await api<{ cancelled: boolean }>(`sessions/${sessionId.value}/stop`, { method: "POST" });
  }

  async function configure(changes: Partial<SessionConfig>): Promise<void> {
    if (!sessionId.value) return;
    config.value = await api<SessionConfig>(`sessions/${sessionId.value}/config`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    });
  }

  return {
    sessionId, title, history, debugLines, config, running, streaming, toolStatus, notice, queued,
    pendingImages, uploading, open, close, send, stop, configure, uploadImages, removeImage,
  };
});
