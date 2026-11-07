// Hash history: the app needs no server-side rewrites and runs under any mount point.
import { createRouter, createWebHashHistory } from "vue-router";
import ChatView from "./views/ChatView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [{ path: "/", component: ChatView }],
});
