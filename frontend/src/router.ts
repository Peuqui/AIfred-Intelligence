// Hash history: the app needs no server-side rewrites and runs under any mount point.
import { createRouter, createWebHashHistory } from "vue-router";
import AgentsView from "./views/AgentsView.vue";
import ChatView from "./views/ChatView.vue";
import DocumentsView from "./views/DocumentsView.vue";
import JobsView from "./views/JobsView.vue";
import MaintenanceView from "./views/MaintenanceView.vue";
import MemoryView from "./views/MemoryView.vue";
import SettingsView from "./views/SettingsView.vue";
import AudioView from "./views/AudioView.vue";
import VisionView from "./views/VisionView.vue";

// A route with `plugin` shows the page only while that tool plugin is on (App.vue, PluginGate).
declare module "vue-router" {
  interface RouteMeta {
    plugin?: string;
  }
}

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: ChatView },
    { path: "/agents", component: AgentsView },
    { path: "/documents", component: DocumentsView },
    { path: "/memory", component: MemoryView },
    { path: "/jobs", component: JobsView, meta: { plugin: "scheduler_tool" } },
    { path: "/audio", component: AudioView },
    { path: "/maintenance", component: MaintenanceView },
    // The parent carries the plugin for the pages below it and for the menu's link to "/vision" (resolve takes no redirect).
    { path: "/vision", meta: { plugin: "vision" }, redirect: "/vision/live", children: [{ path: ":tab", component: VisionView }] },
    { path: "/settings", component: SettingsView },
  ],
});
