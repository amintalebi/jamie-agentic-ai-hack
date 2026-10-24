// OpenUI component library for the dashboard.
// The server (server/dashboard.py) writes screens in OpenUI Lang using these components.
// Arguments are positional, in the order of each schema's keys: keep both sides in sync.
import { createLibrary, defineComponent } from "@openuidev/react-lang";
import { z } from "zod/v4";
import { duration, statusLook, when, type Tone } from "./format";

const tone = z.enum(["neutral", "live", "good", "bad", "warn"]);

function Badge({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span className={`badge tone-${tone}`}>
      {tone === "live" && <span className="pulse" aria-hidden />}
      {label}
    </span>
  );
}

export const Stat = defineComponent({
  name: "Stat",
  description: "One headline number",
  props: z.object({ label: z.string(), value: z.string(), tone: tone }),
  component: ({ props }) => (
    <div className={`stat tone-${props.tone}`}>
      <div className="stat-value">{props.value}</div>
      <div className="stat-label">{props.label}</div>
    </div>
  ),
});

export const StatRow = defineComponent({
  name: "StatRow",
  description: "A row of headline numbers",
  props: z.object({ stats: z.array(Stat.ref) }),
  component: ({ props, renderNode }) => <div className="stat-row">{renderNode(props.stats)}</div>,
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
    return (
      <a className="call-row" href={`#/calls/${encodeURIComponent(props.callId)}`}>
        <div className="call-row-main">
          <div className="call-row-title">{props.title}</div>
          <div className="muted">{props.subtitle}</div>
        </div>
        <div className="call-row-side">
          <Badge {...look} />
          <div className="muted small">
            {when(props.createdAt)} · {duration(props.durationS)}
          </div>
        </div>
      </a>
    );
  },
});

export const CallList = defineComponent({
  name: "CallList",
  description: "The list of calls, newest first",
  props: z.object({ rows: z.array(CallRow.ref) }),
  component: ({ props, renderNode }) => <div className="card list">{renderNode(props.rows)}</div>,
});

export const Empty = defineComponent({
  name: "Empty",
  description: "Empty state",
  props: z.object({ text: z.string() }),
  component: ({ props }) => <div className="card empty">{props.text}</div>,
});

export const BackLink = defineComponent({
  name: "BackLink",
  description: "Link back to a screen",
  props: z.object({ label: z.string(), href: z.string() }),
  component: ({ props }) => (
    <a className="back" href={props.href}>
      ← {props.label}
    </a>
  ),
});

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
    const live = look.tone === "live" || props.status === "waiting_user";
    const pct = Math.min(100, Math.round(((props.durationS ?? 0) / Math.max(props.maxS, 1)) * 100));
    return (
      <div className="card header">
        <div className="header-top">
          <Badge {...look} />
          <span className="muted small">
            {props.language.toUpperCase()} · started {when(props.createdAt)}
          </span>
        </div>
        <h2>{props.title}</h2>
        <div className="muted">{props.subtitle}</div>
        <div className="timer">
          <span>{duration(props.durationS)}</span>
          {live && (
            <div className="bar" aria-label={`${pct}% of the time limit`}>
              <div style={{ width: `${pct}%` }} />
            </div>
          )}
          {live && <span className="muted small">limit {duration(props.maxS)}</span>}
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
    <div className="card pending">
      <div className="pending-label">On hold, waiting for your answer in iMessage</div>
      <div className="pending-q">{props.question}</div>
      <div className="chips">
        {props.options.map((o) => (
          <span key={o} className="chip">{o}</span>
        ))}
      </div>
    </div>
  ),
});

export const Note = defineComponent({
  name: "Note",
  description: "Highlighted summary line",
  props: z.object({ text: z.string() }),
  component: ({ props }) => <div className="card note">{props.text}</div>,
});

export const KeyValue = defineComponent({
  name: "KeyValue",
  description: "Label and value",
  props: z.object({ label: z.string(), value: z.string() }),
  component: ({ props }) => (
    <div className="kv">
      <dt>{props.label}</dt>
      <dd>{props.value}</dd>
    </div>
  ),
});

export const TimelineItem = defineComponent({
  name: "TimelineItem",
  description: "One event in a call's timeline",
  props: z.object({ kind: z.string(), label: z.string(), text: z.string(), at: z.string().optional() }),
  component: ({ props }) => (
    <li className={`tl tl-${props.kind}`}>
      <span className="tl-dot" aria-hidden />
      <div>
        <div className="tl-head">
          <strong>{props.label}</strong> <span className="muted small">{when(props.at)}</span>
        </div>
        <div>{props.text}</div>
      </div>
    </li>
  ),
});

export const TranscriptLine = defineComponent({
  name: "TranscriptLine",
  description: "One turn of the call transcript",
  props: z.object({ speaker: z.enum(["agent", "business"]), text: z.string() }),
  component: ({ props }) => (
    <div className={`line line-${props.speaker}`}>
      <div className="line-who">{props.speaker === "agent" ? "Jamie" : "Business"}</div>
      <div className="bubble">{props.text}</div>
    </div>
  ),
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
      <section className="card section">
        <h3>{props.title}</h3>
        {kind === "TimelineItem" ? <ol className="timeline">{body}</ol>
          : kind === "KeyValue" ? <dl className="kvs">{body}</dl>
          : <div className="transcript">{body}</div>}
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
  component: ({ props, renderNode }) => (
    <main className="page">
      <div className="page-head">
        <h1>{props.title}</h1>
        {props.subtitle && <span className="muted">{props.subtitle}</span>}
      </div>
      {renderNode(props.children)}
    </main>
  ),
});

export const library = createLibrary({
  components: [
    Page, StatRow, Stat, CallList, CallRow, Empty, BackLink, CallHeader, PendingQuestion, Note, Section, KeyValue,
    TimelineItem, TranscriptLine,
  ],
  root: "Page",
});
