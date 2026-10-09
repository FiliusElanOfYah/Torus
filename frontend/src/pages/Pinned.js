import { useEffect, useState } from "react";
import Shell from "../components/Shell";
import {
  ensureSession,
  getPinnedTopics,
  getProgress,
  pinnedYamlUrl,
  savePinnedTopics,
} from "../lib/api";
import { Plus, Trash2, Download, Save, Link as LinkIcon, Radar } from "lucide-react";
import { toast } from "sonner";

export default function Pinned() {
  const [topics, setTopics] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [progressPct, setProgressPct] = useState(0);

  useEffect(() => {
    (async () => {
      await ensureSession();
      const [t, pr] = await Promise.all([getPinnedTopics(), getProgress()]);
      setTopics(t);
      setProgressPct(pr?.overall?.percent || 0);
      setLoading(false);
    })();
  }, []);

  const addTopic = () =>
    setTopics((t) => [...t, { name: `topic-${t.length + 1}`, seeds: [""] }]);

  const removeTopic = (i) =>
    setTopics((t) => t.filter((_, idx) => idx !== i));

  const setTopicName = (i, name) =>
    setTopics((t) => t.map((x, idx) => (idx === i ? { ...x, name } : x)));

  const addSeed = (i) =>
    setTopics((t) =>
      t.map((x, idx) => (idx === i ? { ...x, seeds: [...x.seeds, ""] } : x))
    );

  const setSeed = (i, j, v) =>
    setTopics((t) =>
      t.map((x, idx) =>
        idx === i
          ? { ...x, seeds: x.seeds.map((s, jdx) => (jdx === j ? v : s)) }
          : x
      )
    );

  const removeSeed = (i, j) =>
    setTopics((t) =>
      t.map((x, idx) =>
        idx === i ? { ...x, seeds: x.seeds.filter((_, jdx) => jdx !== j) } : x
      )
    );

  const save = async () => {
    // Prune empty seeds and empty-named topics
    const clean = topics
      .map((t) => ({
        name: (t.name || "").trim(),
        seeds: (t.seeds || []).map((s) => s.trim()).filter(Boolean),
      }))
      .filter((t) => t.name);
    setSaving(true);
    try {
      const r = await savePinnedTopics(clean);
      setTopics(r);
      toast.success("Pinned topics saved");
    } catch {
      toast.error("Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Shell overallPercent={progressPct}>
      <div className="px-6 py-8 max-w-4xl mx-auto">
        <header className="mb-8 reveal" data-testid="pinned-header">
          <div
            className="font-tactical text-[10px] uppercase tracking-[0.3em]"
            style={{ color: "var(--accent-amber)" }}
          >
            Phase 07 · oracled seed config
          </div>
          <h1
            className="font-display text-3xl sm:text-4xl font-bold tracking-tight uppercase mt-2"
            style={{ color: "var(--text-primary)" }}
          >
            Pinned Topics
          </h1>
          <p
            className="mt-4 max-w-2xl text-sm leading-relaxed"
            style={{ color: "var(--text-secondary)" }}
          >
            oracled wakes once per hour and re-ingests every seed URL for each
            topic. Tune the list here, download the YAML, and drop it at{" "}
            <span className="font-code" style={{ color: "var(--text-amber)" }}>
              ~/.torus/etc/pinned.yaml
            </span>{" "}
            on the device. No restart required — oracled re-reads the file on
            every cycle.
          </p>

          <div className="mt-5 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={save}
              disabled={saving}
              data-testid="save-pinned-btn"
              className="flex items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest"
              style={{
                borderColor: "var(--accent-amber)",
                color: "var(--accent-amber)",
                background: "var(--bg-command)",
              }}
            >
              <Save size={12} />
              <span>{saving ? "Committing…" : "Commit"}</span>
            </button>
            <a
              href={pinnedYamlUrl()}
              className="flex items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest hover:text-amber-glow"
              style={{
                borderColor: "var(--border-bright)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
              }}
              data-testid="download-pinned-btn"
            >
              <Download size={12} />
              <span>pinned.yaml</span>
            </a>
            <button
              type="button"
              onClick={addTopic}
              data-testid="add-topic-btn"
              className="flex items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest hover:text-amber-glow"
              style={{
                borderColor: "var(--border-bright)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
              }}
            >
              <Plus size={12} />
              <span>Add topic</span>
            </button>
          </div>
        </header>

        {loading ? (
          <div
            className="font-tactical text-xs uppercase tracking-widest"
            style={{ color: "var(--text-dim)" }}
          >
            Loading…
          </div>
        ) : topics.length === 0 ? (
          <div
            className="border border-dim p-6 text-center"
            style={{ background: "var(--bg-card)" }}
            data-testid="pinned-empty"
          >
            <Radar size={28} style={{ color: "var(--text-dim)" }} className="mx-auto" />
            <p className="mt-3 text-sm" style={{ color: "var(--text-secondary)" }}>
              No pinned topics yet. Click <strong>Add topic</strong> to brief oracled.
            </p>
          </div>
        ) : (
          <ul className="space-y-4 reveal-stagger" data-testid="pinned-list">
            {topics.map((t, i) => (
              <li
                key={i}
                className="reveal border border-dim p-4"
                style={{ background: "var(--bg-card)" }}
                data-testid={`pinned-topic-${i}`}
              >
                <div className="flex items-center gap-3">
                  <Radar size={14} style={{ color: "var(--accent-amber)" }} />
                  <input
                    type="text"
                    value={t.name}
                    onChange={(e) => setTopicName(i, e.target.value)}
                    placeholder="topic-name"
                    className="flex-1 px-3 py-2 text-sm font-code"
                    data-testid={`topic-name-${i}`}
                  />
                  <button
                    type="button"
                    onClick={() => removeTopic(i)}
                    data-testid={`remove-topic-${i}`}
                    className="border px-2 py-2"
                    style={{
                      borderColor: "var(--border-bright)",
                      color: "var(--status-danger)",
                      background: "var(--bg-command)",
                    }}
                    aria-label="remove topic"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>

                <div className="mt-4 space-y-2">
                  <div
                    className="font-tactical text-[10px] uppercase tracking-widest"
                    style={{ color: "var(--text-dim)" }}
                  >
                    Seed URLs
                  </div>
                  {(t.seeds || []).map((s, j) => (
                    <div key={j} className="flex items-center gap-2" data-testid={`seed-row-${i}-${j}`}>
                      <LinkIcon size={12} style={{ color: "var(--text-dim)" }} />
                      <input
                        type="url"
                        value={s}
                        onChange={(e) => setSeed(i, j, e.target.value)}
                        placeholder="https://…"
                        className="flex-1 px-3 py-2 font-code text-sm"
                        data-testid={`seed-input-${i}-${j}`}
                      />
                      <button
                        type="button"
                        onClick={() => removeSeed(i, j)}
                        data-testid={`remove-seed-${i}-${j}`}
                        className="border px-2 py-2"
                        style={{
                          borderColor: "var(--border-dim)",
                          color: "var(--text-dim)",
                          background: "var(--bg-command)",
                        }}
                        aria-label="remove seed"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => addSeed(i)}
                    data-testid={`add-seed-${i}`}
                    className="flex items-center gap-2 px-2 py-1.5 border font-tactical text-[10px] uppercase tracking-widest hover:text-amber-glow"
                    style={{
                      borderColor: "var(--border-dim)",
                      color: "var(--text-secondary)",
                      background: "var(--bg-command)",
                    }}
                  >
                    <Plus size={10} />
                    <span>Add seed</span>
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        <section
          className="mt-10 border border-dim p-5 reveal"
          style={{ background: "var(--bg-card)" }}
          data-testid="pinned-footer"
        >
          <h2
            className="font-display text-sm uppercase tracking-widest font-semibold mb-2"
            style={{ color: "var(--accent-amber)" }}
          >
            Deploy snippet
          </h2>
          <pre className="command-block" data-testid="pinned-deploy-snippet">
            <code>curl -sfL &lt;this-manual&gt;/api/sessions/&lt;id&gt;/pinned.yaml -o ~/.torus/etc/pinned.yaml</code>
          </pre>
          <p className="mt-3 text-xs" style={{ color: "var(--text-dim)" }}>
            Or use the download button above and <span className="font-code" style={{ color: "var(--text-amber)" }}>adb push</span> it
            to <span className="font-code" style={{ color: "var(--text-amber)" }}>/sdcard/Download/</span>.
          </p>
        </section>
      </div>
    </Shell>
  );
}
