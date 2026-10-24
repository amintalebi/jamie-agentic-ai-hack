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

/** A failed request; retryAfter (seconds) is set when the server asks us to wait (429). */
export class ApiError extends Error {
  constructor(message: string, readonly retryAfter = 0) {
    super(message);
  }
}

async function postJson(url: string, body: unknown): Promise<any> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = typeof data.detail === "string" ? data.detail : `${res.status} ${res.statusText}`;
    throw new ApiError(message, Number(res.headers.get("retry-after")) || 0);
  }
  return data;
}

/** Ask the server to text a login code to this number; returns seconds until a resend is allowed. */
export async function requestCode(phone: string): Promise<number> {
  const data = await postJson("/api/auth/otp", { phone });
  return Number(data.retry_after) || 0;
}

/** Check the code; returns the session token and whether this login created the account. */
export async function verifyCode(phone: string, code: string): Promise<{ token: string; newUser: boolean }> {
  const data = await postJson("/api/auth/verify", { phone, code });
  return { token: data.token, newUser: Boolean(data.new_user) };
}

/**
 * Android Chrome: read the code from the incoming SMS (WebOTP). Needs the SMS to end with
 * "@<this host> #<code>" (server APP_DOMAIN). Resolves "" where unsupported or when aborted.
 * iOS needs no code here: autocomplete="one-time-code" offers the code above the keyboard.
 */
export async function readSmsCode(signal: AbortSignal): Promise<string> {
  if (!("OTPCredential" in window)) return "";
  try {
    const cred = (await navigator.credentials.get({ otp: { transport: ["sms"] }, signal } as CredentialRequestOptions)) as
      | (Credential & { code?: string })
      | null;
    return cred?.code ?? "";
  } catch {
    return "";
  }
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

export type VoiceInfo = {
  voice_id: string | null;
  voice_name: string | null;
  configured: boolean;
};

function authHeaders(): HeadersInit {
  return { authorization: `Bearer ${getToken()}` };
}

async function readApiError(res: Response): Promise<string> {
  const data = await res.json().catch(() => ({}));
  return typeof data.detail === "string" ? data.detail : `${res.status} ${res.statusText}`;
}

export async function fetchVoice(signal?: AbortSignal): Promise<VoiceInfo> {
  const res = await fetch("/api/voices", { signal, headers: authHeaders() });
  if (res.status === 401) throw new AuthError("Session expired, log in again");
  if (!res.ok) throw new ApiError(await readApiError(res));
  return res.json();
}

export async function uploadVoice(file: File): Promise<VoiceInfo> {
  const body = new FormData();
  body.append("sample", file);
  const res = await fetch("/api/voices", { method: "POST", headers: authHeaders(), body });
  if (res.status === 401) throw new AuthError("Session expired, log in again");
  if (!res.ok) throw new ApiError(await readApiError(res));
  return res.json();
}

export async function deleteVoice(): Promise<void> {
  const res = await fetch("/api/voices", { method: "DELETE", headers: authHeaders() });
  if (res.status === 401) throw new AuthError("Session expired, log in again");
  if (!res.ok) throw new ApiError(await readApiError(res));
}