import { useEffect, useState } from "react";
import Shell from "../components/Shell";
import { getDaemons, getProgress } from "../lib/api";
import { Radio } from "lucide-react";

const RING_ORDER = ["ingest", "storage", "cache", "brain", "guard", "interface"];
const RING_COLOR = {
  ingest: "var(--accent-cyan)",
  storage: "var(--status-running)",
  cache: "var(--accent-amber)",
  brain: "var(--accent-emerald)",
  guard: "var(--status-danger)",
  interface: "#A855F7",
};

export default function Mesh() {
  const [daemons, setDaemons] = useState([]);
  const [progress, setProgress] = useState(null);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    (async () => {
      const d = await getDaemons();
      const pr = await getProgress();
      setDaemons(d.daemons);
      setProgress(pr);
    })();
  }, []);

  const grouped = RING_ORDER.map((r) => ({
    ring: r,
    members: daemons.filter((d) => d.ring === r),
  }));

  return (
    <Shell overallPercent={progress?.overall?.percent || 0}>
      <div className="px-6 py-8 max-w-6xl mx-auto">
        <header className="mb-8 reveal">
          <div
            className="font-tactical text-[10px] uppercase tracking-[0.3em]"
            style={{ color: "var(--accent-amber)" }}
          >
            Topology · 12 daemons · NATS bus · 127.0.0.1 only
          </div>
          <h1
            className="font-display text-3xl sm:text-4xl font-bold tracking-tight uppercase mt-2"
            style={{ color: "var(--text-primary)" }}
          >
            Daemon Mesh
          </h1>
          <p
            className="mt-4 max-w-2xl text-sm leading-relaxed"
            style={{ color: "var(--text-secondary)" }}
          >
            Each daemon is a single-purpose process supervised by runit. Events
            flow left-to-right through the ingest → storage → brain rings, with
            sentineld tapping every subject and airgapd signing every release.
            Tap a node to inspect its contract.
          </p>
        </header>

        {/* Ring columns */}
        <section
          className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 reveal-stagger"
          data-testid="mesh-grid"
        >
          {grouped.map((g) => (
            <div key={g.ring} className="reveal">
              <div
                className="font-tactical text-[10px] uppercase tracking-widest pb-2 mb-3 border-b"
                style={{ color: RING_COLOR[g.ring], borderColor: "var(--border-dim)" }}
              >
                {g.ring}
              </div>
              <ul className="space-y-2">
                {g.members.map((d) => (
                  <li key={d.name}>
                    <button
                      type="button"
                      onClick={() => setSelected(d)}
                      data-testid={`daemon-mesh-node-${d.name}`}
                      className="w-full text-left border p-3 transition-all hover:translate-y-[-1px]"
                      style={{
                        borderColor:
                          selected?.name === d.name ? RING_COLOR[g.ring] : "var(--border-dim)",
                        background:
                          selected?.name === d.name ? "var(--bg-card-hover)" : "var(--bg-card)",
                      }}
                    >
                      <div className="flex items-center gap-2">
                        <Radio
                          size={10}
                          style={{ color: RING_COLOR[g.ring] }}
                        />
                        <span
                          className="font-code text-sm font-semibold"
                          style={{ color: "var(--text-primary)" }}
                        >
                          {d.name}
                        </span>
                      </div>
                      <div
                        className="mt-1 text-[10px] font-tactical uppercase tracking-wider"
                        style={{ color: "var(--text-dim)" }}
                      >
                        {d.bus}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>

        {/* Detail panel */}
        {selected && (
          <section
            className="mt-8 border p-6 reveal"
            style={{
              borderColor: RING_COLOR[selected.ring],
              background: "var(--bg-card)",
            }}
            data-testid="daemon-detail"
          >
            <div className="flex items-center justify-between">
              <div>
                <div
                  className="font-tactical text-[10px] uppercase tracking-[0.3em]"
                  style={{ color: RING_COLOR[selected.ring] }}
                >
                  {selected.ring} ring
                </div>
                <h2
                  className="font-display text-2xl font-bold uppercase mt-1"
                  style={{ color: "var(--text-primary)" }}
                >
                  {selected.name}
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="font-tactical text-[10px] uppercase tracking-widest border px-3 py-1.5"
                style={{
                  borderColor: "var(--border-bright)",
                  color: "var(--text-secondary)",
                }}
                data-testid="close-daemon-detail"
              >
                close
              </button>
            </div>
            <p className="mt-4 text-sm" style={{ color: "var(--text-primary)" }}>
              {selected.role}
            </p>
            <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="border border-dim p-3">
                <div
                  className="font-tactical text-[10px] uppercase tracking-widest"
                  style={{ color: "var(--text-dim)" }}
                >
                  NATS subject
                </div>
                <div className="font-code text-sm mt-1" style={{ color: "var(--text-amber)" }}>
                  {selected.bus}
                </div>
              </div>
              <div className="border border-dim p-3">
                <div
                  className="font-tactical text-[10px] uppercase tracking-widest"
                  style={{ color: "var(--text-dim)" }}
                >
                  Ring
                </div>
                <div className="font-code text-sm mt-1" style={{ color: RING_COLOR[selected.ring] }}>
                  {selected.ring}
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Topology legend */}
        <section
          className="mt-10 border border-dim p-5 reveal"
          style={{ background: "var(--bg-card)" }}
          data-testid="mesh-legend"
        >
          <h2
            className="font-display text-sm uppercase tracking-widest font-semibold mb-3"
            style={{ color: "var(--accent-amber)" }}
          >
            Event loop
          </h2>
          <pre
            className="command-block"
            style={{ color: "var(--text-primary)" }}
            data-testid="mesh-event-loop"
          >
{`user → gatewayd → NATS → fetcherd → parserd → indexerd ⇄ mongod/vaultd
                                        ↘ summarizerd → gatewayd (back)
                                        ↘ pageringd  (hot cache)
sentineld taps every subject · airgapd signs every release`}
          </pre>
        </section>
      </div>
    </Shell>
  );
}
