import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ensureSession,
  getPhase,
  getPhases,
  getProgress,
  phaseBundleUrl,
  toggleStep,
} from "../lib/api";
import Shell from "../components/Shell";
import CommandCard from "../components/CommandCard";
import PhaseStepper from "../components/PhaseStepper";
import { ArrowLeft, ArrowRight, Download, Target, Flag } from "lucide-react";
import { toast } from "sonner";

export default function PhaseDetail() {
  const { number } = useParams();
  const num = parseInt(number, 10);
  const navigate = useNavigate();
  const [phase, setPhase] = useState(null);
  const [phases, setPhases] = useState([]);
  const [progress, setProgress] = useState(null);
  const [session, setSession] = useState(null);

  useEffect(() => {
    (async () => {
      const [s, p, all, pr] = await Promise.all([
        ensureSession(),
        getPhase(num),
        getPhases(),
        getProgress(),
      ]);
      setSession(s);
      setPhase(p);
      setPhases(all.phases);
      setProgress(pr);
    })();
  }, [num]);

  const verifiedSet = useMemo(
    () => new Set(session?.verified_steps || []),
    [session]
  );

  const onVerify = async (stepId, v) => {
    setSession((s) => ({
      ...s,
      verified_steps: v
        ? [...(s?.verified_steps || []), stepId]
        : (s?.verified_steps || []).filter((x) => x !== stepId),
    }));
    try {
      const r = await toggleStep(stepId, v);
      setSession(r);
      setProgress(await getProgress());
      if (v) toast.success(`${stepId} gate passed`);
    } catch {
      toast.error("Save failed");
    }
  };

  if (!phase) {
    return (
      <Shell overallPercent={0}>
        <div
          className="px-6 py-8 font-tactical text-xs uppercase tracking-widest"
          style={{ color: "var(--text-dim)" }}
        >
          Loading phase…
        </div>
      </Shell>
    );
  }

  const phaseProg = progress?.phases?.find((p) => p.number === num);
  const pct = Math.round(100 * (phaseProg?.verified || 0) / (phaseProg?.total || 1));
  const allComplete = pct === 100;
  const nextNumber = num < 9 ? num + 1 : null;

  return (
    <Shell overallPercent={progress?.overall?.percent || 0}>
      <div className="px-6 py-8 max-w-5xl mx-auto">
        {/* Header */}
        <nav className="flex items-center justify-between mb-6 text-xs font-tactical uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>
          <Link
            to="/"
            className="flex items-center gap-2 hover:text-amber-glow"
            data-testid="back-to-dashboard"
          >
            <ArrowLeft size={12} /> <span>Dashboard</span>
          </Link>
          <span>
            Phase {phase.number} of 9
          </span>
        </nav>

        {/* Mini stepper */}
        <section className="mb-8 reveal">
          <PhaseStepper phases={phases} progress={progress} currentNumber={num} />
        </section>

        {/* Phase hero */}
        <header className="mb-8 reveal" data-testid="phase-header">
          <div
            className="font-tactical text-[10px] uppercase tracking-[0.3em]"
            style={{ color: "var(--accent-amber)" }}
          >
            PHASE {String(phase.number).padStart(2, "0")}
          </div>
          <h1
            className="font-display text-3xl sm:text-4xl font-bold tracking-tight uppercase mt-2"
            style={{ color: "var(--text-primary)" }}
          >
            {phase.name}
          </h1>

          <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-3">
            <div
              className="border border-dim p-4"
              style={{ background: "var(--bg-card)" }}
              data-testid="phase-goal"
            >
              <div className="flex items-center gap-2 font-tactical text-[10px] uppercase tracking-widest mb-2" style={{ color: "var(--accent-emerald)" }}>
                <Target size={12} /> <span>Goal</span>
              </div>
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-primary)" }}>
                {phase.goal}
              </p>
            </div>
            <div
              className="border border-dim p-4"
              style={{ background: "var(--bg-card)" }}
              data-testid="phase-gate"
            >
              <div className="flex items-center gap-2 font-tactical text-[10px] uppercase tracking-widest mb-2" style={{ color: "var(--accent-amber)" }}>
                <Flag size={12} /> <span>Verification Gate</span>
              </div>
              <p className="text-sm leading-relaxed" style={{ color: "var(--text-primary)" }}>
                {phase.gate}
              </p>
            </div>
          </div>

          {/* Progress bar */}
          <div className="mt-5" data-testid="phase-progress">
            <div className="flex items-end justify-between mb-2">
              <span className="font-tactical text-[10px] uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>
                Progress
              </span>
              <span className="font-tactical text-xs" style={{ color: allComplete ? "var(--accent-emerald)" : "var(--accent-amber)" }}>
                {phaseProg?.verified || 0}/{phaseProg?.total || 0} · {pct}%
              </span>
            </div>
            <div className="h-1.5 w-full border border-dim" style={{ background: "var(--bg-command)" }}>
              <div
                className="h-full transition-all duration-300"
                style={{
                  width: `${pct}%`,
                  background: allComplete ? "var(--accent-emerald)" : "var(--accent-amber)",
                }}
              />
            </div>
          </div>
        </header>

        {/* Steps */}
        <section className="space-y-4 reveal-stagger" data-testid="phase-steps">
          {phase.steps.map((step, i) => (
            <CommandCard
              key={step.id}
              step={step}
              index={i}
              verified={verifiedSet.has(step.id)}
              note={(session?.notes || {})[step.id] || ""}
              onVerify={onVerify}
              onNoteSaved={(s) => setSession(s)}
            />
          ))}
        </section>

        {/* OPSEC checklist shortcut for Phase 8 */}
        {phase.opsec_checklist && (
          <div
            className="mt-8 border p-5 reveal"
            style={{
              borderColor: "var(--status-warning)",
              background: "var(--bg-card)",
            }}
            data-testid="opsec-shortcut"
          >
            <h2
              className="font-display text-sm uppercase tracking-widest font-semibold mb-2"
              style={{ color: "var(--status-warning)" }}
            >
              OPSEC audit required
            </h2>
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              Phase 8 ships with a dedicated hardening checklist. Walk every item
              before building the export tarball in Phase 9.
            </p>
            <Link
              to="/opsec"
              className="inline-flex mt-3 items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest"
              style={{
                borderColor: "var(--status-warning)",
                color: "var(--status-warning)",
                background: "var(--bg-command)",
              }}
              data-testid="goto-opsec-btn"
            >
              Walk audit <ArrowRight size={12} />
            </Link>
          </div>
        )}

        {/* Bundle + next */}
        <section
          className="mt-8 grid grid-cols-1 md:grid-cols-2 gap-4 reveal"
          data-testid="phase-footer"
        >
          <a
            href={phaseBundleUrl(phase.number)}
            className="border p-5 flex items-center justify-between hover:translate-y-[-1px] transition-transform"
            style={{
              borderColor: "var(--accent-amber)",
              background: "var(--bg-card)",
            }}
            data-testid={`bundle-download-button-${phase.number}`}
          >
            <div>
              <div
                className="font-tactical text-[10px] uppercase tracking-widest"
                style={{ color: "var(--accent-amber)" }}
              >
                Phase bundle
              </div>
              <div className="mt-1 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                torus-phase-{phase.number}.zip
              </div>
              <div className="mt-1 text-xs" style={{ color: "var(--text-dim)" }}>
                install.sh · README.md · runit units · env.sample
              </div>
            </div>
            <Download size={20} style={{ color: "var(--accent-amber)" }} />
          </a>

          {nextNumber !== null ? (
            <button
              type="button"
              onClick={() => navigate(`/phase/${nextNumber}`)}
              disabled={!allComplete}
              className="border p-5 flex items-center justify-between transition-transform disabled:opacity-40 enabled:hover:translate-y-[-1px]"
              style={{
                borderColor: allComplete ? "var(--accent-emerald)" : "var(--border-dim)",
                background: "var(--bg-card)",
                cursor: allComplete ? "pointer" : "not-allowed",
              }}
              data-testid="next-phase-btn"
            >
              <div className="text-left">
                <div
                  className="font-tactical text-[10px] uppercase tracking-widest"
                  style={{
                    color: allComplete ? "var(--accent-emerald)" : "var(--text-dim)",
                  }}
                >
                  {allComplete ? "Gate passed" : "Gate locked"}
                </div>
                <div className="mt-1 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                  Advance to Phase {nextNumber}
                </div>
                <div className="mt-1 text-xs" style={{ color: "var(--text-dim)" }}>
                  {allComplete ? "Every step verified" : `Verify remaining ${(phaseProg?.total || 0) - (phaseProg?.verified || 0)} steps`}
                </div>
              </div>
              <ArrowRight size={20} style={{ color: allComplete ? "var(--accent-emerald)" : "var(--text-dim)" }} />
            </button>
          ) : (
            <Link
              to="/"
              className="border p-5 flex items-center justify-between hover:translate-y-[-1px] transition-transform"
              style={{
                borderColor: "var(--accent-emerald)",
                background: "var(--bg-card)",
              }}
              data-testid="finish-link"
            >
              <div>
                <div
                  className="font-tactical text-[10px] uppercase tracking-widest"
                  style={{ color: "var(--accent-emerald)" }}
                >
                  Final phase
                </div>
                <div className="mt-1 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                  Return to dashboard
                </div>
              </div>
              <ArrowRight size={20} style={{ color: "var(--accent-emerald)" }} />
            </Link>
          )}
        </section>
      </div>
    </Shell>
  );
}
