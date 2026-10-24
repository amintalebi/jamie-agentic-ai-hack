// OpenUI component library for the dashboard.
// The server (server/dashboard.py) writes screens in OpenUI Lang using these components.
// Arguments are positional, in the order of each schema's keys: keep both sides in sync.
import { createLibrary, defineComponent } from "@openuidev/react-lang";
import {
  Activity, ArrowLeft, Ban, CalendarCheck, Check, ChevronRight, CircleCheck, CircleX, Clock, Hourglass,
  MessageCircleQuestion, MessageCircleReply, Phone, PhoneCall, PhoneOff, PhoneOutgoing, Radio, Scissors, Sparkles,
  Stethoscope, Store, TriangleAlert, Utensils, Wifi, type LucideIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { z } from "zod/v4";
import { Confetti, LiveDot, Logo, TypingDots, Waveform } from "@/components/brand";
import { Badge as UiBadge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { duration, statusLook, when, type Tone } from "./format";

const tone = z.enum(["neutral", "live", "good", "bad", "warn"]);

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-muted-foreground",
  live: "text-live",
  good: "text-good",
  bad: "text-bad",
  warn: "text-warn",
};
const TONE_CHIP: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground",
  live: "bg-live-soft text-live",
  good: "bg-good-soft text-good",
  bad: "bg-bad-soft text-bad",
  warn: "bg-warn-soft text-warn",
};

function StatusBadge({ label, tone, className }: { label: string; tone: Tone; className?: string }) {
  return (
    <UiBadge className={cn("h-6 gap-1.5 rounded-full border-0 px-2.5 text-[12px] font-semibold", TONE_CHIP[tone], className)}>
      {tone === "live" ? <LiveDot className="size-1.5" /> : tone === "warn" ? <Hourglass /> : tone === "good" ? <Check /> : tone === "bad" ? <CircleX /> : null}
      {label}
    </UiBadge>
  );
}

/** A friendly icon for what the call is about, guessed from its goal. */
function goalIcon(goal: string): LucideIcon {
  const g = goal.toLowerCase();
  if (/table|restaurant|dinner|lunch|reserv|tisch|pizza|sushi/.test(g)) return Utensils;
  if (/doctor|dentist|arzt|appointment|termin|clinic/.test(g)) return Stethoscope;
  if (/hair|barber|friseur|salon/.test(g)) return Scissors;
  if (/cancel|storn/.test(g)) return Ban;
  if (/book|confirm/.test(g)) return CalendarCheck;
  return Phone;
}

const isLiveStatus = (s: string) => s === "dialing" || s === "active" || s === "waiting_user";

// Last number shown per stat label, so a remount (new screen, re-parse) picks up where it was
// instead of counting from 0 again.
const lastShown = new Map<string, number>();

/** Forget the stats on screen (call on sign out, so the next account counts from 0). */
export function resetStats() {
  lastShown.clear();
}

/** Animate a number string to its value, starting from whatever is on screen now. */
function CountUp({ id, value }: { id: string; value: string }) {
  const target = /^\d+$/.test(value) ? Number(value) : null;
  const current = useRef(lastShown.get(id) ?? 0);
  const [shown, setShown] = useState(target === null ? value : String(current.current));
  useEffect(() => {
    if (target === null) return setShown(value);
    const show = (n: number) => {
      current.current = n;
      lastShown.set(id, n);
      setShown(String(n));
    };
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return show(target);
    const start = performance.now();
    const base = current.current;
    let raf = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - start) / 700);
      show(Math.round(base + (target - base) * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [id, value]);
  return <>{shown}</>;
}

const STAT_ICON: Record<Tone, LucideIcon> = { live: Radio, neutral: PhoneCall, good: CircleCheck, bad: CircleX, warn: Hourglass };

export const Stat = defineComponent({
  name: "Stat",
  description: "One headline number",
  props: z.object({ label: z.string(), value: z.string(), tone: tone }),
  component: ({ props }) => {
    const Icon = STAT_ICON[props.tone];
    const hot = props.tone === "live" && props.value !== "0";
    return (
      <div
        className={cn(
          "group relative overflow-hidden rounded-2xl border bg-card p-4 shadow-soft transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lift",
          hot && "live-border",
        )}
      >
        <div className="flex items-center justify-between">
          <span className="text-[13px] font-medium text-muted-foreground">{props.label}</span>
          <span className={cn("grid size-7 place-items-center rounded-full", TONE_CHIP[props.tone])}>
            {hot ? <LiveDot /> : <Icon className="size-3.5" />}
          </span>
        </div>
        <div className={cn("mt-3 text-[34px] font-semibold leading-none tracking-tight tabular-nums", props.value !== "0" && TONE_TEXT[props.tone], props.tone === "neutral" && "text-foreground")}>
          <CountUp id={props.label} value={props.value} />
        </div>
      </div>
    );
  },
});

export const StatRow = defineComponent({
  name: "StatRow",
  description: "A row of headline numbers",
  props: z.object({ stats: z.array(Stat.ref) }),
  component: ({ props, renderNode }) => <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{renderNode(props.stats)}</div>,
});

export const CallRow = defineComponent({
  name: "CallRow",
  description: "One call in the list; links to its detail screen",
  props: z.object({
    callId: z.string(),
    title: z.string(),
    subtitle: z.string(),
    status: z.string(),
    outcome: z.string().optional(),
    createdAt: z.string().optional(),
    durationS: z.number().optional(),
  }),
  component: ({ props }) => {
    const look = statusLook(props.status, props.outcome);
    const live = isLiveStatus(props.status);
    const Icon = goalIcon(props.title);
    return (
      <a
        href={`#/calls/${encodeURIComponent(props.callId)}`}
        className={cn(
          "group flex items-center gap-4 rounded-2xl border bg-card p-4 shadow-soft transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lift active:scale-[0.99]",
          live && "live-border",
        )}
      >
        <span className={cn("relative grid size-12 shrink-0 place-items-center rounded-2xl", live ? "bg-brand text-white" : TONE_CHIP[look.tone])}>
          {live && <span className="ring-out absolute inset-0 rounded-2xl bg-primary/40" />}
          <Icon className="relative size-5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 font-semibold leading-snug tracking-tight">{props.title}</span>
          <span className="mt-0.5 flex items-center gap-1.5 truncate text-sm text-muted-foreground">
            {live && look.tone === "live" ? (
              <><Waveform bars={4} className="h-3 text-live" /> Live now</>
            ) : (
              <>{when(props.createdAt)}{props.durationS != null && <> · {duration(props.durationS)}</>}</>
            )}
          </span>
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">{props.subtitle}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <StatusBadge {...look} />
          <ChevronRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
        </span>
      </a>
    );
  },
});

export const CallList = defineComponent({
  name: "CallList",
  description: "The list of calls, newest first",
  props: z.object({ rows: z.array(CallRow.ref) }),
  component: ({ props, renderNode }) => (
    <section className="flex flex-col gap-3">
      <h2 className="px-1 text-sm font-semibold text-muted-foreground">Recent calls</h2>
      <div className="flex flex-col gap-2.5">{renderNode(props.rows)}</div>
    </section>
  ),
});

export const Empty = defineComponent({
  name: "Empty",
  description: "Empty state",
  props: z.object({ text: z.string() }),
  component: ({ props }) => (
    <div className="relative flex flex-col items-center overflow-hidden rounded-3xl border bg-card px-6 py-14 text-center shadow-soft">
      <div aria-hidden className="bg-brand absolute -top-24 size-64 rounded-full opacity-20 blur-3xl" />
      <span className="relative">
        <span className="ring-out absolute inset-0 rounded-[30%] bg-primary/30" />
        <Logo className="relative size-16" />
      </span>
      <h3 className="relative mt-6 text-xl font-semibold tracking-tight">Nothing on the line yet</h3>
      <p className="relative mt-2 max-w-sm text-muted-foreground">{props.text}</p>
      <div className="relative mt-6 rounded-[18px] rounded-br-[6px] bg-imessage px-4 py-2 text-[15px] text-white shadow-soft">
        Book a table for 4 tomorrow at 8 🍝
      </div>
      <span className="relative mt-2 text-xs text-muted-foreground">Try texting Jamie this</span>
    </div>
  ),
});

export const BackLink = defineComponent({
  name: "BackLink",
  description: "Link back to a screen",
  props: z.object({ label: z.string(), href: z.string() }),
  component: ({ props }) => (
    <a
      href={props.href}
      className="group inline-flex items-center gap-1.5 self-start rounded-full border bg-card/70 py-1.5 pl-2 pr-3.5 text-sm font-medium shadow-soft backdrop-blur transition-colors hover:bg-card"
    >
      <ArrowLeft className="size-4 transition-transform group-hover:-translate-x-0.5" />
      {props.label}
    </a>
  ),
});

/** Circular progress of the call against its time limit, with the elapsed time inside. */
function TimerRing({ seconds, max, live }: { seconds: number; max: number; live: boolean }) {
  const r = 34;
  const c = 2 * Math.PI * r;
  const pct = Math.min(1, seconds / Math.max(max, 1));
  return (
    <div className="relative grid size-[88px] shrink-0 place-items-center" aria-label={`${duration(seconds)} of ${duration(max)}`}>
      <svg viewBox="0 0 80 80" className="absolute inset-0 -rotate-90">
        <defs>
          <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--brand-1)" />
            <stop offset="55%" stopColor="var(--brand-2)" />
            <stop offset="100%" stopColor="var(--brand-3)" />
          </linearGradient>
        </defs>
        <circle cx="40" cy="40" r={r} fill="none" stroke="var(--muted)" strokeWidth="6" />
        <circle
          cx="40" cy="40" r={r} fill="none" stroke={live ? "url(#ring)" : "var(--muted-foreground)"} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)} className="transition-[stroke-dashoffset] duration-700"
        />
      </svg>
      <div className="text-center">
        <div className="text-[15px] font-semibold leading-none tabular-nums">{duration(seconds) || "0s"}</div>
        <div className="mt-1 text-[10px] text-muted-foreground">of {duration(max)}</div>
      </div>
    </div>
  );
}

const LANG: Record<string, string> = { de: "🇩🇪 Deutsch", en: "🇬🇧 English" };

export const CallHeader = defineComponent({
  name: "CallHeader",
  description: "Goal, state and timing of one call",
  props: z.object({
    title: z.string(),
    subtitle: z.string(),
    status: z.string(),
    outcome: z.string().optional(),
    language: z.string(),
    createdAt: z.string().optional(),
    durationS: z.number().optional(),
    maxS: z.number(),
  }),
  component: ({ props }) => {
    const look = statusLook(props.status, props.outcome);
    const live = isLiveStatus(props.status);
    const onHold = props.status === "waiting_user";
    const Icon = live ? PhoneCall : look.tone === "good" ? CircleCheck : look.tone === "bad" ? PhoneOff : goalIcon(props.title);
    return (
      <div className="rounded-3xl">
        <div className={cn("relative overflow-hidden rounded-3xl border bg-card p-5 shadow-lift sm:p-6", live && "live-border live-border-spin")}>
          {live && <div aria-hidden className="bg-brand absolute -right-20 -top-20 size-60 rounded-full opacity-15 blur-3xl" />}
          {props.status === "ended" && props.outcome === "success" && <Confetti count={28} />}
          <div className="relative flex items-start gap-4">
            <span className={cn("relative grid size-14 shrink-0 place-items-center rounded-2xl", live ? "bg-brand text-white shadow-soft" : TONE_CHIP[look.tone])}>
              {live && !onHold && <><span className="ring-out absolute inset-0 rounded-2xl bg-primary/40" /><span className="ring-out absolute inset-0 rounded-2xl bg-primary/30 [animation-delay:900ms]" /></>}
              <Icon className="relative size-6" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge {...look} />
                <span className="text-xs text-muted-foreground">{LANG[props.language] ?? props.language.toUpperCase()}</span>
              </div>
              <h1 className="mt-2 text-[22px] font-semibold leading-tight tracking-tight sm:text-2xl">{props.title}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
                <span>{props.subtitle}</span>
                {props.createdAt && <span className="inline-flex items-center gap-1"><Clock className="size-3.5" /> {when(props.createdAt)}</span>}
              </div>
            </div>
            {live ? (
              <TimerRing seconds={props.durationS ?? 0} max={props.maxS} live />
            ) : (
              props.durationS != null && (
                <div className="shrink-0 text-right">
                  <div className="text-xl font-semibold tabular-nums sm:text-2xl">{duration(props.durationS)}</div>
                  <div className="text-xs text-muted-foreground">call length</div>
                </div>
              )
            )}
          </div>
          {live && (
            <div className="relative mt-5 flex items-center gap-3 rounded-2xl bg-muted/70 px-4 py-3">
              {onHold ? (
                <>
                  <TypingDots className="text-warn" />
                  <span className="text-sm font-medium">Business on hold while Jamie checks with you</span>
                </>
              ) : (
                <>
                  <Waveform bars={28} className="h-6 flex-1 text-primary/80" />
                  <span className="shrink-0 text-sm font-medium">{props.status === "dialing" ? "Ringing…" : "Jamie is talking"}</span>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    );
  },
});

export const PendingQuestion = defineComponent({
  name: "PendingQuestion",
  description: "The business is on hold waiting for the user's answer",
  props: z.object({ question: z.string(), options: z.array(z.string()) }),
  component: ({ props }) => (
    <div className="relative overflow-hidden rounded-3xl border border-warn/40 bg-warn-soft p-5 shadow-soft sm:p-6">
      <div aria-hidden className="absolute -right-10 -top-10 size-40 rounded-full bg-warn opacity-20 blur-3xl" />
      <div className="relative flex items-center gap-2 text-[13px] font-semibold text-warn">
        <MessageCircleQuestion className="size-4" /> Your answer is needed
        <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-card/80 px-2.5 py-1 text-[11px] font-medium text-foreground">
          <span className="size-1.5 rounded-full bg-imessage" /> Reply in iMessage
        </span>
      </div>
      <div className="relative mt-3 text-lg font-semibold leading-snug tracking-tight sm:text-xl">{props.question}</div>
      <div className="relative mt-4 grid gap-2 sm:grid-cols-2">
        {props.options.map((o) => (
          <div key={o} className="flex items-center justify-between rounded-xl bg-card px-4 py-3 font-medium shadow-soft">
            {o}
            <span className="size-4 rounded-full border-2 border-muted-foreground/30" />
          </div>
        ))}
      </div>
    </div>
  ),
});

export const Note = defineComponent({
  name: "Note",
  description: "Highlighted summary line",
  props: z.object({ text: z.string() }),
  component: ({ props }) => (
    <div className="relative overflow-hidden rounded-3xl border bg-card p-5 shadow-soft">
      <div aria-hidden className="bg-brand absolute inset-y-0 left-0 w-1" />
      <div className="flex items-start gap-3">
        <span className="bg-brand grid size-9 shrink-0 place-items-center rounded-xl text-white"><Sparkles className="size-4" /></span>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Summary</div>
          <div className="mt-1 text-[17px] font-semibold leading-snug tracking-tight">{props.text}</div>
        </div>
      </div>
    </div>
  ),
});

export const KeyValue = defineComponent({
  name: "KeyValue",
  description: "Label and value",
  props: z.object({ label: z.string(), value: z.string() }),
  component: ({ props }) => (
    <div className="grid grid-cols-[minmax(110px,35%)_1fr] gap-4 py-3 first:pt-0 last:pb-0">
      <dt className="text-sm text-muted-foreground first-letter:uppercase">{props.label}</dt>
      <dd className="m-0 font-medium [overflow-wrap:anywhere]">{props.value}</dd>
    </div>
  ),
});

const TL: Record<string, { icon: LucideIcon; cls: string }> = {
  placed: { icon: PhoneOutgoing, cls: "bg-muted text-muted-foreground" },
  started: { icon: PhoneCall, cls: "bg-live-soft text-live" },
  progress: { icon: Activity, cls: "bg-live-soft text-live" },
  question: { icon: MessageCircleQuestion, cls: "bg-warn-soft text-warn" },
  answer: { icon: MessageCircleReply, cls: "bg-imessage text-white" },
  poll_failed: { icon: TriangleAlert, cls: "bg-warn-soft text-warn" },
  result: { icon: CircleCheck, cls: "bg-good text-white" },
  failed: { icon: CircleX, cls: "bg-bad text-white" },
  lost: { icon: Wifi, cls: "bg-bad-soft text-bad" },
};

export const TimelineItem = defineComponent({
  name: "TimelineItem",
  description: "One event in a call's timeline",
  props: z.object({ kind: z.string(), label: z.string(), text: z.string(), at: z.string().optional() }),
  component: ({ props }) => {
    const t = TL[props.kind] ?? { icon: Activity, cls: "bg-muted text-muted-foreground" };
    const Icon = t.icon;
    return (
      <li className="relative grid grid-cols-[32px_1fr] gap-3 pb-5 last:pb-0 [&:not(:last-child)]:before:absolute [&:not(:last-child)]:before:bottom-0 [&:not(:last-child)]:before:left-[15px] [&:not(:last-child)]:before:top-9 [&:not(:last-child)]:before:w-0.5 [&:not(:last-child)]:before:rounded-full [&:not(:last-child)]:before:bg-border [&:not(:last-child)]:before:content-['']">
        <span className={cn("grid size-8 place-items-center rounded-full ring-4 ring-card", t.cls)}>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 pt-1">
          <div className="flex flex-wrap items-baseline justify-between gap-x-2">
            <span className="font-semibold">{props.label}</span>
            <span className="text-xs tabular-nums text-muted-foreground">{when(props.at)}</span>
          </div>
          <div className="mt-0.5 text-sm text-muted-foreground [overflow-wrap:anywhere]">{props.text}</div>
        </div>
      </li>
    );
  },
});

export const TranscriptLine = defineComponent({
  name: "TranscriptLine",
  description: "One turn of the call transcript",
  props: z.object({ speaker: z.enum(["agent", "business"]), text: z.string() }),
  component: ({ props }) => {
    const agent = props.speaker === "agent";
    return (
      <div className={cn("flex max-w-[85%] items-end gap-2", agent ? "flex-row-reverse self-end" : "self-start")}>
        {agent ? <Logo className="size-6" /> : (
          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-muted text-muted-foreground"><Store className="size-3" /></span>
        )}
        <div className={cn("flex flex-col", agent && "items-end")}>
          <span className="mx-2 mb-0.5 text-[11px] text-muted-foreground">{agent ? "Jamie" : "Business"}</span>
          <div
            className={cn(
              "rounded-[18px] px-3.5 py-2 text-[15px] leading-snug [overflow-wrap:anywhere]",
              agent ? "rounded-br-[6px] bg-bubble-agent text-white" : "rounded-bl-[6px] bg-muted",
            )}
          >
            {props.text}
          </div>
        </div>
      </div>
    );
  },
});

export const Section = defineComponent({
  name: "Section",
  description: "Titled card holding key values, a timeline or a transcript",
  props: z.object({
    title: z.string(),
    items: z.array(z.union([KeyValue.ref, TimelineItem.ref, TranscriptLine.ref])),
  }),
  component: ({ props, renderNode }) => {
    const kind = props.items[0]?.typeName;
    const body = renderNode(props.items);
    return (
      <section className="rounded-3xl border bg-card p-5 shadow-soft sm:p-6">
        <h2 className="mb-4 text-[15px] font-semibold tracking-tight">{props.title}</h2>
        {kind === "TimelineItem" ? <ol className="m-0 list-none p-0">{body}</ol>
          : kind === "KeyValue" ? <dl className="m-0 divide-y">{body}</dl>
          : <div className="flex flex-col gap-3">{body}</div>}
      </section>
    );
  },
});

export const Page = defineComponent({
  name: "Page",
  description: "A dashboard screen",
  props: z.object({
    title: z.string(),
    subtitle: z.string().optional(),
    children: z.array(z.union([
      StatRow.ref, CallList.ref, Empty.ref, BackLink.ref, CallHeader.ref, PendingQuestion.ref, Note.ref, Section.ref,
    ])),
  }),
  component: ({ props, renderNode }) => {
    // dashboard.py sends an empty title when a header card already names the screen.
    const kids = renderNode(props.children);
    return (
      <main className="flex flex-col gap-5">
        {props.title && (
          <div className="rise">
            <h1 className="text-[34px] font-semibold leading-tight tracking-[-0.03em]">{props.title}</h1>
            {props.subtitle && <p className="text-muted-foreground">{props.subtitle}</p>}
          </div>
        )}
        {(Array.isArray(kids) ? kids : [kids]).map((k, i) => (
          <div key={i} className="rise flex flex-col" style={{ animationDelay: `${60 + i * 70}ms` }}>{k}</div>
        ))}
      </main>
    );
  },
});

export const library = createLibrary({
  components: [
    Page, StatRow, Stat, CallList, CallRow, Empty, BackLink, CallHeader, PendingQuestion, Note, Section, KeyValue,
    TimelineItem, TranscriptLine,
  ],
  root: "Page",
});
