import { useState } from "react";
import { Check, Copy, AlertTriangle, ChevronDown, ChevronRight, CircleCheck } from "lucide-react";
import { toast } from "sonner";

export default function CommandCard({ step, index, verified, onVerify }) {
  const [copied, setCopied] = useState(false);
  const [open, setOpen] = useState({ expected: false, trouble: false });

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

  return (
    <article
      className="border border-dim reveal"
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
        <button
          type="button"
          onClick={copyCmd}
          data-testid={`copy-btn-${step.id}`}
          className="shrink-0 flex items-center gap-2 px-3 py-1.5 border font-tactical text-[10px] uppercase tracking-widest transition-all duration-150"
          style={{
            borderColor: copied ? "var(--accent-emerald)" : "var(--border-dim)",
            color: copied ? "var(--accent-emerald)" : "var(--text-secondary)",
            background: copied ? "rgba(16, 185, 129, 0.08)" : "var(--bg-command)",
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </header>

      {/* Command block */}
      <pre className="command-block" data-testid={`cmd-block-${step.id}`}>
        <code>{step.command}</code>
      </pre>

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
    </article>
  );
}
