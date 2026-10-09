import { useEffect, useState } from "react";
import Shell from "../components/Shell";
import { ensureSession, getPhase, getProgress, toggleOpsec } from "../lib/api";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

export default function Opsec() {
  const [items, setItems] = useState([]);
  const [session, setSession] = useState(null);
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    (async () => {
      const s = await ensureSession();
      const p = await getPhase(8);
      const pr = await getProgress();
      setSession(s);
      setItems(p.opsec_checklist || []);
      setProgress(pr);
    })();
  }, []);

  const checked = new Set(session?.opsec_checked || []);
  const total = items.length;
  const done = items.filter((i) => checked.has(i.id)).length;
  const pct = total ? Math.round(100 * done / total) : 0;

  const toggle = async (id, v) => {
    setSession((s) => ({
      ...s,
      opsec_checked: v
        ? [...(s?.opsec_checked || []), id]
        : (s?.opsec_checked || []).filter((x) => x !== id),
    }));
    try {
      const r = await toggleOpsec(id, v);
      setSession(r);
      if (v) toast.success("OPSEC item locked", { duration: 900 });
    } catch {
      toast.error("Save failed");
    }
  };

  return (
    <Shell overallPercent={progress?.overall?.percent || 0}>
      <div className="px-6 py-8 max-w-4xl mx-auto">
        <header className="mb-8 reveal">
          <div
            className="font-tactical text-[10px] uppercase tracking-[0.3em]"
            style={{ color: "var(--status-warning)" }}
          >
            Phase 08 · Hardening Audit
          </div>
          <h1
            className="font-display text-3xl sm:text-4xl font-bold tracking-tight uppercase mt-2"
            style={{ color: "var(--text-primary)" }}
          >
            OPSEC Audit
          </h1>
          <p
            className="mt-4 max-w-2xl text-sm leading-relaxed"
            style={{ color: "var(--text-secondary)" }}
          >
            Ten preconditions for a trustable mesh. Walk every item in order. If
            an item fails, fix it before Phase 9 — the export tarball will carry
            forward whatever posture is current.
          </p>

          <div className="mt-6 flex items-center gap-4" data-testid="opsec-progress">
            {pct === 100 ? (
              <ShieldCheck size={20} style={{ color: "var(--accent-emerald)" }} />
            ) : (
              <ShieldAlert size={20} style={{ color: "var(--status-warning)" }} />
            )}
            <div className="flex-1">
              <div className="flex items-end justify-between mb-1 font-tactical text-[10px] uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>
                <span>Audit status</span>
                <span style={{ color: pct === 100 ? "var(--accent-emerald)" : "var(--status-warning)" }}>
                  {done}/{total} · {pct}%
                </span>
              </div>
              <div className="h-1.5 w-full border border-dim" style={{ background: "var(--bg-command)" }}>
                <div
                  className="h-full transition-all duration-300"
                  style={{
                    width: `${pct}%`,
                    background: pct === 100 ? "var(--accent-emerald)" : "var(--status-warning)",
                  }}
                />
              </div>
            </div>
          </div>
        </header>

        <ol className="space-y-2 reveal-stagger" data-testid="opsec-list">
          {items.map((item, i) => {
            const on = checked.has(item.id);
            return (
              <li key={item.id} className="reveal">
                <label
                  className="flex items-start gap-4 cursor-pointer p-4 border transition-all"
                  style={{
                    borderColor: on ? "var(--accent-emerald)" : "var(--border-dim)",
                    background: on ? "rgba(16,185,129,0.05)" : "var(--bg-card)",
                    borderLeft: on
                      ? "3px solid var(--accent-emerald)"
                      : "3px solid var(--status-warning)",
                  }}
                  data-testid={`opsec-audit-item-${item.id}`}
                >
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={on}
                    onChange={(e) => toggle(item.id, e.target.checked)}
                    data-testid={`opsec-checkbox-${item.id}`}
                  />
                  <span
                    className="w-5 h-5 flex items-center justify-center border mt-0.5 shrink-0"
                    style={{
                      borderColor: on ? "var(--accent-emerald)" : "var(--border-bright)",
                      background: on ? "var(--accent-emerald)" : "transparent",
                    }}
                  >
                    {on && (
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          background: "var(--bg-primary)",
                        }}
                      />
                    )}
                  </span>
                  <div className="min-w-0">
                    <div
                      className="font-tactical text-[10px] uppercase tracking-widest"
                      style={{ color: "var(--text-dim)" }}
                    >
                      {item.id.toUpperCase()} · item {i + 1}
                    </div>
                    <div
                      className="mt-1 text-sm leading-relaxed"
                      style={{
                        color: on ? "var(--accent-emerald)" : "var(--text-primary)",
                      }}
                    >
                      {item.label}
                    </div>
                  </div>
                </label>
              </li>
            );
          })}
        </ol>

        {pct === 100 && (
          <div
            className="mt-8 border p-5 reveal"
            style={{
              borderColor: "var(--accent-emerald)",
              background: "var(--bg-card)",
            }}
            data-testid="opsec-passed"
          >
            <h2
              className="font-display text-sm uppercase tracking-widest font-semibold"
              style={{ color: "var(--accent-emerald)" }}
            >
              Audit passed · ready for Phase 9 export
            </h2>
            <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
              Every precondition for a trustable mesh is now green. Proceed to
              Phase 9 to tarball the full stack and `adb pull` it to the laptop.
            </p>
          </div>
        )}
      </div>
    </Shell>
  );
}
