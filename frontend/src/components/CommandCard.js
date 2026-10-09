import { useEffect, useRef, useState } from "react";
import {
  Check,
  Copy,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  NotebookPen,
  X,
  Save,
} from "lucide-react";
import { toast } from "sonner";
import { saveNote } from "../lib/api";

export default function CommandCard({ step, index, verified, note = "", onVerify, onNoteSaved }) {
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState({ expected: false, trouble: false });
  const [drawer, setDrawer] = useState(false);
  const [draft, setDraft] = useState(note || "");
  const [saving, setSaving] = useState(false);
  const timer = useRef(null);

  // Keep draft in sync when parent note prop refreshes (e.g. after import)
  useEffect(() => {
    setDraft(note || "");
  }, [note]);

  const persist = async (text) => {
    setSaving(true);
    try {
      const r = await saveNote(step.id, text);
      onNoteSaved?.(r);
    } catch {
      toast.error("Note save failed");
    } finally {
      setSaving(false);
    }
  };

  const onDraftChange = (v) => {
    setDraft(v);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => persist(v), 700);
  };

  const copyCmd = async () => {
    try {
      await navigator.clipboard.writeText(step.command);
      setCopied(true);
      toast.success(`Step ${step.id} copied`, { duration: 1200 });
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("Clipboard blocked by browser");
    }
  };

  const hasNote = (note || "").trim().length > 0;

  return (
    <article
      className="border border-dim reveal relative"
      style={{
        background: "var(--bg-card)",
        borderLeft: verified
          ? "3px solid var(--accent-emerald)"
          : "3px solid var(--border-dim)",
      }}
      data-testid={`command-card-${step.id}`}
    >
      {/* Header */}
      <header className="flex items-start justify-between gap-4 px-5 py-4 border-b border-dim">
        <div className="flex items-start gap-3 min-w-0">
          <div
            className="font-display text-sm font-bold px-2 py-1 border border-dim shrink-0"
            style={{
              color: "var(--accent-amber)",
              background: "var(--bg-command)",
              letterSpacing: "0.08em",
            }}
            data-testid={`step-id-${step.id}`}
          >
            STEP {step.id}
          </div>
          <div className="min-w-0">
            <h3
              className="text-base font-medium leading-snug break-words"
              style={{ color: "var(--text-primary)" }}
            >
              {step.title}
            </h3>
            <p className="text-xs mt-1 font-tactical uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
              Expected: <span style={{ color: "var(--text-secondary)", textTransform: "none" }}>{step.expected}</span>
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={() => setDrawer(true)}
            data-testid={`notes-btn-${step.id}`}
            className="flex items-center gap-2 px-3 py-1.5 border font-tactical text-[10px] uppercase tracking-widest transition-colors"
            style={{
              borderColor: hasNote ? "var(--accent-amber)" : "var(--border-dim)",
              color: hasNote ? "var(--accent-amber)" : "var(--text-secondary)",
              background: "var(--bg-command)",
            }}
          >
            <NotebookPen size={12} />
            <span>{hasNote ? "Notes •" : "Notes"}</span>
          </button>
          <button
            type="button"
            onClick={copyCmd}
            data-testid={`copy-btn-${step.id}`}
            className="flex items-center gap-2 px-3 py-1.5 border font-tactical text-[10px] uppercase tracking-widest transition-all duration-150"
            style={{
              borderColor: copied ? "var(--accent-emerald)" : "var(--border-dim)",
              color: copied ? "var(--accent-emerald)" : "var(--text-secondary)",
              background: copied ? "rgba(16, 185, 129, 0.08)" : "var(--bg-command)",
            }}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            <span>{copied ? "Copied" : "Copy"}</span>
          </button>
        </div>
      </header>

      {/* Command block */}
      <pre className="command-block" data-testid={`cmd-block-${step.id}`}>
        <code>{step.command}</code>
      </pre>

      {/* Note preview */}
      {hasNote && (
        <div
          className="mx-5 mt-3 border-l-2 pl-3 py-2 font-code text-xs leading-relaxed whitespace-pre-wrap"
          style={{
            borderColor: "var(--accent-amber)",
            color: "var(--text-secondary)",
            background: "rgba(255,176,0,0.03)",
          }}
          data-testid={`note-preview-${step.id}`}
        >
          <span
            className="font-tactical text-[10px] uppercase tracking-widest block mb-1"
            style={{ color: "var(--accent-amber)" }}
          >
            Captured output
          </span>
          {note.length > 240 ? note.slice(0, 240) + "…" : note}
        </div>
      )}

      {/* Verify + Expected / Troubleshooting */}
      <div className="px-5 py-4 flex flex-col gap-3">
        <button
          type="button"
          className="flex items-center justify-between text-left font-tactical text-xs uppercase tracking-widest hover:text-amber-glow"
          onClick={() => setOpen((o) => ({ ...o, expected: !o.expected }))}
          style={{ color: "var(--text-secondary)" }}
          data-testid={`toggle-verify-${step.id}`}
        >
          <span className="flex items-center gap-2">
            {open.expected ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Verification probe
          </span>
          <span style={{ color: "var(--text-dim)" }}>exit 0 → unlock next</span>
        </button>
        {open.expected && (
          <pre
            className="command-block"
            style={{ color: "var(--accent-emerald)" }}
            data-testid={`verify-block-${step.id}`}
          >
            <code>{step.verify}</code>
          </pre>
        )}

        {step.troubleshooting && (
          <>
            <button
              type="button"
              className="flex items-center gap-2 text-left font-tactical text-xs uppercase tracking-widest hover:text-amber-glow"
              onClick={() => setOpen((o) => ({ ...o, trouble: !o.trouble }))}
              style={{ color: "var(--text-secondary)" }}
              data-testid={`toggle-trouble-${step.id}`}
            >
              {open.trouble ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              <AlertTriangle size={12} style={{ color: "var(--status-warning)" }} />
              <span>If it fails…</span>
            </button>
            {open.trouble && (
              <div
                className="text-xs leading-relaxed border-l-2 pl-3"
                style={{
                  color: "var(--text-secondary)",
                  borderColor: "var(--status-warning)",
                }}
                data-testid={`trouble-block-${step.id}`}
              >
                {step.troubleshooting}
              </div>
            )}
          </>
        )}

        {/* Verification gate */}
        <div
          className="mt-2 flex items-center justify-between border-t border-dim pt-3"
          data-testid={`verify-gate-${step.id}`}
        >
          <label
            className="flex items-center gap-3 cursor-pointer select-none"
            data-testid={`verify-label-${step.id}`}
          >
            <input
              type="checkbox"
              className="sr-only"
              checked={verified}
              onChange={(e) => onVerify(step.id, e.target.checked)}
              data-testid={`verify-checkbox-${step.id}`}
            />
            <span
              className="w-5 h-5 flex items-center justify-center border transition-all"
              style={{
                borderColor: verified ? "var(--accent-emerald)" : "var(--border-bright)",
                background: verified ? "rgba(16, 185, 129, 0.15)" : "var(--bg-command)",
              }}
            >
              {verified && <CircleCheck size={14} style={{ color: "var(--accent-emerald)" }} />}
            </span>
            <span
              className="font-tactical text-xs uppercase tracking-widest"
              style={{ color: verified ? "var(--accent-emerald)" : "var(--text-secondary)" }}
            >
              {verified ? "Verified · gate passed" : "Mark exit-code 0"}
            </span>
          </label>
          <span
            className="font-tactical text-[10px] uppercase tracking-widest"
            style={{ color: "var(--text-dim)" }}
          >
            #{index + 1}
          </span>
        </div>
      </div>

      {/* Notes Drawer (right side) */}
      {drawer && (
        <div
          className="fixed inset-0 z-50 flex"
          data-testid={`notes-drawer-${step.id}`}
        >
          <button
            type="button"
            onClick={() => setDrawer(false)}
            aria-label="close drawer"
            className="flex-1 bg-black/70"
            data-testid={`notes-drawer-backdrop-${step.id}`}
          />
          <aside
            className="w-full sm:max-w-md flex flex-col border-l-2"
            style={{
              background: "var(--bg-secondary)",
              borderColor: "var(--accent-amber)",
              animation: "torus-reveal 0.3s ease-out",
            }}
          >
            <header
              className="flex items-start justify-between gap-4 px-5 py-4 border-b border-dim"
              style={{ background: "var(--bg-card)" }}
            >
              <div className="min-w-0">
                <div
                  className="font-tactical text-[10px] uppercase tracking-widest"
                  style={{ color: "var(--accent-amber)" }}
                >
                  Step {step.id} · Captured Output
                </div>
                <h3
                  className="mt-1 text-base font-medium break-words"
                  style={{ color: "var(--text-primary)" }}
                >
                  {step.title}
                </h3>
                <p
                  className="mt-2 text-xs"
                  style={{ color: "var(--text-dim)" }}
                >
                  Paste the actual terminal output here. Saved automatically; shows up on this card and travels in your signed session manifest.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setDrawer(false)}
                data-testid={`notes-close-${step.id}`}
                className="shrink-0 border px-2 py-2"
                style={{
                  borderColor: "var(--border-bright)",
                  color: "var(--text-secondary)",
                  background: "var(--bg-command)",
                }}
                aria-label="close"
              >
                <X size={14} />
              </button>
            </header>

            <div className="px-5 py-4 flex-1 flex flex-col gap-3 min-h-0">
              <div
                className="font-tactical text-[10px] uppercase tracking-widest"
                style={{ color: "var(--text-dim)" }}
              >
                Command (reference)
              </div>
              <pre
                className="command-block max-h-28 overflow-auto"
                data-testid={`notes-cmd-reference-${step.id}`}
              >
                <code>{step.command}</code>
              </pre>

              <div
                className="flex items-center justify-between font-tactical text-[10px] uppercase tracking-widest"
                style={{ color: "var(--text-dim)" }}
              >
                <span>Terminal output</span>
                <span style={{ color: saving ? "var(--accent-amber)" : "var(--text-dim)" }}>
                  {saving ? "writing…" : "autosaved"}
                </span>
              </div>
              <textarea
                value={draft}
                onChange={(e) => onDraftChange(e.target.value)}
                placeholder={`$ ${step.command.split("\n")[0].slice(0, 60)}\n…paste the real output here…`}
                className="flex-1 min-h-[260px] px-3 py-3 font-code text-xs leading-relaxed"
                style={{
                  background: "var(--bg-command)",
                  border: "1px solid var(--border-dim)",
                  color: "var(--text-amber)",
                  resize: "vertical",
                }}
                data-testid={`notes-textarea-${step.id}`}
              />

              <div className="flex items-center justify-between pt-2">
                <button
                  type="button"
                  onClick={() => {
                    if (timer.current) clearTimeout(timer.current);
                    persist(draft);
                  }}
                  disabled={saving}
                  data-testid={`notes-save-${step.id}`}
                  className="flex items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest"
                  style={{
                    borderColor: "var(--accent-amber)",
                    color: "var(--accent-amber)",
                    background: "var(--bg-command)",
                  }}
                >
                  <Save size={12} />
                  <span>Flush now</span>
                </button>
                <span
                  className="font-tactical text-[10px] uppercase tracking-widest"
                  style={{ color: "var(--text-dim)" }}
                >
                  {draft.length} chars
                </span>
              </div>
            </div>
          </aside>
        </div>
      )}
    </article>
  );
}
