<script setup lang="ts">
// The shell: the login, then the top bar with the pages and the page itself. The chat page stays alive
// while another page shows (its session and event stream go on).
import { onMounted, watch } from "vue";
import { RouterLink, RouterView } from "vue-router";
import { api } from "./api/client";
import LoginForm from "./components/LoginForm.vue";
import { loadLanguage, t } from "./i18n";
import { useAuth } from "./stores/auth";
import { useChat } from "./stores/chat";
import { useSettings } from "./stores/settings";

const auth = useAuth();
const chat = useChat();
const settings = useSettings();

async function logout(): Promise<void> {
  chat.close();
  await auth.logout();
}

watch(() => auth.user, (user) => user && void settings.load());

onMounted(async () => {
  // AIfred's UI language, also for the login form (the shell serves it without a login).
  await loadLanguage((await api<{ ui_language: string }>("app/ui-settings")).ui_language);
  await auth.check();
});
</script>

<template>
  <LoginForm v-if="auth.checked && !auth.user" />
  <div v-else-if="auth.user && settings.current" class="app">
    <header class="top">
      <strong>🎩 AIfred Intelligence</strong>
      <nav>
        <RouterLink to="/">💬 {{ t("nav_chat") }}</RouterLink>
        <RouterLink to="/documents">{{ t("doc_manager_title") }}</RouterLink>
        <RouterLink to="/settings">{{ t("settings") }}</RouterLink>
      </nav>
      <span class="user">{{ auth.user }}</span>
      <button class="logout" @click="logout">{{ t("logout") }}</button>
    </header>
    <RouterView v-slot="{ Component }">
      <KeepAlive><component :is="Component" class="page" /></KeepAlive>
    </RouterView>
  </div>
</template>

<style>
html,
body {
  margin: 0;
  background: var(--aifred-page-bg);
  color: var(--aifred-text-primary);
  font-family: system-ui, sans-serif;
}
</style>

<style scoped>
.app { display: flex; flex-direction: column; height: 100vh; padding: 0 16px; box-sizing: border-box; }
.top { display: flex; align-items: center; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--aifred-border); flex-wrap: wrap; }
nav { display: flex; gap: 12px; }
nav a { color: var(--aifred-text-secondary); text-decoration: none; padding: 2px 6px; border-radius: 6px; }
nav a.router-link-exact-active { color: var(--aifred-text-primary); background: var(--aifred-input-bg); }
.user { margin-left: auto; color: var(--aifred-text-secondary); }
.logout { background: none; border: 1px solid var(--aifred-border); color: inherit; border-radius: 6px; padding: 4px 10px; cursor: pointer; }
.page { flex: 1; min-height: 0; }
@media (max-width: 760px) {
  .app { padding: 0 8px; }
}
</style>
