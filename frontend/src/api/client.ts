// Thin fetch wrapper for the AIfred API. Same origin: the login cookie travels with every call.
// API paths are relative to the app's own folder (…/api/app/ → …/api/), so the build works
// under any mount point.

const API_ROOT = new URL("../", document.baseURI);

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
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, String(body.detail ?? response.statusText));
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}
