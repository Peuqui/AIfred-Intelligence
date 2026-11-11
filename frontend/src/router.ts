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

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: ChatView },
    { path: "/agents", component: AgentsView },
    { path: "/documents", component: DocumentsView },
    { path: "/memory", component: MemoryView },
    { path: "/jobs", component: JobsView },
    { path: "/audio", component: AudioView },
    { path: "/maintenance", component: MaintenanceView },
    { path: "/vision", redirect: "/vision/live" },
    { path: "/vision/:tab", component: VisionView },
    { path: "/settings", component: SettingsView },
  ],
});
