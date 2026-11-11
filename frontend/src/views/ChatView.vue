<script setup lang="ts">
import { Download, FolderOpen, Pin, Share2, Trash2 } from "lucide-vue-next";
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { AgentSummary, ChatEntry, CropBox, DebateMode } from "../api/types";
import AgentChoicesModal from "../components/AgentChoicesModal.vue";
import AgentPills from "../components/AgentPills.vue";
import ChatMenu from "../components/ChatMenu.vue";
import DebateControls from "../components/DebateControls.vue";
import VisionBadge from "../components/VisionBadge.vue";
import ChatInput from "../components/ChatInput.vue";
import DebugConsole from "../components/DebugConsole.vue";
import ImageEditor from "../components/ImageEditor.vue";
import MediaPlayer from "../components/MediaPlayer.vue";
import TtsIcon from "../components/TtsIcon.vue";
import MessageBubble from "../components/MessageBubble.vue";
import SessionList from "../components/SessionList.vue";
import TurnProgress from "../components/TurnProgress.vue";
import { t } from "../i18n";
import { ZOOM, chatZoom, zoomBy } from "../lib/devicePrefs";
import { pinQuestion } from "../lib/devicePrefs";
import { answerStartTop, isAtEnd } from "../lib/followScroll";
import { useSessionsColumn } from "../lib/sessionsColumn";
import HelpButton from "../components/help/HelpButton.vue";
import { saveAs } from "../lib/download";
import { renderMarkdown } from "../lib/markdown";
import { successorSession } from "../lib/sessionSuccessor";
import { progressLine } from "../lib/turnProgress";
import { useChat } from "../stores/chat";
import { useSessions } from "../stores/sessions";
import { useSettings } from "../stores/settings";

const sessions = useSessions();
const chat = useChat();
const settings = useSettings();
const sessionsColumn = useSessionsColumn().shown;
const agents = ref<AgentSummary[]>([]);
// Every agent's colour, switched off ones too: the history shows their answers (the stripe of a bubble).
const colors = ref<Record<string, string>>({});
const modes = ref<DebateMode[]>([]);
const messages = ref<HTMLElement | null>(null);
const chatMain = ref<HTMLElement | null>(null);
const streamingHtml = computed(() => (chat.streaming ? renderMarkdown(chat.streaming.text) : ""));
const hasReplies = computed(() => chat.history.some((entry) => entry.role === "assistant"));
// The history in turns: a question with the answers after it. The question stays at the top edge of the box while its
// answers scroll beneath it, until the next turn's question pushes it out (sticky within its turn).
const turns = computed(() => {
  const grouped: { key: string; entries: ChatEntry[] }[] = [];
  let current: { key: string; entries: ChatEntry[] } | null = null;
  for (const entry of chat.history) {
    if (entry.role === "user" || current === null) {
      current = { key: entry.timestamp + entry.role, entries: [] };
      grouped.push(current);
    }
    current.entries.push(entry);
  }
  return grouped;
});
// The turn whose question sticks at the top edge of the box now: its turn begins above the box's top edge and still
// reaches into it. Only that question is folded to a few lines; where it stands in the history it shows in full.
const stuckTurn = ref<string | null>(null);
function updateStuck(): void {
  const view = messages.value;
  if (!view) return;
  const top = view.getBoundingClientRect().top;
  let key: string | null = null;
  for (const turn of view.querySelectorAll<HTMLElement>(".turn")) {
    const bounds = turn.getBoundingClientRect();
    if (bounds.top < top - 1 && bounds.bottom > top) key = turn.dataset.key ?? null;
  }
  stuckTurn.value = key;
}
watch([turns, pinQuestion], () => void nextTick(updateStuck), { flush: "post" });
// Shown in the streaming bubble, or on its own while the turn has none yet.
const progress = computed(() =>
  progressLine(chat.running, chat.phase, !chat.streaming || (!chat.streaming.text && !chat.toolStatus)),
);

// The agents' quick choices (models and switches) behind the 🎛️ button.
const choicesOpen = ref(false);

// What the sandbox made in this chat (plots, pages, screenshots) in the file requester; loaded when it opens.
const FileRequester = defineAsyncComponent(() => import("../components/FileRequester.vue"));
const outputsOpen = ref(false);

// The image of the next message in the crop dialog (its place in the list: turning gives it a new name).
const editing = ref<number | null>(null);
const editingImage = computed(() => (editing.value === null ? undefined : chat.pendingImages[editing.value]));
const editBusy = ref(false);
// Another session (or the image sent) leaves no image at that place: the dialog is over, not waiting.
watch(editingImage, (image) => {
  if (!image) editing.value = null;
});

async function rotateImage(clockwise: boolean): Promise<void> {
  if (editing.value === null) return;
  editBusy.value = true;
  await chat.rotateImage(editing.value, clockwise);
  editBusy.value = false;
}

async function cropImage(box: CropBox): Promise<void> {
  if (editing.value === null) return;
  editBusy.value = true;
  if (await chat.cropImage(editing.value, box)) editing.value = null;
  editBusy.value = false;
}

// The chat as one standalone HTML file: opened in a tab (to read, or to print as PDF) or saved. The tab opens on the
// click itself, before the server has built the file: opened later, a browser takes it for a pop-up.
const exporting = ref(false);

async function shareChat(): Promise<void> {
  const tab = window.open("", "_blank");
  exporting.value = true;
  const done = await chat.exportChat();
  exporting.value = false;
  if (done && tab) tab.location.href = done.url;
  else tab?.close();
}

async function downloadChat(): Promise<void> {
  exporting.value = true;
  const done = await chat.exportChat();
  if (done) await saveAs(done.url, done.download_name);
  exporting.value = false;
}

async function start(): Promise<void> {
  agents.value = await api<AgentSummary[]>("agents/summary");
  colors.value = await api<Record<string, string>>("agents/colors");
  modes.value = await api<DebateMode[]>("debate/modes");
}

async function create(): Promise<void> {
  await chat.open(await sessions.create());
}

// The open session among the deleted: the nearest remaining one below it opens, else above, else a new one.
async function remove(sessionIds: string[]): Promise<void> {
  const order = sessions.list.map((session) => session.session_id);
  await sessions.remove(sessionIds);
  if (chat.sessionId && sessionIds.includes(chat.sessionId)) {
    const next = successorSession(order, chat.sessionId, new Set(sessionIds));
    await chat.open(next ?? (await sessions.create()));
  }
}

// While an answer streams the box follows its end, as long as the reader stands there (followScroll.ts); when it is
// finished the box jumps once to its start, below its question, to be read from the beginning. Where the page itself
// put the box still counts as following (the next agent of a debate streams in view too); scrolled by hand, it stays.
// Measured before the change reaches the page (the watcher runs first), scrolled after it.
let placedAt: number | null = null;
watch(
  () => [chat.history.length, chat.streaming?.text.length, progress.value?.text] as const,
  async ([length], [lengthBefore]) => {
    const view = messages.value;
    if (!view) return;
    const following = isAtEnd(view) || (placedAt !== null && Math.abs(view.scrollTop - placedAt) <= 1);
    await nextTick();
    if (!following) return;
    const answer = length > lengthBefore ? finishedAnswer(view) : null;
    // The turn's end (no stream, no progress line) leaves the box at the answer's start it jumped to.
    if (!answer && !chat.streaming && !progress.value) return;
    view.scrollTop = answer ? answerStartTop(view, ...answer) : view.scrollHeight;
    placedAt = view.scrollTop;
  },
);

// The answer just added to the last turn (not its question) and the question that sticks above it.
function finishedAnswer(view: HTMLElement): [HTMLElement, HTMLElement | null] | null {
  const turn = view.querySelector<HTMLElement>(".turn:last-of-type");
  const last = turn?.lastElementChild;
  if (!(last instanceof HTMLElement) || last.classList.contains("question")) return null;
  // A question that sticks covers the answer's start: the answer stops below it (none sticks: right at the top).
  return [last, turn?.querySelector<HTMLElement>(".pinned") ?? null];
}
// A session opened shows its end.
watch(() => chat.sessionId, async () => {
  await nextTick();
  placedAt = null;
  if (messages.value) messages.value.scrollTop = messages.value.scrollHeight;
});

// A chat chosen in the list: it opens, the list has closed, and the page goes to the chat so that it shows.
async function openSession(sessionId: string): Promise<void> {
  await chat.open(sessionId);
  await nextTick();
  chatMain.value?.scrollIntoView({ block: "start", behavior: "smooth" });
}

// The shell shows this page only once logged in and has opened the session (App.vue).
onMounted(() => void start());


</script>

<template>
  <div class="layout" :class="{ column: sessionsColumn }">
    <SessionList class="side" :current="chat.sessionId" @open="openSession" @create="create" @remove="remove" />
    <main ref="chatMain" class="chat">
      <!-- The history's title with the text size and the ⋮ of what concerns the whole chat above it; the input as in
           Agent-Orc, who answers and how below it (Peuqui 09.11.2026). -->
      <div class="toolbar">
        <h2>{{ chat.title || t("untitled_chat") }}</h2>
        <button class="zoom" :disabled="chatZoom <= ZOOM.min" :title="t('chat_font_smaller')" @click="zoomBy('chat', -1)">A−</button>
        <button class="zoom" :disabled="chatZoom >= ZOOM.max" :title="t('chat_font_larger')" @click="zoomBy('chat', 1)">A+</button>
        <ChatMenu>
          <button v-if="settings.current?.enable_tts" :disabled="!hasReplies || chat.regenerating" @click="chat.regenerateAll">
            <TtsIcon />{{ t("tts_regenerate_all") }}
          </button>
          <button :disabled="!hasReplies || chat.remembering || chat.running" :title="t('save_memory_hint')" @click="chat.remember">
            <Pin :size="16" />{{ t("save_memory") }}
          </button>
          <button :disabled="!chat.history.length || exporting" @click="shareChat"><Share2 :size="16" />{{ t("share_chat") }}</button>
          <button :disabled="!chat.history.length || exporting" @click="downloadChat"><Download :size="16" />{{ t("download_chat") }}</button>
          <button :disabled="!chat.sessionId" :title="t('sandbox_outputs_tooltip')" @click="outputsOpen = true">
            <FolderOpen :size="16" />{{ t("sandbox_outputs") }}
          </button>
          <button class="danger" :disabled="!chat.history.length" @click="chat.clearChat"><Trash2 :size="16" />{{ t("clear_chat") }}</button>
        </ChatMenu>
      </div>
      <div ref="messages" class="messages" @scroll.passive="updateStuck">
        <div class="bubbles">
        <section v-for="turn in turns" :key="turn.key" :data-key="turn.key" class="turn">
          <MessageBubble
            v-for="entry in turn.entries"
            :key="entry.timestamp + entry.role"
            :class="{ question: entry.role === 'user', pinned: entry.role === 'user' && pinQuestion }"
            :entry="entry"
            :pinned="entry.role === 'user' && pinQuestion"
            :stuck="turn.key === stuckTurn"
            :speech-on="!!settings.current?.enable_tts && entry.role === 'assistant'"
            :color="entry.agent ? colors[entry.agent] : undefined"
            @read-again="chat.readAgain"
          />
        </section>
        <article v-if="chat.streaming" class="streaming" :style="colors[chat.streaming.agent] ? { '--agent-color': colors[chat.streaming.agent] } : undefined">
          <header>{{ chat.streaming.emoji }} {{ chat.streaming.displayName }}</header>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown (DOMPurify) -->
          <div v-html="streamingHtml" />
          <p v-if="chat.toolStatus" class="tool">{{ chat.toolStatus }}</p>
          <TurnProgress v-if="progress" :line="progress" />
        </article>
        <TurnProgress v-else-if="progress" class="waiting" :line="progress" />
        <p v-if="chat.notice" class="notice">⚠️ {{ chat.notice }}</p>
        <p v-if="chat.info" class="info">{{ chat.info }}</p>
        <p v-if="settings.notice" class="notice">⚠️ {{ settings.notice }}</p>
        </div>
      </div>
      <MediaPlayer />
      <ChatInput
        v-if="settings.current"
        v-model="chat.draft"
        :running="chat.running"
        :queued="chat.queued.length"
        :images="chat.pendingImages"
        :uploading="chat.uploading"
        :transcribing="chat.dictations.length > 0"
        :settings="settings.current"
        @send="chat.send"
        @stop="chat.stop"
        @upload="chat.uploadImages"
        @remove-image="chat.removeImage"
        @edit-image="(index) => (editing = index)"
        @dictate="chat.dictate"
        @heard="chat.dictated"
      />
      <!-- Who answers: the agents in a row of their own (room for more), below it the mode with its seats, memory, the
           quick choices and the help on the left, Vigilantia on the right (Peuqui 09.11.2026). -->
      <div class="who">
        <AgentPills :config="chat.config" :agents="agents" @configure="chat.configure" />
      </div>
      <div class="how">
        <DebateControls :config="chat.config" :agents="agents" :modes="modes" @configure="chat.configure" />
        <button
          class="incognito"
          :class="{ on: chat.config.incognito }"
          :title="chat.config.incognito ? t('incognito_mode') : t('memory_active')"
          @click="chat.configure({ incognito: !chat.config.incognito })"
        >
          {{ chat.config.incognito ? "🔒" : "🔓" }}
        </button>
        <button class="agent-choices" :title="t('agent_choices_title')" @click="choicesOpen = true">🎛️</button>
        <HelpButton topic="chat" />
        <VisionBadge class="watch" />
      </div>
      <div class="divider" role="separator" />
      <DebugConsole :lines="chat.debugLines" />
      <AgentChoicesModal v-if="choicesOpen" @close="choicesOpen = false" />
      <FileRequester v-if="outputsOpen && chat.sessionId" :area="`sandbox-${chat.sessionId}`" :title="t('sandbox_outputs')" @close="outputsOpen = false" />
      <ImageEditor
        v-if="editingImage"
        :image="editingImage"
        :busy="editBusy"
        @rotate="rotateImage"
        @crop="cropImage"
        @close="editing = null"
      />
    </main>
  </div>
</template>

<style scoped>
/* One column, as under Reflex: saved chats (an accordion), the chat history as a box of its own (it follows the
   answer), input and console below it; the page scrolls. */
.layout { display: flex; flex-direction: column; gap: 10px; padding-top: 12px; }
/* The saved chats as a column at the left (lib/sessionsColumn.ts): it stays in view and scrolls by itself. */
.layout.column { flex-direction: row; align-items: flex-start; gap: 14px; }
.layout.column .side {
  flex: none; width: var(--sessions-column-width); position: sticky; top: 12px; max-height: calc(100 * var(--dvh) - 24px);
}
.layout.column .chat { flex: 1; }
.chat { display: flex; flex-direction: column; gap: 10px; min-width: 0; padding-bottom: 12px; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar h2 { margin-right: auto; }
/* The container of the agent pills' query: on a narrow row they show their emoji only. */
.who { min-width: 0; container-type: inline-size; }
.how { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.how .watch { margin-left: auto; }
h2 { margin: 0; font-size: 1.1rem; }
.incognito, .agent-choices, .zoom { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 8px; cursor: pointer; font: inherit; color: inherit; }
.zoom:disabled { opacity: 0.5; cursor: default; }
.incognito.on { border-color: var(--aifred-accent-amber); }
/* The box keeps 70 % of the screen whatever the sizes; only its content takes the chat's size (the interface's
   size, set on the page, is taken out again). */
.messages {
  height: calc(var(--chat-height-desktop) / var(--ui-zoom));
  overflow-y: auto;
  overflow-wrap: anywhere;
  /* No padding on top: the pinned question sticks where it sits (top: 0), so it does not move a pixel before it stops. */
  padding: 0 10px 10px;
  background: var(--aifred-chat-panel-bg);
  border: 1px solid var(--aifred-border);
  border-radius: 8px;
}
@media (max-width: 760px) {
  .messages { height: calc(var(--chat-height-mobile) / var(--ui-zoom)); }
}
.bubbles { color: var(--aifred-text-primary); display: flex; flex-direction: column; gap: 10px; zoom: calc(var(--chat-zoom) / var(--ui-zoom)); }
.turn { display: flex; flex-direction: column; gap: 10px; }
/* Above the collapsibles' headers, which stick too (formatting.py writes them with z-index 2 into the answer). */
.turn > .pinned { position: sticky; top: 0; z-index: 3; box-shadow: 0 4px 10px var(--aifred-page-bg); }
/* As the finished bubble (MessageBubble), its rim dashed while it streams. */
.streaming {
  box-sizing: border-box; width: 100%; padding: 10px 14px; border-radius: 10px; border: 1px dashed var(--aifred-border-light);
  border-left: 1px solid var(--agent-color, var(--aifred-border-light));
  color: var(--aifred-answer-text);
}
.streaming header { font-size: var(--text-small); font-weight: 600; color: var(--agent-color, var(--aifred-primary)); }
.tool { margin: 4px 0 0; font-size: var(--text-small); color: var(--aifred-accent-cyan); }
.waiting { align-self: flex-start; padding: 0 14px; }
.notice { color: var(--aifred-accent-amber); }
.info { color: var(--aifred-accent-success); }
</style>
