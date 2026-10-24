const USER_KEY = "jamie.userPhone";

export function getPhone(): string {
  try {
    return localStorage.getItem(USER_KEY) ?? memoryPhone;
  } catch {
    return memoryPhone;
  }
}

export function setPhone(phone: string): void {
  try {
    localStorage.setItem(USER_KEY, phone);
  } catch {
    // private mode: phone lasts for this page only
  }
  memoryPhone = phone;
}

export function clearPhone(): void {
  try {
    localStorage.removeItem(USER_KEY);
  } catch {
    // ignore
  }
  memoryPhone = "";
}

let memoryPhone = "";

/** Normalise a phone string loosely (keep digits and a leading +). */
export function normalisePhone(raw: string): string {
  const s = raw.trim().replace(/[\s\-().]/g, "");
  if (!s) return "";
  if (s.startsWith("+")) return "+" + s.slice(1).replace(/\D/g, "");
  return "+" + s.replace(/\D/g, "");
}

const TOKEN_KEY = "jamie.token";
let memoryToken = "";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string): void {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // private mode: token lasts for this page only
  }
}

/** Thrown when the session is missing or expired, so the app can show the login form. */
export class AuthError extends Error {}

async function postJson(url: string, body: unknown): Promise<any> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `${res.status} ${res.statusText}`);
  return data;
}

/** Ask the server to text a login code to this number. */
export async function requestCode(phone: string): Promise<void> {
  await postJson("/api/auth/otp", { phone });
}

/** Check the code; returns the session token. */
export async function verifyCode(phone: string, code: string): Promise<string> {
  const data = await postJson("/api/auth/verify", { phone, code });
  return data.token;
}

export type Screen = { ui: string; live: boolean };

export async function fetchScreen(path: string, signal?: AbortSignal): Promise<Screen> {
  const res = await fetch(`/api/dashboard${path}`, {
    signal,
    headers: { authorization: `Bearer ${getToken()}` },
  });
  if (res.status === 401) throw new AuthError("Session expired, log in again");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}