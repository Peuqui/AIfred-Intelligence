<script setup lang="ts">
// The shell: the login, then the top bar (the pages behind ☰, as under Reflex) and the page itself. The chat
// page stays alive while another page shows (its session and event stream go on).
import { onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { api } from "./api/client";
import HubNoticeToast from "./components/HubNoticeToast.vue";
import LoginForm from "./components/LoginForm.vue";
import MediaPlayer from "./components/MediaPlayer.vue";
import ServiceStatusBar from "./components/ServiceStatusBar.vue";
import { loadLanguage, t } from "./i18n";
import { useAuth } from "./stores/auth";
import { useChat } from "./stores/chat";
import { useSettings } from "./stores/settings";

const auth = useAuth();
const chat = useChat();
const settings = useSettings();
const route = useRoute();
const menuOpen = ref(false);

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
      <RouterLink v-if="route.path !== '/'" to="/" class="back">💬 {{ t("nav_chat") }}</RouterLink>
      <span class="user">{{ auth.user }}</span>
      <button class="burger" :aria-expanded="menuOpen" :title="t('settings')" @click="menuOpen = !menuOpen">☰</button>
      <div v-if="menuOpen" class="backdrop" @click="menuOpen = false" />
      <nav v-if="menuOpen" class="menu" @click="menuOpen = false">
        <RouterLink to="/">💬 {{ t("nav_chat") }}</RouterLink>
        <RouterLink to="/agents">{{ t("nav_agents") }}</RouterLink>
        <RouterLink to="/documents">{{ t("doc_manager_title") }}</RouterLink>
        <RouterLink to="/vision">{{ t("nav_vision") }}</RouterLink>
        <RouterLink to="/memory">{{ t("nav_memory") }}</RouterLink>
        <RouterLink to="/jobs">{{ t("nav_jobs") }}</RouterLink>
        <RouterLink to="/audio">{{ t("nav_audio") }}</RouterLink>
        <RouterLink to="/settings">{{ t("settings") }}</RouterLink>
      </nav>
      <button class="logout" @click="logout">{{ t("logout") }}</button>
    </header>
    <ServiceStatusBar />
    <MediaPlayer />
    <HubNoticeToast />
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
/* One column of at most 1200 px, as under Reflex. */
.app { display: flex; flex-direction: column; min-height: 100vh; width: 100%; max-width: 1200px; margin: 0 auto; padding: 0 16px; box-sizing: border-box; }
.top { position: relative; display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--aifred-border); }
.top strong { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.back { color: var(--aifred-text-secondary); text-decoration: none; white-space: nowrap; }
.user { color: var(--aifred-text-secondary); white-space: nowrap; }
.burger { background: none; border: none; color: var(--aifred-primary); cursor: pointer; font-size: 1.3rem; padding: 0 4px; }
.backdrop { position: fixed; inset: 0; z-index: 40; }
.menu { position: absolute; top: 100%; right: 0; z-index: 41; display: flex; flex-direction: column; min-width: 220px; padding: 6px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 8px; }
.menu a { color: var(--aifred-text-secondary); text-decoration: none; padding: 8px 10px; border-radius: 6px; }
.menu a:hover, .menu a.router-link-active { color: var(--aifred-text-primary); background: var(--aifred-input-bg); }
.logout { white-space: nowrap; background: none; border: 1px solid var(--aifred-border); color: inherit; border-radius: 6px; padding: 4px 10px; cursor: pointer; }
.page { flex: 1; }
@media (max-width: 760px) {
  .app { padding: 0 8px; }
}
</style>
