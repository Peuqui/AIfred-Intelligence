import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "../api/client";
import type { LoggedIn, Me } from "../api/types";

export const useAuth = defineStore("auth", () => {
  const user = ref<string | null>(null);
  const checked = ref(false);

  async function check(): Promise<void> {
    user.value = (await api<Me>("auth/me")).user ?? null;
    checked.value = true;
  }

  async function login(username: string, password: string): Promise<void> {
    user.value = (await api<LoggedIn>("auth/login", { method: "POST", body: JSON.stringify({ username, password }) })).user;
  }

  // A new account for a name on the server's whitelist, logged in at once.
  async function register(username: string, password: string): Promise<void> {
    user.value = (await api<LoggedIn>("auth/register", { method: "POST", body: JSON.stringify({ username, password }) })).user;
  }

  async function logout(): Promise<void> {
    await api<void>("auth/logout", { method: "POST" });
    user.value = null;
  }

  return { user, checked, check, login, register, logout };
});
