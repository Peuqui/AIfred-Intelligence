<script setup lang="ts">
import { Download, FolderOpen, MessageSquareOff, MessageSquarePlus, Pin, Share2, SlidersHorizontal, Trash2, TriangleAlert } from "lucide-vue-next";
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { AgentSummary, ChatEntry, CropBox, DebateMode } from "../api/types";
import AgentChoicesModal from "../components/AgentChoicesModal.vue";
import AgentPills from "../components/AgentPills.vue";
import ChatMenu from "../components/ChatMenu.vue";
import DebateControls from "../components/DebateControls.vue";
import VisionBadge from "../components/VisionBadge.vue";
import ChatInput from "../components/ChatInput.vue";
import GpuHoldBanner from "../components/GpuHoldBanner.vue";
import DebugConsole from "../components/DebugConsole.vue";
import ResizeGrip from "../components/ResizeGrip.vue";
import ImageEditor from "../components/ImageEditor.vue";
import MediaPlayer from "../components/MediaPlayer.vue";
import MemorySwitchIcon from "../components/MemorySwitchIcon.vue";
import TtsIcon from "../components/TtsIcon.vue";
import MessageBubble from "../components/MessageBubble.vue";
import ReadMark from "../components/ReadMark.vue";
import SessionList from "../components/SessionList.vue";
import StreamingAnswer from "../components/StreamingAnswer.vue";
import { t, tCount } from "../i18n";
import { ZOOM, chatHeight, chatZoom, zoomBy } from "../lib/devicePrefs";
import { pinQuestion } from "../lib/devicePrefs";
import { useChatScroll } from "../lib/useChatScroll";
import { isPlainClick, isUnread, lineAfterIndex } from "../lib/unread";
import { useSessionsColumn } from "../lib/sessionsColumn";
import HelpButton from "../components/help/HelpButton.vue";
import { saveAs } from "../lib/download";
import { type StreamSection, renderStream } from "../lib/streamBlocks";
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
const MESSAGES_MIN_HEIGHT = 120; // px: a box the grip cannot shrink to nothing
const agentRow = ref<HTMLElement | null>(null);
// The answer being written, section by section: with every piece only its last section is rendered again, the finished
// ones keep their HTML and their DOM (a long text rendered whole with every piece stopped the page on an old tablet).
const streamingSections = computed<StreamSection[]>((previous) => (chat.streaming ? renderStream(chat.streaming.text, previous ?? []) : []));
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
// Where the "read up to here" line stands: under the entry with this key; "" above the first one; null: no line.
const entryKey = (entry: ChatEntry): string => entry.timestamp + entry.role;
const readLineAfter = computed<string | null>(() => {
  const at = lineAfterIndex(chat.history, chat.readUntil);
  if (at === null) return null;
  const entry = chat.history[at];
  return entry ? entryKey(entry) : "";
});
// A click on a bubble below the line says "read up to here" (not on what has a click of its own, not while text is selected).
function bubbleClicked(entry: ChatEntry, event: MouseEvent): void {
  if (isPlainClick(event)) void chat.markRead(entry.timestamp);
}
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

// The box's scrolling (lib/followScroll.ts has the rules, lib/useChatScroll.ts the movement): a chat that opens goes to
// the "read up to here" line, else to where the reader was, else to the end; what comes in moves the box only while the
// reader follows; sending something takes it to the end.
const unreadKeys = computed(() => chat.history.filter((entry) => isUnread(entry, chat.readUntil)).map(entryKey));
const { unreadBelow, place, placeIncoming, expectOwn, onScroll, takenByHand, onKey, onPointerDown, showNewBelow } = useChatScroll(
  messages,
  () => chat.sessionId,
  () => unreadKeys.value,
);
watch(
  () => [chat.sessionId, chat.history.length, chat.streaming?.text.length, progress.value?.text] as const,
  ([sessionId, length], [sessionBefore, lengthBefore]) => {
    if (sessionId !== sessionBefore) return void place("opened");
    void placeIncoming(chat.history.slice(lengthBefore, length).some((entry) => entry.role === "user"));
  },
);
// The mark moved (a click, "all read", a reading heard to its end, another tab): what is unread below the view changes
// with it. Within the same chat it lets go of what the box held: marking must not move the view (the line that goes
// away would otherwise send a box held at the line to the end).
watch(
  () => [chat.sessionId, chat.readUntil] as const,
  ([sessionId], [sessionBefore]) => {
    if (sessionId === sessionBefore) takenByHand();
    onScroll();
  },
  { flush: "post" },
);

function scrolled(): void {
  updateStuck();
  onScroll();
}

function sendOwn(text: string): Promise<void> {
  expectOwn();
  return chat.send(text);
}

// A chat chosen in the list: it opens, the list has closed, and the page goes to the chat so that it shows: the agents'
// row, the last of what belongs to the chat, at the bottom edge, the title above the box at the top (the box is sized for it).
async function openSession(sessionId: string): Promise<void> {
  await chat.open(sessionId);
  await nextTick();
  agentRow.value?.scrollIntoView({ block: "end", behavior: "smooth" });
}

// The shell shows this page only once logged in and has opened the session (App.vue).
onMounted(() => void start());


</script>

<template>
  <div class="layout" :class="{ column: sessionsColumn }">
    <SessionList class="side" :current="chat.sessionId" @open="openSession" @create="create" @remove="remove" />
    <main class="chat">
      <!-- The history's title with the text size and the ⋮ of what concerns the whole chat above it; the input as in
           Agent-Orc, who answers and how below it (Peuqui 09.11.2026). -->
      <div class="toolbar">
        <h2>{{ chat.title || t("untitled_chat") }}</h2>
        <div class="tools">
          <button class="all-read" :disabled="!chat.unread" @click="chat.markAllRead">{{ tCount("mark_all_read", chat.unread) }}</button>
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
      </div>
      <GpuHoldBanner />
      <div class="messages-box">
        <div
          ref="messages"
          class="messages"
          :style="chatHeight ? { height: `min(${chatHeight}px, calc(100 * var(--dvh)))` } : undefined"
          @scroll.passive="scrolled"
          @wheel.passive="takenByHand"
          @touchmove.passive="takenByHand"
          @keydown="onKey"
          @pointerdown="onPointerDown"
        >
          <div class="bubbles">
          <section v-for="(turn, at) in turns" :key="turn.key" :data-key="turn.key" class="turn">
            <ReadMark v-if="at === 0 && readLineAfter === ''" />
            <template v-for="entry in turn.entries" :key="entryKey(entry)">
              <MessageBubble
                :class="{ question: entry.role === 'user', pinned: entry.role === 'user' && pinQuestion }"
                :entry="entry"
                :data-entry="entryKey(entry)"
                :data-pinned="entry.role === 'user' && pinQuestion ? '' : undefined"
                :pinned="entry.role === 'user' && pinQuestion"
                :stuck="turn.key === stuckTurn"
                :speech-on="!!settings.current?.enable_tts && entry.role === 'assistant'"
                :color="entry.agent ? colors[entry.agent] : undefined"
                :reading-requested="chat.readingRequested === entry.timestamp"
                @read-again="chat.readAgain"
                @click="bubbleClicked(entry, $event)"
              />
              <ReadMark v-if="readLineAfter === entryKey(entry)" />
            </template>
            <!-- The answer being written belongs to the last turn, where it lands once done: inside the turn its question
                 sticks above it at once, also when "keep the question at the top" is switched on during the answer. -->
            <StreamingAnswer v-if="at === turns.length - 1" :sections="streamingSections" :progress="progress" :color="chat.streaming ? colors[chat.streaming.agent] : undefined" />
          </section>
          <!-- No turn yet (an empty chat): the answer being written stands on its own. -->
          <StreamingAnswer v-if="!turns.length" :sections="streamingSections" :progress="progress" :color="chat.streaming ? colors[chat.streaming.agent] : undefined" />
          <p v-if="chat.notice" class="notice"><TriangleAlert :size="16" /> {{ chat.notice }}</p>
          <p v-if="chat.info" class="info"><Pin :size="16" /> {{ chat.info }}</p>
          <p v-if="settings.notice" class="notice"><TriangleAlert :size="16" /> {{ settings.notice }}</p>
          </div>
        </div>
        <!-- Shown while unread bubbles lie below the view; it marks nothing as read. -->
        <button v-if="unreadBelow > 0" class="new-below" @click="showNewBelow">{{ tCount("unread_below", unreadBelow) }}</button>
      </div>
      <ResizeGrip :target="messages" :min-height="MESSAGES_MIN_HEIGHT" :max-screens="1" @resize="(height) => (chatHeight = height)" @reset="chatHeight = null" />
      <MediaPlayer />
      <ChatInput
        v-if="settings.current"
        v-model="chat.draft"
        :running="chat.running"
        :queued="chat.queued.length"
        :images="chat.pendingImages"
        :uploading="chat.uploading"
        :transcribing="chat.dictations.length > 0"
        :dictations-landed="chat.dictationsLanded"
        :settings="settings.current"
        @send="sendOwn"
        @stop="chat.stop"
        @upload="chat.uploadImages"
        @remove-image="chat.removeImage"
        @edit-image="(index) => (editing = index)"
        @dictate="chat.dictate"
        @heard="chat.dictated"
      />
      <!-- Who answers: the agents in a row of their own (room for more), below it the mode with its seats, memory, the
           quick choices and the help on the left, Vigilantia on the right (Peuqui 09.11.2026). -->
      <div ref="agentRow" class="who">
        <AgentPills :config="chat.config" :agents="agents" @configure="chat.configure" />
      </div>
      <div class="how">
        <DebateControls :config="chat.config" :agents="agents" :modes="modes" @configure="chat.configure" />
        <button
          class="memory-switch"
          :class="{ on: !chat.config.incognito }"
          :title="chat.config.incognito ? t('incognito_mode') : t('memory_active')"
          @click="chat.configure({ incognito: !chat.config.incognito })"
        >
          <MemorySwitchIcon :incognito="chat.config.incognito" :size="16" />
        </button>
        <button
          class="interjection-switch"
          :class="{ on: chat.config.interjections }"
          :title="chat.config.interjections ? t('interjections_on') : t('interjections_off')"
          @click="chat.configure({ interjections: !chat.config.interjections })"
        >
          <MessageSquarePlus v-if="chat.config.interjections" :size="16" />
          <MessageSquareOff v-else :size="16" />
        </button>
        <button class="agent-choices" :title="t('agent_choices_title')" @click="choicesOpen = true"><SlidersHorizontal :size="16" /></button>
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
.chat { --grip-gap: 10px; display: flex; flex-direction: column; gap: 10px; min-width: 0; padding-bottom: 12px; }
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.toolbar h2 { margin-right: auto; }
/* The text size and the ⋮ wrap as one: when they no longer fit beside the title they go down together, not one by one. */
.tools { display: flex; align-items: center; gap: 8px; margin-left: auto; }
/* The container of the agent pills' query: on a narrow row they show their emoji only. */
.who { min-width: 0; container-type: inline-size; }
.how { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.how .watch { margin-left: auto; }
h2 { margin: 0; font-size: 1.1rem; }
.agent-choices, .zoom { padding: 2px 8px; }
.all-read { padding: 2px 8px; }
.all-read:disabled { opacity: 0.5; cursor: default; }
.zoom:disabled { opacity: 0.5; cursor: default; }
/* The box keeps 70 % of the screen whatever the sizes; only its content takes the chat's size (the interface's
   size, set on the page, is taken out again). */
.messages-box { position: relative; }
/* Over the lower edge of the box, above the sticking question (z-index 3). */
.new-below { position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%); z-index: 4; box-shadow: 0 2px 8px var(--aifred-page-bg); }
.messages {
  box-sizing: border-box; /* the grip's height is the one the box takes */
  height: calc(var(--chat-height-desktop) / var(--ui-zoom));
  overflow-y: auto;
  /* The browser's scroll anchoring would carry the position along when the pinned question folds or unfolds (its
     height changes by the fold, the margin under it makes it good a tick later): near the top the box then jumped
     back to 0 on every touch and could not be scrolled away. The page keeps the position itself (follow scroll). */
  overflow-anchor: none;
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
.notice { color: var(--aifred-accent-amber); --icon-own: currentColor; }
.info { color: var(--aifred-accent-success); --icon-own: currentColor; }
</style>
