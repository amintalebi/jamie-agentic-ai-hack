import { Renderer } from "@openuidev/react-lang";
import { useEffect, useState } from "react";
import { AuthError, fetchScreen, setToken, type Screen } from "./api";
import { library } from "./library";

const LIVE_POLL_MS = 2000;
const IDLE_POLL_MS = 15000;

function useHashRoute(): string {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const onChange = () => setHash(location.hash);
    addEventListener("hashchange", onChange);
    return () => removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

/** "#/calls/abc" -> "/calls/abc"; anything else -> "/calls". */
function apiPath(hash: string): string {
  const m = hash.match(/^#\/calls\/([^/?#]+)/);
  return m ? `/calls/${m[1]}` : "/calls";
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; screen: Screen; at: Date; stale: boolean }
  | { kind: "auth"; message: string }
  | { kind: "error"; message: string };

export function App() {
  const hash = useHashRoute();
  const path = apiPath(hash);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [authTick, setAuthTick] = useState(0);

  // Pick up a token from the link once ("#token=..." or "#/calls/id?token=..."), then drop it from the URL.
  useEffect(() => {
    const m = location.hash.match(/[#?&]token=([^&]+)/);
    if (m) {
      setToken(decodeURIComponent(m[1]));
      const rest = location.hash.replace(/[?&]?token=[^&]+/, "");
      history.replaceState(null, "", location.pathname + (rest.length > 1 ? rest : "#/"));
      setAuthTick((t) => t + 1);
    }
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    let ctrl: AbortController | undefined;
    let stopped = false;

    async function tick() {
      ctrl = new AbortController();
      let live = false;
      try {
        const screen = await fetchScreen(path, ctrl.signal);
        live = screen.live;
        setState({ kind: "ok", screen, at: new Date(), stale: false });
      } catch (e) {
        if (stopped || (e as Error).name === "AbortError") return;
        if (e instanceof AuthError) {
          setState({ kind: "auth", message: e.message });
          return; // wait for a token
        }
        setState((s) => (s.kind === "ok" ? { ...s, stale: true } : { kind: "error", message: String(e) }));
      }
      if (!stopped && !document.hidden) timer = window.setTimeout(tick, live ? LIVE_POLL_MS : IDLE_POLL_MS);
    }

    const onVisible = () => {
      if (!document.hidden) {
        clearTimeout(timer);
        tick();
      }
    };
    setState((s) => (s.kind === "ok" ? s : { kind: "loading" }));
    tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      clearTimeout(timer);
      ctrl?.abort();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [path, authTick]);

  return (
    <div className="shell">
      <header className="topbar">
        <a href="#/" className="brand">
          <img src={`${import.meta.env.BASE_URL}icons/icon-192.png`} alt="" width={24} height={24} />
          Jamie
        </a>
        {state.kind === "ok" && (
          <span className={`sync ${state.stale ? "sync-stale" : ""}`}>
            {state.stale ? "Offline, showing last data" : `Updated ${state.at.toLocaleTimeString()}`}
          </span>
        )}
      </header>
      {state.kind === "loading" && <div className="page muted">Loading…</div>}
      {state.kind === "error" && <div className="page card error">Could not load: {state.message}</div>}
      {state.kind === "auth" && <TokenForm message={state.message} onSaved={() => setAuthTick((t) => t + 1)} />}
      {state.kind === "ok" && (
        <Renderer
          response={state.screen.ui}
          library={library}
          isStreaming={false}
          onError={(errors) => errors.length && console.warn("[dashboard] OpenUI errors", errors)}
        />
      )}
    </div>
  );
}

function TokenForm({ message, onSaved }: { message: string; onSaved: () => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="page card token"
      onSubmit={(e) => {
        e.preventDefault();
        setToken(value.trim());
        onSaved();
      }}
    >
      <h2>Dashboard token</h2>
      <p className="muted">Enter the DASHBOARD_TOKEN from the server's .env.</p>
      {message && <p className="muted small">{message}</p>}
      <input type="password" value={value} onChange={(e) => setValue(e.target.value)} autoFocus aria-label="Token" />
      <button type="submit" disabled={!value.trim()}>Open dashboard</button>
    </form>
  );
}
