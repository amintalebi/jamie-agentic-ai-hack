import { cn } from "@/lib/utils";

/** Jamie's mark: a chat bubble holding a sound wave, on the brand gradient. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("bg-brand inline-grid size-8 place-items-center rounded-[30%] shadow-soft", className)}>
      <svg viewBox="0 0 24 24" className="size-[62%]" fill="none" aria-hidden>
        <path
          d="M12 3.5c-4.97 0-9 3.36-9 7.5 0 2.2 1.13 4.17 2.94 5.54L5.2 20.2a.5.5 0 0 0 .7.56l3.83-1.83c.73.15 1.49.23 2.27.23 4.97 0 9-3.36 9-7.5S16.97 3.5 12 3.5Z"
          fill="white"
        />
        <g stroke="url(#jw)" strokeWidth="1.9" strokeLinecap="round">
          <path d="M8 10v2" />
          <path d="M10.7 8.6v4.8" />
          <path d="M13.4 9.4v3.2" />
          <path d="M16 10.3v1.4" />
        </g>
        <defs>
          <linearGradient id="jw" x1="7" y1="8" x2="17" y2="14" gradientUnits="userSpaceOnUse">
            <stop stopColor="var(--brand-1)" />
            <stop offset="1" stopColor="var(--brand-3)" />
          </linearGradient>
        </defs>
      </svg>
    </span>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5 text-[17px] font-semibold tracking-tight", className)}>
      <Logo />
      Jamie
    </span>
  );
}

/** Soft moving colour blobs behind the page. Purely decorative. */
export function Aurora({ className, intensity = 1 }: { className?: string; intensity?: number }) {
  const blobs = [
    { c: "var(--brand-1)", s: "left-[-10%] top-[-15%] size-[55vmax]", d: "0s" },
    { c: "var(--brand-2)", s: "right-[-15%] top-[10%] size-[45vmax]", d: "-6s" },
    { c: "var(--brand-3)", s: "left-[20%] bottom-[-25%] size-[50vmax]", d: "-12s" },
  ];
  return (
    <div aria-hidden className={cn("pointer-events-none fixed inset-0 -z-10 overflow-hidden", className)}>
      {blobs.map((b) => (
        <div
          key={b.d}
          className={cn("aurora-blob absolute rounded-full blur-[90px]", b.s)}
          style={{
            background: `radial-gradient(circle at center, ${b.c}, transparent 65%)`,
            opacity: 0.22 * intensity,
            animationDelay: b.d,
          }}
        />
      ))}
      {/* fine grain so the gradients do not band */}
      <div
        className="absolute inset-0 opacity-[0.035] mix-blend-overlay"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        }}
      />
    </div>
  );
}

/** Animated voice bars. `active=false` freezes them at rest heights. */
export function Waveform({
  bars = 5,
  active = true,
  className,
  barClassName,
}: {
  bars?: number;
  active?: boolean;
  className?: string;
  barClassName?: string;
}) {
  return (
    <span aria-hidden className={cn("inline-flex h-4 items-center gap-[3px]", className)}>
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          className={cn("h-full w-[3px] rounded-full bg-current", active && "wave-bar", barClassName)}
          style={{
            animationDelay: `${(i * 137) % 600}ms`,
            animationDuration: `${800 + ((i * 211) % 500)}ms`,
            transform: active ? undefined : `scaleY(${0.35 + ((i * 37) % 60) / 100})`,
          }}
        />
      ))}
    </span>
  );
}

/** A dot with a ripple, for anything happening right now. */
export function LiveDot({ className }: { className?: string }) {
  return (
    <span className={cn("relative inline-flex size-2", className)} aria-hidden>
      <span className="ring-out absolute inset-0 rounded-full bg-current" />
      <span className="relative inline-flex size-2 rounded-full bg-current" />
    </span>
  );
}

const CONFETTI_COLORS = ["var(--brand-1)", "var(--brand-2)", "var(--brand-3)", "var(--live)", "var(--warn)", "var(--good)"];

/** One burst of confetti from the centre of its parent (which must be `relative`). */
export function Confetti({ count = 36 }: { count?: number }) {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-visible">
      {Array.from({ length: count }, (_, i) => {
        const angle = (i / count) * Math.PI * 2 + (i % 3) * 0.3;
        const dist = 90 + ((i * 53) % 110);
        return (
          <span
            key={i}
            className="confetti-bit absolute left-1/2 top-1/3 block rounded-[2px]"
            style={
              {
                width: i % 2 ? 6 : 8,
                height: i % 2 ? 10 : 6,
                background: CONFETTI_COLORS[i % CONFETTI_COLORS.length],
                animationDelay: `${(i % 6) * 25}ms`,
                "--dx": `${Math.cos(angle) * dist}px`,
                "--dy": `${Math.sin(angle) * dist + 60}px`,
                "--rot": `${(i % 2 ? 1 : -1) * (180 + i * 20)}deg`,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}
