// Hash history: the app needs no server-side rewrites and runs under any mount point.
import { createRouter, createWebHashHistory } from "vue-router";
import ChatView from "./views/ChatView.vue";
import DocumentsView from "./views/DocumentsView.vue";
import SettingsView from "./views/SettingsView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: ChatView },
    { path: "/documents", component: DocumentsView },
    { path: "/settings", component: SettingsView },
  ],
});
