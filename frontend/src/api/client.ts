// Thin fetch wrapper for the AIfred API. Same origin: the login cookie travels with every call.
// API paths are relative to the app's own folder (…/api/app/ → …/api/), so the build works
// under any mount point.

import { ref } from "vue";

const API_ROOT = new URL("../", document.baseURI);

// The server names the build it has installed in every answer (aifred/lib/api/app_shell.py, BUILD_ID_HEADER); a
// different one than this page's means a newer one is there (the shell loads it, App.vue).
const BUILD_ID_HEADER = "X-Build-Id";
export const newVersion = ref(false);
// How often an open page asks whether a newer build is there, besides every answer it gets anyway: an idle page (or
// the installed app back from the background) asked nothing, and the event streams bypass this wrapper.
export const UPDATE_CHECK_MS = 60_000;

export async function checkForUpdate(): Promise<void> {
  await api("app/ui-settings");
}

// How often a page asks again about a run on the server it follows (indexing, a burn-in).
export const PROGRESS_POLL_MS = 700;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

export function apiUrl(path: string): string {
  return new URL(path.replace(/^\//, ""), API_ROOT).toString();
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(apiUrl(path), { ...init, headers, credentials: "same-origin" });
  const serverBuild = response.headers.get(BUILD_ID_HEADER);
  // The Vite dev server's page is no installed build: nothing to compare.
  if (!import.meta.env.DEV && serverBuild !== null && serverBuild !== __BUILD_ID__) newVersion.value = true;
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, String(body.detail ?? response.statusText));
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}
