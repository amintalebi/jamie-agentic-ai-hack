export function duration(s: number | null | undefined): string {
  if (s == null) return "";
  const m = Math.floor(s / 60);
  return m ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export type Tone = "neutral" | "live" | "good" | "bad" | "warn";

export function statusLook(status: string, outcome: string | null | undefined): { label: string; tone: Tone } {
  switch (status) {
    case "dialing": return { label: "Dialing", tone: "live" };
    case "active": return { label: "On the call", tone: "live" };
    case "waiting_user": return { label: "Waiting for you", tone: "warn" };
    case "failed": return { label: "Failed", tone: "bad" };
    case "ended":
      if (outcome === "success") return { label: "Done", tone: "good" };
      if (outcome === "partial") return { label: "Partly done", tone: "warn" };
      return { label: "No luck", tone: "bad" };
    default: return { label: status, tone: "neutral" };
  }
}
