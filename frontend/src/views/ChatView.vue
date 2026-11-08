<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { AgentSummary, CropBox, DebateMode } from "../api/types";
import AgentChoicesModal from "../components/AgentChoicesModal.vue";
import AgentSelect from "../components/AgentSelect.vue";
import DebateControls from "../components/DebateControls.vue";
import VisionBadge from "../components/VisionBadge.vue";
import ChatInput from "../components/ChatInput.vue";
import DebugConsole from "../components/DebugConsole.vue";
import ImageEditor from "../components/ImageEditor.vue";
import MessageBubble from "../components/MessageBubble.vue";
import SessionList from "../components/SessionList.vue";
import TurnProgress from "../components/TurnProgress.vue";
import { t } from "../i18n";
import { ZOOM, chatZoom, zoomBy } from "../lib/devicePrefs";
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
const agents = ref<AgentSummary[]>([]);
const modes = ref<DebateMode[]>([]);
const messages = ref<HTMLElement | null>(null);
const streamingHtml = computed(() => (chat.streaming ? renderMarkdown(chat.streaming.text) : ""));
const hasReplies = computed(() => chat.history.some((entry) => entry.role === "assistant"));
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
  modes.value = await api<DebateMode[]>("debate/modes");
  await sessions.refresh();
  const latest = sessions.list[0];
  await chat.open(latest ? latest.session_id : await sessions.create());
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

// The newest message stays in view while an answer streams, as long as the reader stands at the end: scrolled up
// to read, the view stays where it is until they come back down (until 08.11.2026 every token pulled it down).
const FOLLOW_SLACK_PX = 40;
watch(
  () => [chat.history.length, chat.streaming?.text.length, progress.value?.text],
  async () => {
    const view = messages.value;
    const atEnd = !view || view.scrollHeight - view.scrollTop - view.clientHeight < FOLLOW_SLACK_PX;
    await nextTick();
    if (view && atEnd) view.scrollTop = view.scrollHeight;
  },
);
// A session opened shows its end.
watch(() => chat.sessionId, async () => {
  await nextTick();
  if (messages.value) messages.value.scrollTop = messages.value.scrollHeight;
});

// The shell shows this page only once logged in; kept alive, it opens a session once per login.
onMounted(() => void start());
</script>

<template>
  <div class="layout">
    <SessionList class="side" :current="chat.sessionId" @open="chat.open" @create="create" @remove="remove" />
    <main class="chat">
      <div class="toolbar">
        <h2>{{ chat.title || t("untitled_chat") }}</h2>
        <div class="choices">
          <button class="zoom" :disabled="chatZoom <= ZOOM.min" :title="t('chat_font_smaller')" @click="zoomBy('chat', -1)">A−</button>
          <button class="zoom" :disabled="chatZoom >= ZOOM.max" :title="t('chat_font_larger')" @click="zoomBy('chat', 1)">A+</button>
          <button class="agent-choices" :title="t('agent_choices_title')" @click="choicesOpen = true">🎛️</button>
          <button
            v-if="settings.current?.enable_tts && hasReplies"
            class="regenerate"
            :disabled="chat.regenerating"
            @click="chat.regenerateAll"
          >
            {{ t("tts_regenerate_all") }}
          </button>
          <button
            class="incognito"
            :class="{ on: chat.config.incognito }"
            :title="chat.config.incognito ? t('incognito_mode') : t('memory_active')"
            @click="chat.configure({ incognito: !chat.config.incognito })"
          >
            {{ chat.config.incognito ? "🔒" : "🔓" }}
          </button>
          <button class="clear" :disabled="!chat.history.length" :title="t('clear_chat')" @click="chat.clearChat">🗑️</button>
          <button
            class="remember"
            :disabled="!hasReplies || chat.remembering || chat.running"
            :title="`${t('save_memory')} — ${t('save_memory_hint')}`"
            @click="chat.remember"
          >
            📌
          </button>
          <button class="export" :disabled="!chat.history.length || exporting" :title="t('share_chat')" @click="shareChat">🔗</button>
          <button class="export" :disabled="!chat.history.length || exporting" :title="t('download_chat')" @click="downloadChat">💾</button>
          <button class="export" :disabled="!chat.sessionId" :title="t('sandbox_outputs_tooltip')" @click="outputsOpen = true">📂</button>
          <AgentSelect :agents="agents" :active="chat.config.active_agent" @choose="(agent) => chat.configure({ active_agent: agent })" />
          <DebateControls :config="chat.config" :agents="agents" :modes="modes" @configure="chat.configure" />
          <VisionBadge />
        </div>
      </div>
      <div ref="messages" class="messages">
        <div class="bubbles">
        <MessageBubble
          v-for="entry in chat.history"
          :key="entry.timestamp + entry.role"
          :entry="entry"
          :speech-on="!!settings.current?.enable_tts && entry.role === 'assistant'"
          @read-again="chat.readAgain"
        />
        <article v-if="chat.streaming" class="streaming">
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
        @settings="settings.change"
      />
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
.chat { display: flex; flex-direction: column; gap: 10px; min-width: 0; padding-bottom: 12px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 1rem; }
.choices { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.incognito, .regenerate, .agent-choices, .export, .clear, .remember, .zoom { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 8px; cursor: pointer; font: inherit; color: inherit; }
.regenerate:disabled, .export:disabled, .remember:disabled { opacity: 0.7; cursor: progress; }
.clear:disabled, .zoom:disabled { opacity: 0.5; cursor: default; }
.incognito.on { border-color: var(--aifred-accent-amber); }
/* The box keeps 70 % of the screen whatever the sizes; only its content takes the chat's size (the interface's
   size, set on the page, is taken out again). */
.messages {
  height: calc(70vh / var(--ui-zoom));
  overflow-y: auto;
  overflow-wrap: anywhere;
  padding: 10px;
  background: var(--aifred-chat-panel-bg);
  border: 1px solid var(--aifred-border);
  border-radius: 8px;
}
.bubbles { display: flex; flex-direction: column; gap: 10px; zoom: calc(var(--chat-zoom) / var(--ui-zoom)); }
.streaming { align-self: flex-start; max-width: min(860px, 92%); padding: 10px 14px; border-radius: 10px; border: 1px dashed var(--aifred-border-light); }
.streaming header { font-size: 0.8rem; font-weight: 600; color: var(--aifred-primary); }
.tool { margin: 4px 0 0; font-size: 0.8rem; color: var(--aifred-accent-cyan); }
.waiting { align-self: flex-start; padding: 0 14px; }
.notice { color: var(--aifred-accent-amber); }
.info { color: var(--aifred-accent-success); }
</style>
