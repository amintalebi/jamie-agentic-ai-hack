import { Renderer } from "@openuidev/react-lang";
import { REGEXP_ONLY_DIGITS } from "input-otp";
import {
  ArrowLeft, ArrowRight, CircleAlert, Languages, Loader2, LogOut, MessageSquareText, PhoneCall, RefreshCw, Sparkles, Vote,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Aurora, Confetti, LiveDot, Wordmark } from "@/components/brand";
import { PhoneDemo } from "@/components/phone-demo";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import {
  ApiError, AuthError, clearPhone, fetchScreen, getPhone, getToken, normalisePhone, readSmsCode, requestCode,
  setPhone, setToken, verifyCode, type Screen,
} from "./api";
import { library, resetStats } from "./library";

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
  const [welcome, setWelcome] = useState(false);
  const loggedIn = Boolean(token);

  function logout() {
    setToken("");
    clearPhone();
    setTokenState("");
    setWelcome(false);
    resetStats();
    setState({ kind: "loading" }); // never show the previous account's calls to the next login
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

  if (!loggedIn) {
    return (
      <SignIn
        onLoggedIn={(t, newUser) => {
          setToken(t);
          setTokenState(t);
          setWelcome(newUser);
        }}
      />
    );
  }

  return (
    <div className="min-h-dvh">
      <Aurora intensity={0.55} still={state.kind === "ok" && state.screen.live} />
      <header className="glass sticky top-0 z-20 border-b border-border/60 pt-[env(safe-area-inset-top)]">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between gap-3 px-4">
          <a href="#/" aria-label="All calls">
            <Wordmark />
          </a>
          <div className="flex items-center gap-2">
            {state.kind === "ok" && <SyncPill live={state.screen.live} stale={state.stale} at={state.at} />}
            <AccountMenu onLogout={logout} />
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-4 pb-[calc(48px+env(safe-area-inset-bottom))] pt-6">
        {welcome && <Welcome onClose={() => setWelcome(false)} />}
        {state.kind === "loading" && <LoadingScreen />}
        {state.kind === "error" && (
          <div className="rise flex flex-col items-center gap-4 rounded-3xl border bg-card p-10 text-center shadow-soft">
            <span className="grid size-12 place-items-center rounded-2xl bg-bad-soft text-bad"><CircleAlert /></span>
            <div>
              <div className="font-semibold">Could not reach Jamie</div>
              <div className="mt-1 text-sm text-muted-foreground">{state.message}</div>
            </div>
            <Button variant="outline" className="rounded-full" onClick={() => location.reload()}>
              <RefreshCw /> Try again
            </Button>
          </div>
        )}
        {state.kind === "ok" && (
          <Renderer
            response={state.screen.ui}
            library={library}
            isStreaming={false}
            onError={(errors) => errors.length && console.warn("[dashboard] OpenUI errors", errors)}
          />
        )}
      </div>
    </div>
  );
}

function SyncPill({ live, stale, at }: { live: boolean; stale: boolean; at: Date }) {
  const label = stale ? "Offline" : live ? "Live" : "Synced";
  return (
    <span
      title={stale ? "Showing the last data we got" : `Updated ${at.toLocaleTimeString()}`}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium",
        stale ? "border-warn/30 bg-warn-soft text-warn" : live ? "border-live/25 bg-live-soft text-live" : "bg-card text-muted-foreground",
      )}
    >
      {live && !stale ? <LiveDot /> : <span className={cn("size-1.5 rounded-full", stale ? "bg-warn" : "bg-good")} />}
      {label}
    </span>
  );
}

function AccountMenu({ onLogout }: { onLogout: () => void }) {
  const last4 = getPhone().replace(/\D/g, "").slice(-4);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="Account"
          className="bg-brand grid size-8 place-items-center rounded-full text-[11px] font-semibold text-white shadow-soft ring-2 ring-background outline-none transition-transform hover:scale-105 focus-visible:ring-ring"
        >
          {last4 ? last4.slice(-2) : "J"}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56 rounded-xl">
        <DropdownMenuLabel className="font-normal">
          <div className="text-xs text-muted-foreground">Signed in as</div>
          <div className="font-medium tabular-nums">{last4 ? `•••• ${last4}` : "you"}</div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onLogout} className="text-bad focus:text-bad">
          <LogOut className="text-bad" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Welcome({ onClose }: { onClose: () => void }) {
  return (
    <div className="rise relative mb-6 overflow-hidden rounded-3xl border bg-card p-6 shadow-lift sm:p-8">
      <div aria-hidden className="bg-brand absolute inset-x-0 top-0 h-1" />
      <Confetti />
      <div className="relative flex flex-col gap-5 sm:flex-row sm:items-center">
        <span className="bg-brand grid size-14 shrink-0 place-items-center rounded-2xl text-white shadow-soft">
          <Sparkles className="size-7" />
        </span>
        <div className="flex-1">
          <h2 className="text-xl font-semibold tracking-tight">You're in. Say hi to Jamie!</h2>
          <p className="mt-1 text-muted-foreground">
            We just sent you an iMessage. Reply there with your first request, like
            <span className="text-foreground"> "book a table for 4 tomorrow at 8"</span>.
          </p>
        </div>
        <Button onClick={onClose} className="rounded-full px-5">Got it</Button>
      </div>
    </div>
  );
}

function LoadingScreen() {
  return (
    <div className="flex flex-col gap-6" aria-label="Loading">
      <div className="space-y-2">
        <Skeleton className="h-9 w-40 rounded-xl" />
        <Skeleton className="h-4 w-56" />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-2xl" />)}
      </div>
      <div className="space-y-3">
        {[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-2xl" />)}
      </div>
    </div>
  );
}

// --- sign in ----------------------------------------------------------------------

const FEATURES = [
  { icon: Vote, text: "Asks you with iMessage polls" },
  { icon: PhoneCall, text: "Phones businesses for you" },
  { icon: Languages, text: "Speaks German and English" },
];

function SignIn({ onLoggedIn }: { onLoggedIn: (token: string, newUser: boolean) => void }) {
  return (
    <div className="relative min-h-dvh overflow-x-hidden">
      <Aurora />
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 pt-[calc(16px+env(safe-area-inset-top))] sm:px-8">
        <Wordmark />
        <span className="hidden items-center gap-2 rounded-full border bg-card/60 px-3 py-1 text-xs text-muted-foreground backdrop-blur sm:inline-flex">
          <MessageSquareText className="size-3.5" /> Lives in iMessage
        </span>
      </header>

      <main className="mx-auto grid max-w-6xl items-center gap-12 px-4 pb-16 pt-10 sm:px-8 lg:min-h-[calc(100dvh-72px)] lg:grid-cols-[1.1fr_1fr] lg:pt-0">
        <section className="rise flex flex-col gap-8">
          <div className="space-y-5">
            <h1 className="text-balance text-[44px] font-semibold leading-[1.02] tracking-[-0.035em] sm:text-6xl lg:text-[56px]">
              Text it.
              <br />
              <span className="text-gradient">Jamie makes the call.</span>
            </h1>
            <p className="max-w-md text-lg text-muted-foreground">
              Your assistant on iMessage. It books the table, checks with you mid-call, and texts you when it's done.
            </p>
            <ul className="flex flex-wrap gap-2">
              {FEATURES.map(({ icon: Icon, text }) => (
                <li key={text} className="inline-flex items-center gap-1.5 rounded-full border bg-card/70 px-3 py-1.5 text-sm backdrop-blur">
                  <Icon className="size-4 text-primary" /> {text}
                </li>
              ))}
            </ul>
          </div>
          <LoginForm onLoggedIn={onLoggedIn} />
        </section>

        <section className="rise flex justify-center [animation-delay:150ms] lg:justify-end">
          <PhoneDemo />
        </section>
      </main>
    </div>
  );
}

/** Seconds left until `until` (ms timestamp), ticking every second. */
function useCountdown(until: number): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (until <= Date.now()) return;
    const t = window.setInterval(() => {
      const n = Date.now();
      setNow(n);
      if (n >= until) clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [until]);
  return Math.max(0, Math.ceil((until - now) / 1000));
}

/**
 * Signup and login in one: phone number, then the 6-digit code Twilio texts to it.
 * The code autofills (iOS keyboard suggestion via autocomplete="one-time-code", Android via WebOTP)
 * and the form submits itself once 6 digits are in.
 */
function LoginForm({ onLoggedIn }: { onLoggedIn: (token: string, newUser: boolean) => void }) {
  const [value, setValue] = useState(getPhone);
  const [phone, setPhoneSent] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The server's resend cooldown is per number, so remember which number it is for.
  const [resend, setResend] = useState({ phone: "", at: 0 });
  const wait = useCountdown(resend.phone === (phone || normalisePhone(value)) ? resend.at : 0);
  const lastTried = useRef("");

  async function run(target: string, action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      if (e instanceof ApiError && e.retryAfter) setResend({ phone: target, at: Date.now() + e.retryAfter * 1000 });
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const sendCode = (p: string) =>
    run(p, async () => {
      const retryAfter = await requestCode(p);
      setPhone(p);
      setPhoneSent(p);
      setCode("");
      lastTried.current = "";
      setResend({ phone: p, at: Date.now() + retryAfter * 1000 });
    });

  const submitCode = (c: string) => {
    lastTried.current = c;
    return run(phone, async () => {
      const { token, newUser } = await verifyCode(phone, c);
      onLoggedIn(token, newUser);
    });
  };

  // Android Chrome: take the code straight from the SMS while the code step is open.
  useEffect(() => {
    if (!phone) return;
    const ctrl = new AbortController();
    readSmsCode(ctrl.signal).then((c) => {
      if (c && !ctrl.signal.aborted) setCode(c.replace(/\D/g, "").slice(0, 6));
    });
    return () => ctrl.abort();
  }, [phone, resend.at]);

  // Submit once a full code is in (typed, pasted or autofilled), once per code.
  useEffect(() => {
    if (phone && code.length === 6 && !busy && code !== lastTried.current) submitCode(code);
  }, [code, phone, busy]);

  const card = "glass rise w-full max-w-md rounded-3xl border border-border/70 p-5 shadow-lift sm:p-6";

  if (!phone) {
    const valid = normalisePhone(value).length >= 8;
    return (
      <form
        key="phone"
        className={card}
        onSubmit={(e) => {
          e.preventDefault();
          const p = normalisePhone(value);
          if (p) sendCode(p);
        }}
      >
        <label htmlFor="phone" className="text-sm font-medium">Sign up or log in</label>
        <p className="mt-0.5 text-sm text-muted-foreground">Use the number you text Jamie from. New here? This creates your account.</p>
        <div className="mt-4 flex flex-col gap-2.5 sm:flex-row">
          <Input
            id="phone"
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            placeholder="+49 151 0000 1234"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
            aria-invalid={Boolean(error) || undefined}
            className="h-12 flex-1 rounded-xl bg-background/70 px-4 text-base tabular-nums md:text-base"
          />
          <Button
            type="submit"
            disabled={busy || wait > 0 || !valid}
            className="bg-brand group h-12 rounded-xl px-5 text-base font-semibold text-white shadow-soft transition-all hover:brightness-110 active:scale-[0.98]"
          >
            {busy && <Loader2 className="animate-spin" />}
            {busy ? "Sending" : wait > 0 ? `Wait ${wait} s` : "Text me a code"}
            {!busy && wait === 0 && <ArrowRight className="transition-transform group-hover:translate-x-0.5" />}
          </Button>
        </div>
        <FormError text={error} />
        <p className="mt-3 text-xs text-muted-foreground">We'll text you a 6-digit code. Standard SMS rates may apply.</p>
      </form>
    );
  }

  return (
    <form
      key="code"
      className={card}
      onSubmit={(e) => {
        e.preventDefault();
        if (code.length === 6) submitCode(code);
      }}
    >
      <button
        type="button"
        disabled={busy}
        onClick={() => { setPhoneSent(""); setError(""); setCode(""); }}
        className="-ml-1 inline-flex items-center gap-1 rounded-md px-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Change number
      </button>
      <div className="mt-3 text-sm font-medium">Enter the code</div>
      <p className="mt-0.5 text-sm text-muted-foreground">
        Sent to <span className="font-medium text-foreground tabular-nums">{phone}</span>. It fills in by itself on most phones.
      </p>
      <div className="mt-5 flex items-center gap-3">
        <InputOTP
          maxLength={6}
          pattern={REGEXP_ONLY_DIGITS}
          pasteTransformer={(s) => s.replace(/\D/g, "")}
          value={code}
          onChange={(v) => setCode(v.replace(/\D/g, "").slice(0, 6))}
          name="one-time-code"
          autoComplete="one-time-code"
          inputMode="numeric"
          autoFocus
          aria-label="Login code"
        >
          <InputOTPGroup className="gap-1.5 sm:gap-2">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <InputOTPSlot
                key={i}
                index={i}
                aria-invalid={Boolean(error) || undefined}
                className="size-11 rounded-xl border bg-background/70 text-lg font-semibold tabular-nums first:rounded-xl last:rounded-xl data-[active=true]:border-primary data-[active=true]:ring-primary/25 sm:size-12"
              />
            ))}
          </InputOTPGroup>
        </InputOTP>
        {busy && <Loader2 className="size-5 animate-spin text-primary" aria-label="Checking" />}
      </div>
      <FormError text={error} />
      <div className="mt-5 flex items-center justify-between gap-2 text-sm">
        <span className="text-muted-foreground">Didn't get it?</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={busy || wait > 0}
          onClick={() => sendCode(phone)}
          className="rounded-full font-medium text-primary hover:text-primary"
        >
          {wait > 0 ? <span className="tabular-nums">New code in {wait} s</span> : (<><RefreshCw /> Send a new code</>)}
        </Button>
      </div>
    </form>
  );
}

function FormError({ text }: { text: string }) {
  if (!text) return null;
  return (
    <p role="alert" className="rise mt-3 flex items-center gap-1.5 text-sm text-bad">
      <CircleAlert className="size-4 shrink-0" /> {text}
    </p>
  );
}
