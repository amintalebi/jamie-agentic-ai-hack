import { Renderer } from "@openuidev/react-lang";
import { useEffect, useState } from "react";
import { clearPhone, fetchScreen, getPhone, normalisePhone, setPhone, type Screen } from "./api";
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
  | { kind: "auth" }
  | { kind: "error"; message: string };

export function App() {
  const hash = useHashRoute();
  const path = apiPath(hash);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [loginTick, setLoginTick] = useState(0);
  const [phone, setPhoneState] = useState(getPhone);

  // No token, no OTP, no JWT: a phone number is all the login we need (hackathon demo).
  const loggedIn = Boolean(normalisePhone(phone));

  useEffect(() => {
    let timer: number | undefined;
    let ctrl: AbortController | undefined;
    let stopped = false;
    let inFlight = false; // one polling loop only: never start a tick while one is running

    async function tick() {
      inFlight = true;
      ctrl = new AbortController();
      let live = false;
      try {
        const screen = await fetchScreen(path, phone, ctrl.signal);
        live = screen.live;
        setState({ kind: "ok", screen, at: new Date(), stale: false });
      } catch (e) {
        inFlight = false;
        if (stopped || (e as Error).name === "AbortError") return;
        setState((s) => (s.kind === "ok" ? { ...s, stale: true } : { kind: "error", message: String(e) }));
      }
      inFlight = false;
      if (!stopped && !document.hidden) timer = window.setTimeout(tick, live ? LIVE_POLL_MS : IDLE_POLL_MS);
    }

    const onVisible = () => {
      if (!document.hidden && !inFlight) {
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
  }, [path, phone, loginTick]);

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
        {loggedIn && (
          <button type="button" className="logout" onClick={() => { clearPhone(); setPhoneState(""); }}>
            Sign out
          </button>
        )}
      </header>
      {!loggedIn && <PhoneForm onSaved={(p) => { setPhone(p); setPhoneState(p); setLoginTick((t) => t + 1); }} />}
      {loggedIn && (
        <>
          {state.kind === "loading" && <div className="page muted">Loading…</div>}
          {state.kind === "error" && <div className="page card error">Could not load: {state.message}</div>}
          {state.kind === "ok" && (
            <Renderer
              response={state.screen.ui}
              library={library}
              isStreaming={false}
              onError={(errors) => errors.length && console.warn("[dashboard] OpenUI errors", errors)}
            />
          )}
        </>
      )}
    </div>
  );
}

function PhoneForm({ onSaved }: { onSaved: (phone: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <form
      className="page card phone"
      onSubmit={(e) => {
        e.preventDefault();
        const p = normalisePhone(value);
        if (p) {
          setPhone(p);
          onSaved(p);
        }
      }}
    >
      <h2>Your phone number</h2>
      <p className="muted">Log in with the number you text Jamie from. No password, straight in.</p>
      <input
        type="tel"
        inputMode="tel"
        autoComplete="tel"
        placeholder="+49 151 0000 1234"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus
        aria-label="Phone number"
      />
      <button type="submit" disabled={!normalisePhone(value)}>See my calls</button>
    </form>
  );
}