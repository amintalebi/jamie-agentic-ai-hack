import { Renderer } from "@openuidev/react-lang";
import { useEffect, useState } from "react";
import {
  AuthError, clearPhone, fetchScreen, getPhone, getToken, normalisePhone, requestCode, setPhone, setToken,
  verifyCode, type Screen,
} from "./api";
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
  | { kind: "error"; message: string };

export function App() {
  const hash = useHashRoute();
  const path = apiPath(hash);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [token, setTokenState] = useState(getToken);
  const loggedIn = Boolean(token);

  function logout() {
    setToken("");
    clearPhone();
    setTokenState("");
  }

  useEffect(() => {
    if (!token) return;
    let timer: number | undefined;
    let ctrl: AbortController | undefined;
    let stopped = false;
    let inFlight = false; // one polling loop only: never start a tick while one is running

    async function tick() {
      inFlight = true;
      ctrl = new AbortController();
      let live = false;
      try {
        const screen = await fetchScreen(path, ctrl.signal);
        live = screen.live;
        setState({ kind: "ok", screen, at: new Date(), stale: false });
      } catch (e) {
        inFlight = false;
        if (stopped || (e as Error).name === "AbortError") return;
        if (e instanceof AuthError) {
          logout();
          return;
        }
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
  }, [path, token]);

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
          <button type="button" className="logout" onClick={logout}>
            Sign out
          </button>
        )}
      </header>
      {!loggedIn && <LoginForm onLoggedIn={(t) => { setToken(t); setTokenState(t); }} />}
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

/** Two steps: phone number, then the 6-digit code Twilio texts to it. */
function LoginForm({ onLoggedIn }: { onLoggedIn: (token: string) => void }) {
  const [value, setValue] = useState(getPhone);
  const [phone, setPhoneSent] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const sendCode = (p: string) =>
    run(async () => {
      await requestCode(p);
      setPhone(p);
      setPhoneSent(p);
      setCode("");
    });

  if (!phone) {
    return (
      <form
        className="page card phone"
        onSubmit={(e) => {
          e.preventDefault();
          const p = normalisePhone(value);
          if (p) sendCode(p);
        }}
      >
        <h2>Your phone number</h2>
        <p className="muted">Log in with the number you text Jamie from. We will text you a code.</p>
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
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy || !normalisePhone(value)}>{busy ? "Sending…" : "Text me a code"}</button>
      </form>
    );
  }

  return (
    <form
      className="page card phone"
      onSubmit={(e) => {
        e.preventDefault();
        run(async () => onLoggedIn(await verifyCode(phone, code)));
      }}
    >
      <h2>Enter the code</h2>
      <p className="muted">We texted a 6-digit code to {phone}.</p>
      <input
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={6}
        placeholder="123456"
        value={code}
        onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
        autoFocus
        aria-label="Login code"
      />
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Log in"}</button>
      <div className="row">
        <button type="button" className="link" disabled={busy} onClick={() => sendCode(phone)}>Send a new code</button>
        <button type="button" className="link" disabled={busy} onClick={() => { setPhoneSent(""); setError(""); }}>
          Change number
        </button>
      </div>
    </form>
  );
}
