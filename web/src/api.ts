const TOKEN_KEY = "jamie.dashboardToken";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // private mode: token lasts for this page only
  }
  memoryToken = token;
}

let memoryToken = "";

export class AuthError extends Error {}

export type Screen = { ui: string; live: boolean };

export async function fetchScreen(path: string, signal?: AbortSignal): Promise<Screen> {
  const token = getToken() || memoryToken;
  const res = await fetch(`/api/dashboard${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (res.status === 401 || res.status === 503) throw new AuthError(await res.text());
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
