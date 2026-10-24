import { CircleAlert, Loader2, Mic, Square, Trash2, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, AuthError, deleteVoice, fetchVoice, uploadVoice, type VoiceInfo } from "@/api";
import { Button } from "@/components/ui/button";

const MIN_RECORD_S = 10;

type Props = {
  open: boolean;
  onClose: () => void;
  onAuthError: () => void;
};

export function VoiceSettings({ open, onClose, onAuthError }: Props) {
  const [voice, setVoice] = useState<VoiceInfo | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | "delete" | null>("load");
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(false);
  const [pending, setPending] = useState<File | null>(null);
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [previewUrl, setPreviewUrl] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stream = useRef<MediaStream | null>(null);
  const tick = useRef<number>(0);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setBusy("load");
    setError("");
    setConsent(false);
    setPending(null);
    fetchVoice()
      .then((info) => {
        if (!cancelled) setVoice(info);
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof AuthError) onAuthError();
        else setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setBusy(null);
      });
    return () => {
      cancelled = true;
      stopMic();
    };
  }, [open]);

  useEffect(() => {
    if (!pending) {
      setPreviewUrl("");
      return;
    }
    const url = URL.createObjectURL(pending);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [pending]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !recording) onClose();
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open, onClose, recording]);

  function stopMic() {
    recorder.current?.state === "recording" && recorder.current.stop();
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    window.clearInterval(tick.current);
    setRecording(false);
  }

  async function startRecording() {
    setError("");
    setPending(null);
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = media;
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported(t));
      const rec = new MediaRecorder(media, mime ? { mimeType: mime } : undefined);
      chunks.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size) chunks.current.push(e.data);
      };
      rec.onstop = () => {
        const type = rec.mimeType || "audio/webm";
        const blob = new Blob(chunks.current, { type });
        const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        setPending(new File([blob], `sample.${ext}`, { type }));
        media.getTracks().forEach((t) => t.stop());
      };
      recorder.current = rec;
      rec.start();
      setElapsed(0);
      setRecording(true);
      tick.current = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    } catch {
      setError("Microphone access was denied. Upload a clip instead.");
    }
  }

  function stopRecording() {
    window.clearInterval(tick.current);
    recorder.current?.stop();
    setRecording(false);
  }

  async function save() {
    if (!pending || !consent) return;
    if (recording) return;
    if (pending.size < 8 * 1024) {
      setError("That clip is too short. Record about 30 seconds of clear speech.");
      return;
    }
    setBusy("save");
    setError("");
    try {
      const info = await uploadVoice(pending);
      setVoice({ ...info, configured: true });
      setPending(null);
      setConsent(false);
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    setError("");
    try {
      await deleteVoice();
      setVoice((v) => (v ? { ...v, voice_id: null, voice_name: null } : v));
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
      else setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!open) return null;

  const recordedTooShort = Boolean(pending) && elapsed > 0 && elapsed < MIN_RECORD_S;
  const canSave = Boolean(pending) && consent && !busy && !recording && !recordedTooShort && pending!.size >= 8 * 1024;
  const usingClone = Boolean(voice?.voice_id);

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-6" onClick={() => !recording && onClose()}>
      <div
        role="dialog"
        aria-labelledby="voice-title"
        className="rise glass w-full max-w-md rounded-t-3xl border bg-card p-6 shadow-lift sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id="voice-title" className="text-lg font-semibold tracking-tight">Your call voice</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Jamie speaks as you on the phone. Clone a short sample and every call uses it.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="grid size-8 place-items-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        {busy === "load" && (
          <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading voice
          </div>
        )}

        {voice && !voice.configured && (
          <p className="mt-5 rounded-2xl border bg-warn-soft px-4 py-3 text-sm text-warn">
            Voice cloning is not configured on this server yet.
          </p>
        )}

        {voice?.configured && (
          <div className="mt-5 space-y-4">
            <div className="rounded-2xl border bg-secondary/50 px-4 py-3">
              <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Now on calls</div>
              <div className="mt-1 font-medium">{usingClone ? voice.voice_name || "Your clone" : "Jamie's default voice"}</div>
              <div className="mt-0.5 text-sm text-muted-foreground">
                {usingClone ? "Your clone is used the next time Jamie phones someone." : "Upload a sample to replace the default."}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {recording ? (
                <Button type="button" variant="destructive" className="rounded-full" onClick={stopRecording}>
                  <Square className="size-3.5 fill-current" /> Stop {elapsed}s
                </Button>
              ) : (
                <Button type="button" variant="outline" className="rounded-full" onClick={startRecording} disabled={Boolean(busy)}>
                  <Mic /> Record
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                className="rounded-full"
                disabled={Boolean(busy) || recording}
                onClick={() => fileRef.current?.click()}
              >
                <Upload /> Upload clip
              </Button>
              {usingClone && (
                <Button
                  type="button"
                  variant="ghost"
                  className="rounded-full text-bad hover:text-bad"
                  disabled={Boolean(busy) || recording}
                  onClick={remove}
                >
                  {busy === "delete" ? <Loader2 className="animate-spin" /> : <Trash2 />}
                  Remove
                </Button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="audio/*,.m4a,.mp3,.wav,.webm,.ogg,.aac"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) return;
                  setPending(file);
                  setElapsed(0);
                  setError("");
                }}
              />
            </div>

            {recording && (
              <p className="text-sm text-muted-foreground">
                Keep talking. About 30 seconds of clear speech works best
                {elapsed < MIN_RECORD_S ? ` (${MIN_RECORD_S - elapsed}s more)` : ""}.
              </p>
            )}

            {pending && !recording && (
              <div className="space-y-3 rounded-2xl border px-4 py-3">
                <div className="text-sm font-medium">{pending.name}</div>
                <audio controls src={previewUrl} className="w-full" />
                <label className="flex items-start gap-2 text-sm text-muted-foreground">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 accent-[var(--primary)]"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                  />
                  I have the right to clone this voice and want Jamie to use it on calls.
                </label>
                <Button type="button" className="bg-brand rounded-full text-white" disabled={!canSave} onClick={save}>
                  {busy === "save" && <Loader2 className="animate-spin" />}
                  {busy === "save" ? "Cloning" : usingClone ? "Replace voice" : "Use this voice"}
                </Button>
              </div>
            )}
          </div>
        )}

        {error && (
          <p role="alert" className="mt-4 flex items-center gap-1.5 text-sm text-bad">
            <CircleAlert className="size-4 shrink-0" /> {error}
          </p>
        )}
      </div>
    </div>
  );
}
