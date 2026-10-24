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

/** 401: the token is missing or wrong. */
export class AuthError extends Error {}
/** 503: the server has no DASHBOARD_TOKEN set, so no token can work. */
export class ConfigError extends Error {}

/** Token carried in a link: "#token=T" or "#/calls/id?token=T&x=1". */
export function tokenFromHash(hash: string): string | null {
  return new URLSearchParams(splitHash(hash)[1]).get("token");
}

/** The hash without its token param: "#/calls/id?token=T&x=1" -> "#/calls/id?x=1", "#token=T" -> "#/". */
export function stripToken(hash: string): string {
  const [route, query] = splitHash(hash);
  const params = new URLSearchParams(query);
  params.delete("token");
  const rest = params.toString();
  return (route.length > 1 ? route : "#/") + (rest ? `?${rest}` : "");
}

function splitHash(hash: string): [string, string] {
  if (hash.startsWith("#token=")) return ["#/", hash.slice(1)];
  const i = hash.indexOf("?");
  return i < 0 ? [hash, ""] : [hash.slice(0, i), hash.slice(i + 1)];
}

export type Screen = { ui: string; live: boolean };

export async function fetchScreen(path: string, signal?: AbortSignal): Promise<Screen> {
  const token = getToken() || memoryToken;
  const res = await fetch(`/api/dashboard${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (res.status === 401) throw new AuthError(await res.text());
  if (res.status === 503) throw new ConfigError(await res.text());
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
