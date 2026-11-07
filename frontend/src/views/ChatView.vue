<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { AgentSummary, CropBox, DebateMode } from "../api/types";
import AgentChoicesModal from "../components/AgentChoicesModal.vue";
import AgentSelect from "../components/AgentSelect.vue";
import DebateControls from "../components/DebateControls.vue";
import ChatInput from "../components/ChatInput.vue";
import DebugConsole from "../components/DebugConsole.vue";
import ImageEditor from "../components/ImageEditor.vue";
import MessageBubble from "../components/MessageBubble.vue";
import SessionList from "../components/SessionList.vue";
import { t } from "../i18n";
import { renderMarkdown } from "../lib/markdown";
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

// The agents' quick choices (models and switches) behind the 🎛️ button.
const choicesOpen = ref(false);

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

async function remove(sessionId: string): Promise<void> {
  await sessions.remove(sessionId);
  if (sessionId === chat.sessionId) {
    const next = sessions.list[0];
    await chat.open(next ? next.session_id : await sessions.create());
  }
}

// The newest message stays in view while an answer streams.
watch(
  () => [chat.history.length, chat.streaming?.text.length],
  async () => {
    await nextTick();
    if (messages.value) messages.value.scrollTop = messages.value.scrollHeight;
  },
);

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
          <AgentSelect :agents="agents" :active="chat.config.active_agent" @choose="(agent) => chat.configure({ active_agent: agent })" />
          <DebateControls :config="chat.config" :agents="agents" :modes="modes" @configure="chat.configure" />
        </div>
      </div>
      <div ref="messages" class="messages">
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
        </article>
        <p v-if="chat.notice" class="notice">⚠️ {{ chat.notice }}</p>
        <p v-if="settings.notice" class="notice">⚠️ {{ settings.notice }}</p>
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
.layout {
  display: grid;
  grid-template-columns: minmax(200px, 260px) 1fr;
  grid-template-rows: minmax(0, 1fr);
  gap: 0 16px;
}
.side { padding-top: 12px; overflow: hidden; }
.chat { display: flex; flex-direction: column; gap: 10px; min-height: 0; padding: 12px 0; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 1rem; }
.choices { display: flex; align-items: center; gap: 10px; }
.incognito, .regenerate, .agent-choices { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 8px; cursor: pointer; font: inherit; color: inherit; }
.regenerate:disabled { opacity: 0.7; cursor: progress; }
.incognito.on { border-color: var(--aifred-accent-amber); }
.messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding-right: 4px; }
.streaming { align-self: flex-start; max-width: min(860px, 92%); padding: 10px 14px; border-radius: 10px; border: 1px dashed var(--aifred-border-light); }
.streaming header { font-size: 0.8rem; font-weight: 600; color: var(--aifred-primary); }
.tool { margin: 4px 0 0; font-size: 0.8rem; color: var(--aifred-accent-cyan); }
.notice { color: var(--aifred-accent-amber); }
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .side { max-height: 30vh; }
}
</style>
