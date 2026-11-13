// The open session: its history, the turn that runs, its debug lines. The server is the truth;
// this store only follows its event stream (src/api/events.ts) and sends commands.
import { defineStore } from "pinia";
import { ref } from "vue";
import { ApiError, api } from "../api/client";
import { RESOURCES, followSession } from "../api/events";
import type {
  ChatEntry,
  ChatExported,
  CropBox,
  DictatedTextResult,
  DictationAccepted,
  MessageAccepted,
  ServiceSnapshot,
  SessionConfig,
  SessionConfigUpdate,
  SessionDetail,
  SessionRemembered,
  UploadedImage,
} from "../api/types";
import { t } from "../i18n";
import * as media from "../lib/media";
import { resourceChanged, resourceOfKind } from "../lib/resourceChanges";
import { withConfigChanges } from "../lib/sessionConfig";
import * as speech from "../lib/speech";
import type { TurnPhase } from "../lib/turnProgress";
import { type HubNotice, useHubNotice } from "./hubNotice";
import { useServiceStatus } from "./serviceStatus";
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
  const config = ref<SessionConfig>({
    active_agent: "aifred", multi_agent_mode: "standard", symposion_agents: [], incognito: false, debate_seats: {},
  });
  const running = ref(false);
  const streaming = ref<Streaming | null>(null);
  const toolStatus = ref("");
  // What the turn does while it has no text (turn_phase); null: no phase reported.
  const phase = ref<TurnPhase | null>(null);
  const notice = ref("");
  // What an action of the chat's toolbar reports (not a warning): "Merken".
  const info = ref("");
  const remembering = ref(false);
  const queued = ref<string[]>([]);
  // Images uploaded for the next message (they sit in the session's upload folder until it is sent).
  const pendingImages = ref<UploadedImage[]>([]);
  const uploading = ref(false);
  // The text in the input field; a dictation in edit mode lands here.
  const draft = ref("");
  // Dictations this tab sent that are not finished yet: only this tab asks their questions and
  // takes their text into its input field.
  const dictations = ref<string[]>([]);
  // Every reply of the session is being read into new audio (its button is off meanwhile).
  const regenerating = ref(false);
  let source: EventSource | null = null;
  // The session whose audio player plays in this page.
  let mediaSession: string | null = null;
  media.onQueueEnd(() => void mediaControl("ended"));

  async function open(id: string): Promise<void> {
    speech.stop(); // the other session's reading goes on for its other tabs, not here
    await load(id);
    notice.value = "";
    info.value = "";
    queued.value = [];
    pendingImages.value = [];
    draft.value = "";
    dictations.value = [];
  }

  // The session as the server has it, and its event stream from there on. Also when the stream says it may have
  // missed events (``resync``: a new turn emptied the replay, or the server started anew): what the user is
  // typing, attaching or dictating stays.
  async function load(id: string): Promise<void> {
    source?.close();
    const detail = await api<SessionDetail>(`sessions/${id}`);
    sessionId.value = id;
    title.value = detail.title ?? null;
    history.value = detail.chat_history;
    const lastSpoken = [...detail.chat_history].reverse().find((entry) => entry.metadata?.audio_urls?.length || entry.metadata?.browser_speech?.length);
    speech.keep(lastSpoken?.metadata?.audio_urls ?? [], lastSpoken?.metadata?.browser_speech ?? []);
    debugLines.value = detail.debug_messages;
    config.value = detail.config;
    running.value = detail.running;
    regenerating.value = detail.speech_regenerating;
    streaming.value = null;
    toolStatus.value = "";
    phase.value = null;
    source = followSession(id, detail.last_event_id, handle);
  }

  // The session the user works in: the newest, or a new one when there is none. Opened once per login, on whatever page the
  // user lands: its event stream also carries the changes of the lists the other pages show (lib/resourceChanges.ts).
  async function openLatest(): Promise<void> {
    const sessions = useSessions();
    await sessions.refresh();
    const latest = sessions.list[0];
    await open(latest ? latest.session_id : await sessions.create());
  }

  function close(): void {
    source?.close();
    speech.stop();
    source = null;
    sessionId.value = null;
  }

  function handle(kind: string, data: Record<string, unknown>): void {
    const resource = resourceOfKind(kind);
    if (resource) return resourceChanged(resource);   // a list the server changed: its store loads it anew
    switch (kind) {
      case "turn_started":
        running.value = true;
        phase.value = null;
        notice.value = "";
        info.value = "";
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
      case "turn_phase":
        phase.value = data.phase ? (data as unknown as TurnPhase) : null;
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
        // The player offers the reading that was spoken last (again, rewound), also when it has ended.
        if (entry.metadata?.audio_urls?.length || entry.metadata?.browser_speech?.length) {
          speech.keep(entry.metadata.audio_urls ?? [], entry.metadata.browser_speech ?? []);
        }
        break;
      }
      case "resync":
        // The server started anew: the status line and every list it pushes only on change may have changed while
        // this page was not listening, and nothing pushes them to a page that was open.
        void useServiceStatus().fetch(false);
        RESOURCES.forEach(resourceChanged);
        if (sessionId.value) void load(sessionId.value);
        break;
      case "history_replaced":
        history.value = data.chat_history as ChatEntry[];
        break;
      case "chat_cleared":
        history.value = [];
        debugLines.value = [];
        queued.value = [];
        pendingImages.value = [];
        streaming.value = null;
        toolStatus.value = "";
        phase.value = null;
        notice.value = "";
        info.value = "";
        title.value = "";
        void useSessions().refresh();
        break;
      case "session_config":
        // While this tab's own changes are on their way, the last of them brings the state (configure).
        if (!changesOnTheirWay) config.value = data as unknown as SessionConfig;
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
        media.stop();
        break;
      case "media":
        mediaSession = sessionId.value;   // the buttons steer the session that started it, also from another one
        media.handle(kind, data);
        break;
      case "pause":
      case "resume":
      case "seek":
      case "speed":
        media.handle(kind, data);
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
        phase.value = null;
        void useSessions().refresh();
        break;
      case "debug":
        debugLines.value = [...debugLines.value, String(data.line)];
        break;
      case "hub_notification":
        // A channel's message: the hint, and the session list (a channel may have made a session).
        useHubNotice().receive(data as unknown as HubNotice);
        void useSessions().refresh();
        break;
      case "service_status":
        useServiceStatus().receive(data as unknown as ServiceSnapshot);
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

  // „Merken“: the chat summarized into the memory of the agents that spoke in it.
  async function remember(): Promise<void> {
    if (!sessionId.value) return;
    remembering.value = true;
    info.value = "";
    try {
      const done = await api<SessionRemembered>(`sessions/${sessionId.value}/remember`, { method: "POST" });
      info.value = t("save_memory_done", { agents: done.agents.join(", "), stored: done.stored, updated: done.updated });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    } finally {
      remembering.value = false;
    }
  }

  // The player's buttons: through the server, so every tab of the session and the agents' tools see the same.
  async function mediaControl(action: "pause" | "resume" | "stop" | "seek" | "ended", positionSec = 0): Promise<void> {
    if (!mediaSession) return;
    try {
      await api(`sessions/${mediaSession}/media`, { method: "POST", body: JSON.stringify({ action, position_sec: positionSec }) });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
    if (action === "stop" || action === "ended") mediaSession = null;
  }

  // The chat emptied on the server; every tab of the session, this one too, follows chat_cleared.
  async function clearChat(): Promise<void> {
    if (!sessionId.value) return;
    try {
      await api<void>(`sessions/${sessionId.value}/clear`, { method: "POST" });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
    }
  }

  // The chat as one standalone HTML file (lib/chat_export.py): where it lies and a plain file name for saving it.
  async function exportChat(): Promise<ChatExported | null> {
    if (!sessionId.value) return null;
    try {
      return await api<ChatExported>(`sessions/${sessionId.value}/export`, { method: "POST" });
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return null;
    }
  }

  async function answerDictation(jobId: string, question: string): Promise<void> {
    if (!sessionId.value) return;
    await api<void>(`sessions/${sessionId.value}/dictation/${jobId}/answer`, {
      method: "POST",
      body: JSON.stringify({ yes: window.confirm(question) }),
    });
  }

  // An image of the next message turned or cut; the server keeps it as another file, the entry follows.
  async function editImage(index: number, action: "rotate" | "crop", body: object): Promise<boolean> {
    const image = pendingImages.value[index];
    if (!sessionId.value || !image) return false;
    try {
      const edited = await api<UploadedImage>(`sessions/${sessionId.value}/images/${encodeURIComponent(image.name)}/${action}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      pendingImages.value = pendingImages.value.map((old, at) => (at === index ? edited : old));
      return true;
    } catch (error) {
      notice.value = error instanceof ApiError ? error.detail : String(error);
      return false;
    }
  }

  function rotateImage(index: number, clockwise: boolean): Promise<boolean> {
    return editImage(index, "rotate", { clockwise });
  }

  function cropImage(index: number, box: CropBox): Promise<boolean> {
    return editImage(index, "crop", box);
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

  // A choice of this session (agent, mode, seats, incognito); a refused one shows why. It shows at once and the
  // changes go out one after another: the next click builds on what this one shows, not on the server's last answer
  // (quick clicks on the symposion's pills overwrote each other, and answers could arrive in any order). The server's
  // own state (its answer, its session_config event) is taken only once no change is on its way.
  let configuring: Promise<void> = Promise.resolve();
  let changesOnTheirWay = 0;

  function configure(changes: SessionConfigUpdate): Promise<void> {
    const id = sessionId.value;
    if (!id) return configuring;
    config.value = withConfigChanges(config.value, changes);
    changesOnTheirWay += 1;
    configuring = configuring.then(async () => {
      let confirmed: SessionConfig | null = null;
      try {
        confirmed = await api<SessionConfig>(`sessions/${id}/config`, { method: "PATCH", body: JSON.stringify(changes) });
      } catch (error) {
        notice.value = error instanceof ApiError ? error.detail : String(error);
      }
      changesOnTheirWay -= 1;
      if (changesOnTheirWay || sessionId.value !== id) return;
      // After a refused change what shows is not what the server has: its state again.
      config.value = confirmed ?? (await api<SessionDetail>(`sessions/${id}`)).config;
    });
    return configuring;
  }

  return {
    sessionId, title, history, debugLines, config, running, streaming, toolStatus, phase, notice, info, queued,
    pendingImages, uploading, draft, dictations, open, openLatest, close, send, stop, configure,
    uploadImages, removeImage, dictate, dictated,
    readAgain, regenerating, regenerateAll, rotateImage, cropImage, exportChat, clearChat,
    remembering, remember, mediaControl,
  };
});
