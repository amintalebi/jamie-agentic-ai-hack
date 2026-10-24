const USER_KEY = "jamie.userPhone";

export function getPhone(): string {
  try {
    return localStorage.getItem(USER_KEY) ?? "";
  } catch {
    return "";
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

export type Screen = { ui: string; live: boolean };

export async function fetchScreen(path: string, phone: string, signal?: AbortSignal): Promise<Screen> {
  const user = normalisePhone(phone || getPhone() || memoryPhone);
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`/api/dashboard${path}${sep}user=${encodeURIComponent(user)}`, { signal });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}