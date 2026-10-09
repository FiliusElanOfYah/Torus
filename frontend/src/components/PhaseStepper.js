import { Link } from "react-router-dom";
import { Lock, Loader, CheckCircle2 } from "lucide-react";

export default function PhaseStepper({ phases, progress, currentNumber = null }) {
  // progress.phases: [{number, name, total, verified, complete}]
  const map = new Map((progress?.phases || []).map((p) => [p.number, p]));

  const stateFor = (num, idx) => {
    const p = map.get(num);
    if (!p) return "locked";
    if (p.complete) return "complete";
    // active if previous phase is complete OR phase 0
    if (num === 0) return "active";
    const prev = map.get(num - 1);
    return prev?.complete ? "active" : "locked";
  };

  return (
    <ol
      className="grid grid-cols-2 sm:grid-cols-5 lg:grid-cols-10 gap-2 reveal-stagger"
      data-testid="phase-stepper"
    >
      {phases.map((p, idx) => {
        const state = stateFor(p.number, idx);
        const prog = map.get(p.number);
        const isCurrent = currentNumber === p.number;
        const borderColor =
          state === "complete"
            ? "var(--accent-emerald)"
            : state === "active"
            ? "var(--accent-amber)"
            : "var(--border-dim)";
        return (
          <li
            key={p.number}
            className="reveal"
            data-testid={`phase-stepper-item-${p.number}`}
          >
            <Link
              to={`/phase/${p.number}`}
              className="block border-2 p-3 h-full transition-all duration-150 hover:translate-y-[-1px]"
              style={{
                borderColor: isCurrent ? "var(--accent-amber)" : borderColor,
                background: isCurrent ? "var(--bg-card-hover)" : "var(--bg-card)",
                boxShadow: isCurrent ? "0 0 0 1px var(--accent-amber-dim)" : "none",
              }}
            >
              <div className="flex items-center justify-between mb-2">
                <span
                  className="font-display text-[10px] uppercase tracking-widest font-bold"
                  style={{ color: "var(--text-dim)" }}
                >
                  PHASE {String(p.number).padStart(2, "0")}
                </span>
                {state === "complete" ? (
                  <CheckCircle2 size={12} style={{ color: "var(--accent-emerald)" }} />
                ) : state === "active" ? (
                  <Loader size={12} style={{ color: "var(--accent-amber)" }} />
                ) : (
                  <Lock size={12} style={{ color: "var(--text-dim)" }} />
                )}
              </div>
              <div
                className="text-xs font-medium leading-snug"
                style={{
                  color:
                    state === "locked"
                      ? "var(--text-dim)"
                      : "var(--text-primary)",
                }}
              >
                {p.name}
              </div>
              <div
                className="mt-2 font-tactical text-[10px] uppercase tracking-widest"
                style={{
                  color:
                    state === "complete"
                      ? "var(--accent-emerald)"
                      : "var(--text-dim)",
                }}
              >
                {prog ? `${prog.verified}/${prog.total} verified` : "— locked —"}
              </div>
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
