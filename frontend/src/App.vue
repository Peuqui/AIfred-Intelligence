<script setup lang="ts">
// The shell: the login, then the top bar (the pages behind ☰, as under Reflex) and the page itself. The chat
// page stays alive while another page shows (its session and event stream go on).
import { Bot, Clock, Eye, Feather, Folder, LogOut, Menu, MessageSquare, NotebookPen, Settings, Wrench } from "lucide-vue-next";
import { type Component, computed, onMounted, ref, watch, watchEffect } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { UPDATE_CHECK_MS, api, checkForUpdate } from "./api/client";
import PluginGate from "./components/PluginGate.vue";
import FileViewer from "./components/FileViewer.vue";
import HubNoticeToast from "./components/HubNoticeToast.vue";
import LoginForm from "./components/LoginForm.vue";
import ServiceStatusBar from "./components/ServiceStatusBar.vue";
import TtsIcon from "./components/TtsIcon.vue";
import UpdateNotice from "./components/UpdateNotice.vue";
import { loadLanguage, t } from "./i18n";
import { useAuth } from "./stores/auth";
import { useChat } from "./stores/chat";
import { usePlugins } from "./stores/plugins";
import { useSettings } from "./stores/settings";
import { ZOOM, type ZoomName, chatZoom, consoleZoom, pinQuestion, resetZooms, uiZoom, zoomBy } from "./lib/devicePrefs";
import { useSessionsColumn } from "./lib/sessionsColumn";
import hatIcon from "../../assets/AIfred-Zylinder.png";

const auth = useAuth();
const chat = useChat();
const settings = useSettings();
const plugins = usePlugins();
const route = useRoute();
const router = useRouter();
// With the saved chats as a column (on the chat page) the page may be wider, so the chat keeps its width.
const sessionsColumn = useSessionsColumn().shown;
const wide = computed(() => sessionsColumn.value && route.path === "/");
const menuOpen = ref(false);

// The two sizes the menu offers (the chat's own is above the chat).
// The menu's pages, each its icon in a colour of its own from the palette (theme.py), as the coloured emojis
// before them were; the audio page's speaker keeps the TTS suite's own colours.
// The chat is amber, here and in the way back to it in the header.
const CHAT_COLOR = "var(--aifred-accent-amber)";
const menuEntries: { to: string; icon: Component; label: string; color?: string }[] = [
  { to: "/", icon: MessageSquare, label: "nav_chat", color: CHAT_COLOR },
  { to: "/agents", icon: Bot, label: "nav_agents", color: "var(--aifred-user-name)" },
  { to: "/documents", icon: Folder, label: "doc_manager_title", color: "var(--aifred-accent-blue)" },
  { to: "/vision", icon: Eye, label: "nav_vision", color: "var(--aifred-accent-purple)" },
  { to: "/memory", icon: NotebookPen, label: "nav_memory", color: "var(--aifred-accent-success)" },
  { to: "/jobs", icon: Clock, label: "nav_jobs", color: "var(--aifred-primary)" },
  { to: "/audio", icon: TtsIcon, label: "nav_audio" },
  { to: "/maintenance", icon: Wrench, label: "nav_maintenance", color: "var(--aifred-accent-cyan)" },
  { to: "/settings", icon: Settings, label: "settings", color: "var(--aifred-accent-gold)" },
  { to: "/auctor", icon: Feather, label: "nav_auctor", color: "var(--aifred-accent-purple)" },
];
// A page of a plugin (the route's meta `plugin`, router.ts) is in the menu only while the plugin is on.
const shownEntries = computed(() =>
  menuEntries.filter((entry) => {
    const plugin = router.resolve(entry.to).meta.plugin;
    return !plugin || plugins.toolOn(plugin);
  }),
);
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

async function logout(): Promise<void> {
  chat.close();
  await auth.logout();
}

// Logged in: the settings, and the session with its event stream, whatever page the user lands on.
watch(() => auth.user, (user) => {
  if (!user) return;
  void settings.load();
  void plugins.load();
  void chat.openLatest();
});

// Asked once a minute and whenever the page comes back to the front (a tab switched to, the app reopened).
setInterval(() => void checkForUpdate(), UPDATE_CHECK_MS);
// Also the lists and the open session are read anew then: what happened on another device or while the page slept
// reaches no page that was not listening (the user switches between devices).
function pageCameBack(): void {
  void checkForUpdate();
  if (auth.user) chat.resync();
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") pageCameBack();
});
window.addEventListener("focus", pageCameBack);
window.addEventListener("online", pageCameBack);

onMounted(async () => {
  // AIfred's UI language, also for the login form (the shell serves it without a login).
  await loadLanguage((await api<{ ui_language: string }>("app/ui-settings")).ui_language);
  await auth.check();
});
</script>

<template>
  <UpdateNotice />
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
      <RouterLink v-if="route.path !== '/'" to="/" class="back"><MessageSquare :size="16" :style="{ color: CHAT_COLOR }" /> {{ t("nav_chat") }}</RouterLink>
      <button class="burger" :aria-expanded="menuOpen" :title="t('settings')" @click="menuOpen = !menuOpen"><Menu :size="22" /></button>
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
        <RouterLink v-for="entry in shownEntries" :key="entry.to" :to="entry.to">
          <component :is="entry.icon" :size="16" class="menu-icon" :style="{ color: entry.color }" /> {{ t(entry.label) }}
        </RouterLink>
      </nav>
      <!-- The account as one element, set well apart from the navigation: who is logged in, and the way out. -->
      <div class="account">
        <span class="user">{{ auth.user }}</span>
        <button class="logout" :title="t('logout')" :aria-label="t('logout')" @click="logout"><LogOut :size="18" /></button>
      </div>
    </header>
    <ServiceStatusBar />
    <div class="divider" role="separator" />
    <HubNoticeToast />
    <RouterView v-slot="{ Component, route: shown }">
      <PluginGate v-if="shown.meta.plugin" :name="shown.meta.plugin">
        <component :is="Component" class="page" />
        <template #off><component :is="shown.meta.whileOff" v-if="shown.meta.whileOff" /></template>
      </PluginGate>
      <KeepAlive v-else><component :is="Component" class="page" /></KeepAlive>
    </RouterView>
    <FileViewer />
  </div>
</template>

<style>
/* One base size for the whole interface (config.py → theme.css); rem follows it. Form fields take the page's font:
   browsers do not pass it on by themselves (selects showed 13 px Arial). */
html { font-size: var(--text-base); }
/* No rectangle the phone's browser lays over a tapped button (on the round microphone it showed its box), as in
   Agent-Orc; the buttons show a press by themselves. */
html, body { -webkit-tap-highlight-color: transparent; }
/* A hundredth of the screen with the zoom of the part taken out: inside a zoomed element vw/vh grow with it (a dialog of
   88vw became wider than the screen). Every size in screen units uses these; a part with a zoom of its own (the chat's
   history, its field) sets them anew. */
:root { --vw: calc(1vw / var(--ui-zoom)); --vh: calc(1vh / var(--ui-zoom)); --dvh: calc(1dvh / var(--ui-zoom)); }
/* The one size of the big file dialogs (the viewer's box, the file requester's list): as wide as the page, about three
   quarters of the screen high, so a long list or a wide table needs little scrolling. */
:root { --file-dialog-width: min(var(--page-max-width), calc(90 * var(--vw))); --file-dialog-height: calc(75 * var(--dvh)); }
input, select, textarea { font: inherit; }
/* A box that scrolls by itself (the chat's history, a dialog, a list) keeps the wheel and the finger at its end: the page
   behind it no longer moves on when the box is at its bottom (Peuqui 25.11.2026). The page scrolls from outside the box.
   Not on html/body: there it would also switch off the phone's pull-to-refresh. Boxes with nothing to scroll pass the
   wheel on as before. */
body * { overscroll-behavior: contain; }
/* Lucide icons sit on the line of the text beside them (a label with an icon in front), and never shrink in a flex row. */
svg.lucide { vertical-align: -0.15em; flex: none; }
/* A switch as a pill, as on the Reflex page: grey when off, a muted orange when on (tools of an agent, agents of the chat). */
.pill { border-radius: 999px; padding: 2px 10px; font-size: var(--text-small); color: var(--aifred-text-muted); background: var(--aifred-input-bg); border: 1px solid var(--aifred-border); cursor: pointer; }
.pill.on { color: var(--aifred-primary); background: color-mix(in srgb, var(--aifred-primary) 18%, transparent); border-color: var(--aifred-primary); }
.pill:disabled { cursor: default; }
/* Agent-Orc's divider of the agents' cards: an orange line that fades out to both sides, with a glow (below the head,
   above the debug console). Its colour is a variable: the chat's "read up to here" line is the same line in green
   (ReadMark.vue). */
.divider {
  --divider-color: var(--aifred-primary);
  --divider-glow: var(--aifred-primary-hover);
  height: 1.5px;
  margin: 10px 0;
  background: linear-gradient(90deg, transparent, var(--divider-color) 50%, transparent);
  filter: drop-shadow(0 0 4px color-mix(in srgb, var(--divider-glow) 70%, transparent));
}
/* The interface's size on the parts it is for, as in Agent-Orc: never on the page as a whole. Zoomed with the body, the
   chat's scrolling box was zoomed too, and its placements (from rectangles on screen into its own scroll position) were
   off by the zoom: the "read up to here" line landed far below the upper edge (21.11.2026). A view that holds such a
   box sizes its parts itself (class zooms-itself, the chat). */
#app > :not(.app),
.app > :not(.zooms-itself) { zoom: var(--ui-zoom); }
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

.back { color: var(--aifred-text-secondary); text-decoration: none; white-space: nowrap; margin-right: 10px; }
.account { display: inline-flex; align-items: center; gap: 6px; margin-left: 18px; padding: 2px 4px 2px 10px; border: 1px solid color-mix(in srgb, var(--aifred-accent-amber) 45%, transparent); border-radius: 8px; }
.user { color: var(--aifred-text-secondary); white-space: nowrap; }
.burger { background: none; border: none; color: var(--aifred-primary); cursor: pointer; font-size: 1.3rem; padding: 0 4px; --icon-own: currentColor; }
.backdrop { position: fixed; inset: 0; z-index: 40; }
.menu { position: absolute; top: 100%; right: 0; z-index: 41; display: flex; flex-direction: column; min-width: 220px; padding: 6px; background: var(--aifred-card-bg); border: 1px solid var(--aifred-border); border-radius: 8px; }
.menu a { color: var(--aifred-text-secondary); text-decoration: none; padding: 8px 10px; border-radius: 6px; }
.pin { display: flex; align-items: center; gap: 6px; margin-top: 4px; cursor: pointer; }
.pin input { accent-color: var(--aifred-primary); }
.sizes { display: flex; flex-direction: column; gap: 4px; padding: 4px 10px 8px; margin-bottom: 4px; border-bottom: 1px solid var(--aifred-border); color: var(--aifred-text-secondary); font-size: 0.85rem; }
.size { display: flex; align-items: center; gap: 6px; }
.size span { flex: 1; }
.size button { min-width: 32px; }
.reset { align-self: flex-start; margin-top: 2px; background: none; border: 1px solid var(--aifred-border); border-radius: 6px; color: inherit; cursor: pointer; font: inherit; padding: 2px 8px; }
.menu a:hover, .menu a.router-link-active { color: var(--aifred-text-primary); background: var(--aifred-input-bg); }
/* Only the exit sign, everywhere: the tooltip names it. */
.logout { display: inline-flex; align-items: center; padding: 4px 6px; background: none; border: none; }
.page { flex: 1; }
@media (max-width: 760px) {
  .app { padding: 0 8px; }
  .top { padding: 8px 10px; gap: 8px; }
  .hat { width: 34px; height: 34px; }
  .brand strong { font-size: 1.15rem; }
  .user { display: none; } /* room for the title; the name is in every bubble of the user */
  .account { margin-left: 4px; padding-left: 4px; }
}
</style>
