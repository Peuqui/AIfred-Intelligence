<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { AgentSummary } from "../api/types";
import AgentSelect from "../components/AgentSelect.vue";
import ChatInput from "../components/ChatInput.vue";
import DebugConsole from "../components/DebugConsole.vue";
import LoginForm from "../components/LoginForm.vue";
import MessageBubble from "../components/MessageBubble.vue";
import SessionList from "../components/SessionList.vue";
import { loadLanguage, t } from "../i18n";
import { renderMarkdown } from "../lib/markdown";
import { useAuth } from "../stores/auth";
import { useChat } from "../stores/chat";
import { useSessions } from "../stores/sessions";

const auth = useAuth();
const sessions = useSessions();
const chat = useChat();
const agents = ref<AgentSummary[]>([]);
const messages = ref<HTMLElement | null>(null);
const streamingHtml = computed(() => (chat.streaming ? renderMarkdown(chat.streaming.text) : ""));
const hasReplies = computed(() => chat.history.some((entry) => entry.role === "assistant"));

async function start(): Promise<void> {
  agents.value = await api<AgentSummary[]>("agents/summary");
  await chat.loadDictationSettings();
  await chat.loadSpeechSettings();
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

async function logout(): Promise<void> {
  chat.close();
  await auth.logout();
}

// The newest message stays in view while an answer streams.
watch(
  () => [chat.history.length, chat.streaming?.text.length],
  async () => {
    await nextTick();
    if (messages.value) messages.value.scrollTop = messages.value.scrollHeight;
  },
);

watch(() => auth.user, (user) => user && void start());

onMounted(async () => {
  // AIfred's UI language, also for the login form (the shell serves it without a login).
  await loadLanguage((await api<{ ui_language: string }>("app/ui-settings")).ui_language);
  await auth.check();
});
</script>

<template>
  <LoginForm v-if="auth.checked && !auth.user" />
  <div v-else-if="auth.user" class="layout">
    <header class="top">
      <strong>🎩 AIfred Intelligence</strong>
      <span class="user">{{ auth.user }}</span>
      <button class="logout" @click="logout">{{ t("logout") }}</button>
    </header>
    <SessionList class="side" :current="chat.sessionId" @open="chat.open" @create="create" @remove="remove" />
    <main class="chat">
      <div class="toolbar">
        <h2>{{ chat.title || t("untitled_chat") }}</h2>
        <div class="choices">
          <button
            v-if="chat.speechSettings.enable_tts && hasReplies"
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
        </div>
      </div>
      <div ref="messages" class="messages">
        <MessageBubble
          v-for="entry in chat.history"
          :key="entry.timestamp + entry.role"
          :entry="entry"
          :speech-on="chat.speechSettings.enable_tts && entry.role === 'assistant'"
          @read-again="chat.readAgain"
        />
        <article v-if="chat.streaming" class="streaming">
          <header>{{ chat.streaming.emoji }} {{ chat.streaming.displayName }}</header>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized by renderMarkdown (DOMPurify) -->
          <div v-html="streamingHtml" />
          <p v-if="chat.toolStatus" class="tool">{{ chat.toolStatus }}</p>
        </article>
        <p v-if="chat.notice" class="notice">⚠️ {{ chat.notice }}</p>
      </div>
      <ChatInput
        v-model="chat.draft"
        :running="chat.running"
        :queued="chat.queued.length"
        :images="chat.pendingImages"
        :uploading="chat.uploading"
        :transcribing="chat.dictations.length > 0"
        :dictation="chat.dictationSettings"
        @send="chat.send"
        @stop="chat.stop"
        @upload="chat.uploadImages"
        @remove-image="chat.removeImage"
        @dictate="chat.dictate"
        @heard="chat.dictated"
        @dictation-settings="chat.changeDictationSettings"
      />
      <DebugConsole :lines="chat.debugLines" />
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: minmax(200px, 260px) 1fr;
  grid-template-rows: auto 1fr;
  height: 100vh;
  gap: 0 16px;
  padding: 0 16px;
  box-sizing: border-box;
}
.top { grid-column: 1 / -1; display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--aifred-border); }
.user { margin-left: auto; color: var(--aifred-text-secondary); }
.logout { background: none; border: 1px solid var(--aifred-border); color: inherit; border-radius: 6px; padding: 4px 10px; cursor: pointer; }
.side { padding-top: 12px; overflow: hidden; }
.chat { display: flex; flex-direction: column; gap: 10px; min-height: 0; padding: 12px 0; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 1rem; }
.choices { display: flex; align-items: center; gap: 10px; }
.incognito, .regenerate { background: none; border: 1px solid var(--aifred-border); border-radius: 6px; padding: 2px 8px; cursor: pointer; font: inherit; color: inherit; }
.regenerate:disabled { opacity: 0.7; cursor: progress; }
.incognito.on { border-color: var(--aifred-accent-amber); }
.messages { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding-right: 4px; }
.streaming { align-self: flex-start; max-width: min(860px, 92%); padding: 10px 14px; border-radius: 10px; border: 1px dashed var(--aifred-border-light); }
.streaming header { font-size: 0.8rem; font-weight: 600; color: var(--aifred-primary); }
.tool { margin: 4px 0 0; font-size: 0.8rem; color: var(--aifred-accent-cyan); }
.notice { color: var(--aifred-accent-amber); }
@media (max-width: 760px) {
  .layout { grid-template-columns: 1fr; grid-template-rows: auto auto 1fr; padding: 0 8px; }
  .side { max-height: 30vh; }
}
</style>
