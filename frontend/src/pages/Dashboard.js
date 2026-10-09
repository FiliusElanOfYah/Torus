import { useEffect, useState } from "react";
import { ensureSession, getPhases, getProgress } from "../lib/api";
import PhaseStepper from "../components/PhaseStepper";
import DeviceProfile from "../components/DeviceProfile";
import ManifestCard from "../components/ManifestCard";
import Shell from "../components/Shell";
import { ArrowRight, Download, Hexagon, Radar } from "lucide-react";
import { Link } from "react-router-dom";

export default function Dashboard() {
  const [session, setSession] = useState(null);
  const [phases, setPhases] = useState([]);
  const [progress, setProgress] = useState(null);

  const refresh = async () => {
    const [s, p, pr] = await Promise.all([ensureSession(), getPhases(), null].map(async (x, i) => (i === 2 ? null : x)));
    const pr2 = await getProgress();
    setSession(s);
    setPhases(p.phases);
    setProgress(pr2);
  };

  useEffect(() => {
    (async () => {
      await ensureSession();
      const [p, pr] = await Promise.all([getPhases(), getProgress()]);
      const s = await ensureSession();
      setSession(s);
      setPhases(p.phases);
      setProgress(pr);
    })();
  }, []);

  const pct = progress?.overall?.percent ?? 0;
  const nextActive = progress?.phases?.find((p) => !p.complete)?.number ?? 0;

  return (
    <Shell overallPercent={pct}>
      <div className="px-6 py-8 max-w-7xl mx-auto">
        {/* Hero */}
        <section className="mb-10 reveal" data-testid="dashboard-hero">
          <div
            className="font-tactical text-[10px] uppercase tracking-[0.3em]"
            style={{ color: "var(--accent-amber)" }}
          >
            Mission Brief · Local-First Research &amp; Build Assistant
          </div>
          <h1
            className="font-display text-3xl sm:text-4xl lg:text-5xl font-bold tracking-tight uppercase mt-3 cursor-blink"
            style={{ color: "var(--text-primary)" }}
          >
            TORUS Setup
            <br />
            <span className="text-amber-glow">Flight Manual</span>
          </h1>
          <p
            className="mt-4 max-w-2xl text-sm leading-relaxed"
            style={{ color: "var(--text-secondary)" }}
          >
            Ten phases. One phone. Every step is verified before the next unlocks.
            Copy-paste the command, confirm exit-code 0, download the phase bundle,
            and ship it to the device. Sources first, synthesis second — enforced by
            the mesh, not promised by a chatbot.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link
              to={`/phase/${nextActive}`}
              className="flex items-center gap-2 px-4 py-2 font-tactical text-xs uppercase tracking-widest"
              style={{
                background: "var(--accent-amber)",
                color: "var(--bg-primary)",
              }}
              data-testid="hero-cta-continue"
            >
              <span>Continue Phase {nextActive}</span>
              <ArrowRight size={14} />
            </Link>
            <a
              href={`${process.env.REACT_APP_BACKEND_URL}/api/bundle/mesh`}
              className="flex items-center gap-2 px-4 py-2 border font-tactical text-xs uppercase tracking-widest hover:text-amber-glow"
              style={{
                borderColor: "var(--border-bright)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
              }}
              data-testid="hero-full-bundle"
            >
              <Download size={14} />
              <span>Full Mesh ZIP</span>
            </a>
            <Link
              to="/mesh"
              className="flex items-center gap-2 px-4 py-2 border font-tactical text-xs uppercase tracking-widest hover:text-amber-glow"
              style={{
                borderColor: "var(--border-bright)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
              }}
              data-testid="hero-mesh-link"
            >
              <Hexagon size={14} />
              <span>Daemon Mesh</span>
            </Link>
            <Link
              to="/pinned"
              className="flex items-center gap-2 px-4 py-2 border font-tactical text-xs uppercase tracking-widest hover:text-amber-glow"
              style={{
                borderColor: "var(--border-bright)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
              }}
              data-testid="hero-pinned-link"
            >
              <Radar size={14} />
              <span>Pinned Topics</span>
            </Link>
          </div>
        </section>

        {/* Progress rail */}
        <section className="mb-10" data-testid="dashboard-stepper">
          <div className="flex items-end justify-between mb-4">
            <h2
              className="font-display text-lg sm:text-xl uppercase tracking-wide font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              Phase Rail
            </h2>
            <div
              className="font-tactical text-xs uppercase tracking-widest"
              style={{ color: "var(--text-dim)" }}
              data-testid="progress-indicator"
            >
              <span style={{ color: "var(--accent-emerald)" }}>{pct}%</span> verified ·{" "}
              {progress?.overall?.verified_steps || 0}/{progress?.overall?.total_steps || 0} steps
            </div>
          </div>
          {phases.length > 0 && (
            <PhaseStepper phases={phases} progress={progress} />
          )}
        </section>

        {/* Grid: Profile + Phase overview */}
        <section className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <div className="lg:col-span-2 flex flex-col gap-6">
            {session && (
              <DeviceProfile
                initial={session.profile}
                onSaved={(s) => setSession(s)}
              />
            )}
            {session && (
              <ManifestCard
                sessionId={session.id}
                onImported={async (s) => {
                  setSession(s);
                  const pr = await getProgress();
                  setProgress(pr);
                }}
              />
            )}
          </div>

          <div className="lg:col-span-3">
            <div
              className="border border-dim p-5"
              style={{ background: "var(--bg-card)" }}
              data-testid="phase-overview"
            >
              <h2
                className="font-display text-sm uppercase tracking-widest font-semibold mb-4"
                style={{ color: "var(--accent-amber)" }}
              >
                Phase Overview
              </h2>
              <ul className="space-y-2 reveal-stagger">
                {phases.map((p) => {
                  const pr = progress?.phases?.find((x) => x.number === p.number);
                  const complete = pr?.complete;
                  return (
                    <li key={p.number} className="reveal">
                      <Link
                        to={`/phase/${p.number}`}
                        className="flex items-center justify-between px-3 py-2 border border-dim hover:border-bright transition-colors"
                        style={{
                          background: "var(--bg-command)",
                          borderLeft: complete
                            ? "3px solid var(--accent-emerald)"
                            : "3px solid var(--border-dim)",
                        }}
                        data-testid={`phase-overview-item-${p.number}`}
                      >
                        <div className="flex items-center gap-3 min-w-0">
                          <span
                            className="font-display text-[11px] font-bold shrink-0"
                            style={{ color: "var(--accent-amber)" }}
                          >
                            P{String(p.number).padStart(2, "0")}
                          </span>
                          <span
                            className="text-sm truncate"
                            style={{ color: "var(--text-primary)" }}
                          >
                            {p.name}
                          </span>
                        </div>
                        <span
                          className="font-tactical text-[10px] uppercase tracking-widest shrink-0"
                          style={{
                            color: complete
                              ? "var(--accent-emerald)"
                              : "var(--text-dim)",
                          }}
                        >
                          {pr ? `${pr.verified}/${pr.total}` : "—"}
                        </span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </section>

        <footer className="mt-14 pb-6 flex items-center justify-between border-t border-dim pt-4">
          <div
            className="font-tactical text-[10px] uppercase tracking-widest"
            style={{ color: "var(--text-dim)" }}
          >
            TORUS · operations manual with character
          </div>
          <div
            className="font-tactical text-[10px] uppercase tracking-widest"
            style={{ color: "var(--text-dim)" }}
          >
            session · {session?.id?.slice(0, 8) || "—"}
          </div>
        </footer>
      </div>
    </Shell>
  );
}
