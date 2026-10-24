// Live call transcript over Server-Sent Events (server/live.py, GET /api/dashboard/calls/{id}/live).
// EventSource cannot send the bearer token, so this reads the SSE stream with fetch.
// One store per call, shared by every component showing it: the dashboard re-renders every 2 s and
// may remount the component, which must not reopen the stream or lose lines.
import { useSyncExternalStore } from "react";
import { AuthError, getToken } from "./api";

export type Line = { speaker: "agent" | "business"; text: string };
export type LiveState = { lines: Line[]; partial: Line | null; ended: boolean; connected: boolean };

const EMPTY: LiveState = { lines: [], partial: null, ended: false, connected: false };
const CLOSE_GRACE_MS = 5000; // keep the stream open briefly after the last viewer unmounts
const MAX_BACKOFF_MS = 10000;

type Handler = (event: string, data: any) => void;

/** Read one SSE response until it ends. Resolves when the server closes the stream. */
async function readStream(callId: string, onEvent: Handler, signal: AbortSignal): Promise<void> {
  const res = await fetch(`/api/dashboard/calls/${encodeURIComponent(callId)}/live`, {
    signal,
    headers: { authorization: `Bearer ${getToken()}`, accept: "text/event-stream" },
  });
  if (res.status === 401) throw new AuthError("Session expired, log in again");
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) onEvent(event, JSON.parse(data.join("\n")));
    }
  }
}

class Store {
  state: LiveState = EMPTY;
  private listeners = new Set<() => void>();
  private ctrl: AbortController | undefined;
  private closeTimer: number | undefined;

  constructor(private callId: string) {}

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    clearTimeout(this.closeTimer);
    if (!this.ctrl) this.run();
    return () => {
      this.listeners.delete(fn);
      if (this.listeners.size === 0) {
        this.closeTimer = window.setTimeout(() => {
          this.ctrl?.abort();
          this.ctrl = undefined;
          stores.delete(this.callId);
        }, CLOSE_GRACE_MS);
      }
    };
  };

  private set(patch: Partial<LiveState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  private onEvent: Handler = (event, data) => {
    if (event === "snapshot") {
      this.set({ lines: data.lines ?? [], partial: data.partial ?? null, ended: Boolean(data.ended), connected: true });
    } else if (event === "line") {
      const partial = this.state.partial?.speaker === data.speaker ? null : this.state.partial;
      this.set({ lines: [...this.state.lines, data], partial });
    } else if (event === "partial") {
      this.set({ partial: data });
    } else if (event === "end") {
      this.set({ ended: true, partial: null });
    }
  };

  private async run() {
    const ctrl = (this.ctrl = new AbortController());
    let backoff = 1000;
    while (!ctrl.signal.aborted && !this.state.ended) {
      try {
        await readStream(this.callId, this.onEvent, ctrl.signal);
        backoff = 1000; // a clean close: reconnect right away unless the call ended
      } catch (e) {
        if (ctrl.signal.aborted || e instanceof AuthError) break; // the polling loop handles logout
      }
      if (ctrl.signal.aborted || this.state.ended) break;
      this.set({ connected: false });
      await new Promise((r) => setTimeout(r, backoff));
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    }
  }
}

const stores = new Map<string, Store>();

function store(callId: string): Store {
  let s = stores.get(callId);
  if (!s) stores.set(callId, (s = new Store(callId)));
  return s;
}

/** Live transcript of a call; opens the stream while any component uses it. */
export function useLiveTranscript(callId: string): LiveState {
  const s = store(callId);
  return useSyncExternalStore(s.subscribe, () => s.state, () => EMPTY);
}
