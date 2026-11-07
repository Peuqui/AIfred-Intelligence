// The open session: its history, the turn that runs, its debug lines. The server is the truth;
// this store only follows its event stream (src/api/events.ts) and sends commands.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import { followSession } from "../api/events";
import type {
  ChatEntry,
  DictatedTextResult,
  DictationAccepted,
  DictationSettings,
  MessageAccepted,
  SessionConfig,
  SessionDetail,
  SpeechSettings,
  UploadedImage,
} from "../api/types";
import * as speech from "../lib/speech";
import { useSessions } from "./sessions";

interface Streaming {
  agent: string;
  displayName: string;
  emoji: string;
  text: string;
}


export const useChat = defineStore("chat", () => {
  const sessionId = ref<string | null>(null);
  const title = ref<string | null>(null);
  const history = ref<ChatEntry[]>([]);
  const debugLines = ref<string[]>([]);
  const config = ref<SessionConfig>({ active_agent: "aifred", multi_agent_mode: "standard", symposion_agents: [], incognito: false });
  const running = ref(false);
  const streaming = ref<Streaming | null>(null);
  const toolStatus = ref("");
  const notice = ref("");
  const queued = ref<string[]>([]);
  // Images uploaded for the next message (they sit in the session's upload folder until it is sent).
  const pendingImages = ref<UploadedImage[]>([]);
  const uploading = ref(false);
  // The text in the input field; a dictation in edit mode lands here.
  const draft = ref("");
  // Dictations this tab sent that are not finished yet: only this tab asks their questions and
  // takes their text into its input field.
  const dictations = ref<string[]>([]);
  // Who recognizes a dictation and whether it goes into the input field first (settings.json).
  const dictationSettings = ref<DictationSettings>({ engine: "stt_suite", show_transcription: false });
  // Whether speech output is on (settings.json): a reply's button to read it aloud again shows then.
  const speechSettings = ref<SpeechSettings>({ enable_tts: false, tts_autoplay: false });
  // Every reply of the session is being read into new audio (its button is off meanwhile).
  const regenerating = ref(false);
  let source: EventSource | null = null;

  async function open(id: string): Promise<void> {
    source?.close();
    speech.stop(); // the other session's reading goes on for its other tabs, not here
    const detail = await api<SessionDetail>(`sessions/${id}`);
    sessionId.value = id;
    title.value = detail.title ?? null;
    history.value = detail.chat_history;
    debugLines.value = detail.debug_messages;
    config.value = detail.config;
    running.value = detail.running;
    regenerating.value = detail.speech_regenerating;
    streaming.value = null;
    toolStatus.value = "";
    notice.value = "";
    queued.value = [];
    pendingImages.value = [];
    draft.value = "";
    dictations.value = [];
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
      case "speech_regenerating":
        regenerating.value = Boolean(data.running);
        break;
      case "dictation_question":
        if (dictations.value.includes(String(data.job_id))) void answerDictation(String(data.job_id), String(data.text));
        break;
      case "dictation_text":
        if (dictations.value.includes(String(data.job_id))) {
          draft.value = draft.value.trim() ? `${draft.value.trim()} ${String(data.text)}` : String(data.text);
        }
        break;
      case "dictation_finished":
        dictations.value = dictations.value.filter((job) => job !== String(data.job_id));
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

  // A recording (source "mic") or an audio file (source "file") goes to the server, which transcribes it.
  async function dictate(file: File, source: "mic" | "file"): Promise<void> {
    if (!sessionId.value) return;
    // The job's id is known here before the upload: its question or text may arrive before the answer.
    const jobId = crypto.randomUUID().replaceAll("-", "");
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("source", source);
    form.append("job_id", jobId);
    dictations.value = [...dictations.value, jobId];
    try {
      await api<DictationAccepted>(`sessions/${sessionId.value}/dictation`, { method: "POST", body: form });
    } catch (error) {
      dictations.value = dictations.value.filter((job) => job !== jobId);
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  }

  // What the browser's own recognition heard: the server sends it to the AI or gives it back for the
  // input field, by the same rule as a transcription (dictation.hand_on).
  async function dictated(text: string): Promise<void> {
    if (!sessionId.value) return;
    const result = await api<DictatedTextResult>(`sessions/${sessionId.value}/dictation/text`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    if (!result.sent) draft.value = draft.value.trim() ? `${draft.value.trim()} ${text}` : text;
  }

  async function loadDictationSettings(): Promise<void> {
    dictationSettings.value = await api<DictationSettings>("dictation/settings");
  }

  async function loadSpeechSettings(): Promise<void> {
    speechSettings.value = await api<SpeechSettings>("speech/settings");
  }

  // A finished reply read aloud again; the reading arrives over the event stream like a new one.
  async function readAgain(timestamp: string): Promise<void> {
    if (!sessionId.value) return;
    try {
      await api<void>(`sessions/${sessionId.value}/messages/speech`, { method: "POST", body: JSON.stringify({ timestamp }) });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  }

  // Every reply read into new audio, without playing it; each bubble comes back as message_updated.
  async function regenerateAll(): Promise<void> {
    if (!sessionId.value) return;
    try {
      await api<void>(`sessions/${sessionId.value}/speech/regenerate`, { method: "POST" });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  }

  async function changeDictationSettings(changes: Partial<DictationSettings>): Promise<void> {
    dictationSettings.value = await api<DictationSettings>("dictation/settings", {
      method: "PATCH",
      body: JSON.stringify(changes),
    });
  }

  async function answerDictation(jobId: string, question: string): Promise<void> {
    if (!sessionId.value) return;
    await api<void>(`sessions/${sessionId.value}/dictation/${jobId}/answer`, {
      method: "POST",
      body: JSON.stringify({ yes: window.confirm(question) }),
    });
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
    pendingImages, uploading, draft, dictations, dictationSettings, open, close, send, stop, configure,
    uploadImages, removeImage, dictate, dictated, loadDictationSettings, changeDictationSettings,
    speechSettings, loadSpeechSettings, readAgain, regenerating, regenerateAll,
  };
});
