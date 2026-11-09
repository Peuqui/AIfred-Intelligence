<script setup lang="ts">
// The shell: the login, then the top bar (the pages behind ☰, as under Reflex) and the page itself. The chat
// page stays alive while another page shows (its session and event stream go on).
import { LogOut } from "lucide-vue-next";
import { computed, onMounted, ref, watch, watchEffect } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";
import { UPDATE_CHECK_MS, api, checkForUpdate, newVersion } from "./api/client";
import HubNoticeToast from "./components/HubNoticeToast.vue";
import LoginForm from "./components/LoginForm.vue";
import ServiceStatusBar from "./components/ServiceStatusBar.vue";
import { loadLanguage, t } from "./i18n";
import { useAuth } from "./stores/auth";
import { useChat } from "./stores/chat";
import { useSettings } from "./stores/settings";
import { ZOOM, type ZoomName, chatZoom, consoleZoom, pinQuestion, resetZooms, uiZoom, zoomBy } from "./lib/devicePrefs";
import { useSessionsColumn } from "./lib/sessionsColumn";
import hatIcon from "../../assets/AIfred-Zylinder.png";

const auth = useAuth();
const chat = useChat();
const settings = useSettings();
const route = useRoute();
// With the saved chats as a column (on the chat page) the page may be wider, so the chat keeps its width.
const sessionsColumn = useSessionsColumn().shown;
const wide = computed(() => sessionsColumn.value && route.path === "/");
const menuOpen = ref(false);

// The two sizes the menu offers (the chat's own is above the chat).
const sizes = computed<{ name: ZoomName; label: string; value: number }[]>(() => [
  { name: "ui", label: "size_interface", value: uiZoom.value },
  { name: "console", label: "size_console", value: consoleZoom.value },
]);

// The three sizes of this device reach the stylesheet as numbers: the interface scales the whole page (body), the
// chat's history and the console take it out again and apply their own.
watchEffect(() => {
  const root = document.documentElement.style;
  root.setProperty("--ui-zoom", String(uiZoom.value / 100));
  root.setProperty("--chat-zoom", String(chatZoom.value / 100));
  root.setProperty("--console-zoom", String(consoleZoom.value / 100));
});

function reloadPage(): void {
  window.location.reload();
}

async function logout(): Promise<void> {
  chat.close();
  await auth.logout();
}

watch(() => auth.user, (user) => user && void settings.load());

// A newer build is installed: load it as soon as nothing would be lost (a draft, images, a dictation, a running
// answer); until then the bar says so, and a click loads it at once.
const updateBlocked = computed(
  () => !!chat.draft.trim() || chat.pendingImages.length > 0 || chat.dictations.length > 0 || chat.running,
);
watch([newVersion, updateBlocked], ([available, blocked]) => {
  if (available && !blocked) window.location.reload();
});
// Asked once a minute and whenever the page comes back to the front (a tab switched to, the app reopened).
setInterval(() => void checkForUpdate(), UPDATE_CHECK_MS);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") void checkForUpdate();
});

onMounted(async () => {
  // AIfred's UI language, also for the login form (the shell serves it without a login).
  await loadLanguage((await api<{ ui_language: string }>("app/ui-settings")).ui_language);
  await auth.check();
});
</script>

<template>
  <LoginForm v-if="auth.checked && !auth.user" />
  <div v-else-if="auth.user && settings.current" class="app" :class="{ wide }">
    <header class="top">
      <div class="brand">
        <img :src="hatIcon" alt="" class="hat" />
        <div>
          <strong>AIfred Intelligence</strong>
          <!-- One line per part of the subtitle (split at its "•"): "AI at your service", then "Benannt nach …". -->
          <small>
            <span v-for="(part, index) in t('subtitle').split(' • ')" :key="index">{{ part }}</span>
          </small>
        </div>
      </div>
      <RouterLink v-if="route.path !== '/'" to="/" class="back">💬 {{ t("nav_chat") }}</RouterLink>
      <span class="user">{{ auth.user }}</span>
      <button class="burger" :aria-expanded="menuOpen" :title="t('settings')" @click="menuOpen = !menuOpen">☰</button>
      <div v-if="menuOpen" class="backdrop" @click="menuOpen = false" />
      <nav v-if="menuOpen" class="menu" @click="menuOpen = false">
        <div class="sizes" @click.stop>
          <span>{{ t("display_sizes") }}</span>
          <div v-for="size in sizes" :key="size.name" class="size">
            <span>{{ t(size.label) }}</span>
            <button :disabled="size.value <= ZOOM.min" :title="t('chat_font_smaller')" @click="zoomBy(size.name, -1)">−</button>
            <button :disabled="size.value >= ZOOM.max" :title="t('chat_font_larger')" @click="zoomBy(size.name, 1)">+</button>
          </div>
          <button class="reset" @click="resetZooms">{{ t("display_sizes_reset") }}</button>
          <!-- Also of this device: a phone starts with it off. -->
          <label class="pin" :title="t('pin_question_hint')"><input v-model="pinQuestion" type="checkbox" /> {{ t("pin_question") }}</label>
        </div>
        <RouterLink to="/">💬 {{ t("nav_chat") }}</RouterLink>
        <RouterLink to="/agents">{{ t("nav_agents") }}</RouterLink>
        <RouterLink to="/documents">{{ t("doc_manager_title") }}</RouterLink>
        <RouterLink to="/vision">{{ t("nav_vision") }}</RouterLink>
        <RouterLink to="/memory">{{ t("nav_memory") }}</RouterLink>
        <RouterLink to="/jobs">{{ t("nav_jobs") }}</RouterLink>
        <RouterLink to="/audio">{{ t("nav_audio") }}</RouterLink>
        <RouterLink to="/settings">{{ t("settings") }}</RouterLink>
      </nav>
      <button class="logout" :title="t('logout')" :aria-label="t('logout')" @click="logout"><LogOut :size="18" /></button>
    </header>
    <button v-if="newVersion" class="update" @click="reloadPage">{{ t("new_version_waiting") }}</button>
    <ServiceStatusBar />
    <div class="divider" role="separator" />
    <HubNoticeToast />
    <RouterView v-slot="{ Component }">
      <KeepAlive><component :is="Component" class="page" /></KeepAlive>
    </RouterView>
  </div>
</template>

<style>
/* One base size for the whole interface (config.py → theme.css); rem follows it. Form fields take the page's font:
   browsers do not pass it on by themselves (selects showed 13 px Arial). */
html { font-size: var(--text-base); }
/* No rectangle the phone's browser lays over a tapped button (on the round microphone it showed its box), as in
   Agent-Orc; the buttons show a press by themselves. */
html, body { -webkit-tap-highlight-color: transparent; }
/* A hundredth of the screen with the interface's size taken out: the body is zoomed by --ui-zoom, and inside it
   vw/vh would grow with it (a dialog of 88vw became wider than the screen). Every size in screen units uses these. */
:root { --vw: calc(1vw / var(--ui-zoom)); --vh: calc(1vh / var(--ui-zoom)); --dvh: calc(1dvh / var(--ui-zoom)); }
button, input, select, textarea { font: inherit; }
/* A switch as a pill, as on the Reflex page: grey when off, a muted orange when on (tools of an agent, agents of the chat). */
.pill { border-radius: 999px; padding: 2px 10px; font-size: var(--text-small); color: var(--aifred-text-muted); background: var(--aifred-input-bg); border: 1px solid var(--aifred-border); cursor: pointer; }
.pill.on { color: var(--aifred-primary); background: color-mix(in srgb, var(--aifred-primary) 18%, transparent); border-color: var(--aifred-primary); }
.pill:disabled { cursor: default; }
/* Agent-Orc's divider of the agents' cards: an orange line that fades out to both sides, with a glow (below the head,
   above the debug console). */
.divider {
  height: 1.5px;
  margin: 10px 0;
  background: linear-gradient(90deg, transparent, var(--aifred-primary) 50%, transparent);
  filter: drop-shadow(0 0 4px color-mix(in srgb, var(--aifred-primary-hover) 70%, transparent));
}
/* The interface's size on the body only: on html and body together it acted twice (130 % became 169 %). */
body { zoom: var(--ui-zoom); }
/* As on the Reflex page, the near-white is for what one reads and writes (chat bubbles, fields, headings); labels,
   descriptions and the rest of the page in the softer grey (Peuqui 09.11.2026: too much white). */
html,
body {
  margin: 0;
  /* The browser's own parts dark (scrollbars, opened selects, date fields), as the Reflex page had it (custom.css). */
  color-scheme: dark;
  background: var(--aifred-page-bg);
  color: var(--aifred-text-soft);
  font-family: system-ui, sans-serif;
}
h1, h2, h3, input, textarea, select { color: var(--aifred-text-primary); }
</style>

<style scoped>
/* One column of at most 1200 px, as under Reflex. */
.app { display: flex; flex-direction: column; min-height: calc(100 * var(--vh)); width: 100%; max-width: var(--page-max-width); margin: 0 auto; padding: 0 16px; box-sizing: border-box; }
.app.wide { max-width: var(--page-max-width-with-sessions); }
.top { position: relative; display: flex; align-items: center; gap: 12px; margin-top: 10px; padding: 10px 14px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 10px; }
/* The brand as on the Reflex page (the picture, not the emoji: its band is red), on a bar set off from the page. */
.brand { flex: 1; min-width: 0; display: flex; align-items: center; gap: 10px; }
.brand > div { min-width: 0; display: flex; flex-direction: column; }
.hat { width: 44px; height: 44px; flex: none; }
.brand strong { font-size: 1.5rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.brand small { color: var(--aifred-text-secondary); font-style: italic; font-size: 0.75rem; display: flex; flex-direction: column; }

.back { color: var(--aifred-text-secondary); text-decoration: none; white-space: nowrap; }
.user { color: var(--aifred-text-secondary); white-space: nowrap; }
.burger { background: none; border: none; color: var(--aifred-primary); cursor: pointer; font-size: 1.3rem; padding: 0 4px; }
.backdrop { position: fixed; inset: 0; z-index: 40; }
.menu { position: absolute; top: 100%; right: 0; z-index: 41; display: flex; flex-direction: column; min-width: 220px; padding: 6px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 8px; }
.menu a { color: var(--aifred-text-secondary); text-decoration: none; padding: 8px 10px; border-radius: 6px; }
.pin { display: flex; align-items: center; gap: 6px; margin-top: 4px; cursor: pointer; }
.pin input { accent-color: var(--aifred-primary); }
.sizes { display: flex; flex-direction: column; gap: 4px; padding: 4px 10px 8px; margin-bottom: 4px; border-bottom: 1px solid var(--aifred-border); color: var(--aifred-text-secondary); font-size: 0.85rem; }
.size { display: flex; align-items: center; gap: 6px; }
.size span { flex: 1; }
.size button { min-width: 32px; background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; font: inherit; }
.size button:disabled { opacity: 0.4; cursor: default; }
.reset { align-self: flex-start; margin-top: 2px; background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; font: inherit; padding: 2px 8px; }
.menu a:hover, .menu a.router-link-active { color: var(--aifred-text-primary); background: var(--aifred-input-bg); }
.update { margin: 6px 0; padding: 6px 10px; border: 1px solid var(--aifred-primary); border-radius: 6px; background: var(--aifred-input-bg); color: var(--aifred-primary); cursor: pointer; font: inherit; }
/* Only the exit sign, everywhere: the tooltip names it. */
.logout { display: inline-flex; align-items: center; background: none; border: 1px solid var(--aifred-border); color: inherit; border-radius: 6px; padding: 4px 8px; cursor: pointer; }
.page { flex: 1; }
@media (max-width: 760px) {
  .app { padding: 0 8px; }
  .top { padding: 8px 10px; gap: 8px; }
  .hat { width: 34px; height: 34px; }
  .brand strong { font-size: 1.15rem; }
  .user { display: none; } /* room for the title; the name is in every bubble of the user */
}
</style>
