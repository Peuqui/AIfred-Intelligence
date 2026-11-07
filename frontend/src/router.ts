// Hash history: the app needs no server-side rewrites and runs under any mount point.
import { createRouter, createWebHashHistory } from "vue-router";
import AgentsView from "./views/AgentsView.vue";
import ChatView from "./views/ChatView.vue";
import DocumentsView from "./views/DocumentsView.vue";
import MemoryView from "./views/MemoryView.vue";
import SettingsView from "./views/SettingsView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: ChatView },
    { path: "/agents", component: AgentsView },
    { path: "/documents", component: DocumentsView },
    { path: "/memory", component: MemoryView },
    { path: "/settings", component: SettingsView },
  ],
});
