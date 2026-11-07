<script setup lang="ts">
import { ref } from "vue";
import { ApiError } from "../api/client";
import { t } from "../i18n";
import { useAuth } from "../stores/auth";

const auth = useAuth();
const username = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);

async function submit(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    await auth.login(username.value, password.value);
  } catch (failure) {
    error.value = failure instanceof ApiError && failure.status === 401 ? t("login_failed") : String(failure);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <form class="login" @submit.prevent="submit">
    <h1>{{ t("login_title") }}</h1>
    <label>{{ t("login_username") }}<input v-model="username" autocomplete="username" required /></label>
    <label>{{ t("login_password") }}<input v-model="password" type="password" autocomplete="current-password" required /></label>
    <p v-if="error" class="error">{{ error }}</p>
    <button type="submit" :disabled="busy">{{ t("login_submit") }}</button>
  </form>
</template>

<style scoped>
.login {
  max-width: 320px;
  margin: 15vh auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--aifred-card-bg);
  border: 1px solid var(--aifred-border);
  border-radius: 10px;
}
h1 { margin: 0; font-size: 1.2rem; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 0.9rem; color: var(--aifred-text-secondary); }
input {
  padding: 8px;
  background: var(--aifred-input-bg);
  color: var(--aifred-text-primary);
  border: 1px solid var(--aifred-border);
  border-radius: 6px;
}
button {
  padding: 8px;
  background: var(--aifred-primary);
  color: var(--aifred-text-on-accent);
  border: none;
  border-radius: 6px;
  cursor: pointer;
}
.error { margin: 0; color: var(--aifred-danger); font-size: 0.85rem; }
</style>
