<script setup lang="ts">
// The hint of a channel's message at the top of the page; a click opens its session in the chat.
import { useRouter } from "vue-router";
import { t } from "../i18n";
import { useChat } from "../stores/chat";
import { useHubNotice } from "../stores/hubNotice";

const hub = useHubNotice();
const chat = useChat();
const router = useRouter();

async function open(): Promise<void> {
  const notice = hub.notice;
  hub.dismiss();
  if (!notice?.session_id) return;
  await router.push("/");
  await chat.open(notice.session_id);
}
</script>

<template>
  <div v-if="hub.notice" class="toast" :class="hub.notice.status" :title="hub.notice.session_title" @click="open">
    {{ t(`hub_toast_${hub.notice.status}`, { channel: hub.notice.channel, sender: hub.notice.sender }) }}
    <button class="close" :title="t('dialog_close')" @click.stop="hub.dismiss">×</button>
  </div>
</template>

<style scoped>
.toast {
  position: fixed; top: 12px; left: 50%; transform: translateX(-50%); z-index: 50;
  max-width: min(420px, 92vw); display: flex; align-items: center; gap: 10px; padding: 8px 12px;
  background: var(--aifred-card-bg); border: 1px solid var(--aifred-border-light); border-radius: 8px;
  cursor: pointer; font-size: 0.9rem; box-shadow: 0 4px 16px color-mix(in srgb, var(--aifred-media-bg) 50%, transparent);
}
.toast.done { border-color: var(--aifred-accent-success); }
.toast.error { border-color: var(--aifred-danger); }
.close { background: none; border: none; color: inherit; cursor: pointer; font-size: 1.1rem; margin-left: auto; }
</style>
