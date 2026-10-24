import { Check, ChevronLeft, Phone, Video } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { Logo, LiveDot, TypingDots, Waveform } from "./brand";

// The demo script, replayed in a loop on the sign-in screen. Each entry is how long to wait
// before moving to the next step (ms).
const STEPS = [700, 1500, 900, 1500, 1500, 1100, 1700, 2200, 1500, 900, 1000, 4500];
// step index -> what changed
const S = {
  voice: 1, typing1: 2, poll1: 3, vote1: 4, calling: 5, live: 6, poll2: 7, vote2: 8, typing2: 9, done: 10,
} as const;

export function PhoneDemo({ className }: { className?: string }) {
  const [step, setStep] = useState(0);
  const [cycle, setCycle] = useState(0);

  useEffect(() => {
    const id = window.setTimeout(() => {
      if (step >= STEPS.length - 1) {
        setStep(0);
        setCycle((c) => c + 1);
      } else setStep(step + 1);
    }, STEPS[step]);
    return () => clearTimeout(id);
  }, [step]);

  const at = (s: number) => step >= s;

  return (
    <div className={cn("relative mx-auto w-[300px] select-none", className)} aria-label="Example conversation with Jamie">
      {/* glow behind the device */}
      <div aria-hidden className="bg-brand absolute inset-6 -z-10 rounded-[56px] opacity-40 blur-3xl" />
      <div className="rounded-[52px] bg-neutral-900 p-[10px] shadow-lift ring-1 ring-black/10 dark:bg-neutral-800 dark:ring-white/10">
        <div className="relative flex h-[600px] flex-col overflow-hidden rounded-[42px] bg-white text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
          {/* status bar + island */}
          <div className="flex items-center justify-between px-7 pt-3 text-[12px] font-semibold">
            <span>9:41</span>
            <span className="h-[26px] w-[92px] rounded-full bg-black" />
            <span className="flex items-center gap-1">
              <span className="flex items-end gap-[2px]">
                {[4, 6, 8, 10].map((h) => <span key={h} className="w-[3px] rounded-sm bg-current" style={{ height: h }} />)}
              </span>
              <span className="ml-1 h-[11px] w-[22px] rounded-[3px] border border-current p-[1px]">
                <span className="block h-full w-[70%] rounded-[1px] bg-current" />
              </span>
            </span>
          </div>

          {/* conversation header */}
          <div className="flex items-center justify-between border-b border-black/5 px-4 pb-2.5 pt-2 dark:border-white/10">
            <ChevronLeft className="size-6 text-imessage" />
            <div className="flex flex-col items-center gap-0.5">
              <Logo className="size-9" />
              <span className="text-[11px] font-medium">Jamie</span>
            </div>
            <Video className="size-5 text-imessage" />
          </div>

          {/* messages: newest at the bottom, older ones slide up and out */}
          <div key={cycle} className="flex min-h-0 flex-1 flex-col justify-end gap-1.5 overflow-hidden [&>*]:shrink-0 px-3 pb-3 text-[13.5px] leading-snug">
            {at(S.voice) && (
              <Mine>
                <span className="flex items-center gap-2">
                  <span className="grid size-6 place-items-center rounded-full bg-white/25">
                    <span className="ml-0.5 border-y-[5px] border-l-[8px] border-y-transparent border-l-white" />
                  </span>
                  <Waveform bars={14} active={false} className="h-5" />
                  <span className="text-[11px] opacity-80">0:04</span>
                </span>
              </Mine>
            )}
            {at(S.voice) && (
              <p className="bubble-in self-end pr-1 text-[10.5px] italic text-neutral-500">
                "book a table for four tomorrow at eight at the usual Italian place"
              </p>
            )}
            {step === S.typing1 && <Typing />}
            {at(S.poll1) && <Poll question="Inside or outside?" options={["Inside", "Outside"]} picked={at(S.vote1) ? 0 : -1} />}
            {at(S.calling) && <Theirs>Calling Trattoria Da Enzo now 📞</Theirs>}
            {at(S.live) && <CallCard ended={at(S.typing2)} />}
            {at(S.poll2) && (
              <Poll question="On the call: they offer 21:00 instead of 20:00. OK?" options={["Yes", "No"]} picked={at(S.vote2) ? 0 : -1} />
            )}
            {step === S.typing2 && <Typing />}
            {at(S.done) && <Theirs>Booked ✅ Table for 4 tomorrow at 21:00, inside, under Sina. See you there!</Theirs>}
          </div>

          {/* composer */}
          <div className="flex items-center gap-2 px-3 pb-5">
            <span className="grid size-7 place-items-center rounded-full bg-neutral-200 text-lg leading-none text-neutral-500 dark:bg-neutral-800">+</span>
            <span className="flex-1 rounded-full border border-black/10 px-3 py-1.5 text-[13px] text-neutral-400 dark:border-white/15">iMessage</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Mine({ children }: { children: React.ReactNode }) {
  return (
    <div className="bubble-in max-w-[78%] self-end rounded-[18px] rounded-br-[6px] bg-imessage px-3 py-2 text-white">{children}</div>
  );
}

function Theirs({ children }: { children: React.ReactNode }) {
  return (
    <div className="bubble-in max-w-[80%] self-start rounded-[18px] rounded-bl-[6px] bg-[#e9e9eb] px-3 py-2 dark:bg-[#26252a]">
      {children}
    </div>
  );
}

function Typing() {
  return (
    <div className="bubble-in flex w-fit items-center gap-1 self-start rounded-[18px] rounded-bl-[6px] bg-[#e9e9eb] px-3.5 py-3 dark:bg-[#26252a]">
      <TypingDots className="text-neutral-500" />
    </div>
  );
}

function Poll({ question, options, picked }: { question: string; options: string[]; picked: number }) {
  return (
    <div className="bubble-in w-[82%] self-start overflow-hidden rounded-[18px] bg-[#e9e9eb] dark:bg-[#26252a]">
      <div className="px-3 pb-1.5 pt-2.5">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-neutral-500">Poll</div>
        <div className="font-semibold">{question}</div>
      </div>
      <div className="flex flex-col gap-1 p-1.5 pt-0.5">
        {options.map((o, i) => (
          <div
            key={o}
            className={cn(
              "flex items-center justify-between rounded-xl bg-white px-3 py-2 transition-all duration-300 dark:bg-neutral-900",
              picked === i && "bg-imessage text-white dark:bg-imessage",
            )}
          >
            <span className="font-medium">{o}</span>
            {picked === i ? <Check className="size-4" /> : <span className="size-4 rounded-full border border-neutral-300 dark:border-neutral-600" />}
          </div>
        ))}
      </div>
    </div>
  );
}

function CallCard({ ended }: { ended: boolean }) {
  return (
    <div className="bubble-in flex w-[82%] items-center gap-3 self-start rounded-[18px] bg-[#e9e9eb] px-3 py-2.5 dark:bg-[#26252a]">
      <span
        className={cn(
          "grid size-9 place-items-center rounded-full text-white transition-colors duration-500",
          ended ? "bg-neutral-400" : "bg-[#30d158]",
        )}
      >
        <Phone className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-semibold">Trattoria Da Enzo</div>
        <div className="flex items-center gap-1.5 text-[11px] text-neutral-500">
          {ended ? "Call ended · 1:12" : (<><LiveDot className="text-[#30d158]" /> Jamie is on the call</>)}
        </div>
      </div>
      {!ended && <Waveform bars={5} className="text-[#30d158]" />}
    </div>
  );
}
