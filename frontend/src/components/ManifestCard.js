import { useRef, useState } from "react";
import { Download, Upload, FileJson, ShieldCheck, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { manifestUrl, importManifest } from "../lib/api";

export default function ManifestCard({ sessionId, onImported }) {
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  const triggerPick = () => fileRef.current?.click();

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking same file
    if (!file) return;
    setBusy(true);
    try {
      const text = await file.text();
      const manifest = JSON.parse(text);
      const sess = await importManifest(manifest);
      toast.success(`Imported · new session ${sess.id.slice(0, 8)}`);
      onImported?.(sess);
    } catch (err) {
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        "Could not import manifest";
      toast.error(`Import failed · ${detail}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className="border border-dim p-5"
      style={{ background: "var(--bg-card)" }}
      data-testid="manifest-card"
    >
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <FileJson size={16} style={{ color: "var(--accent-emerald)" }} />
          <h2
            className="font-display text-sm uppercase tracking-widest font-semibold"
            style={{ color: "var(--accent-emerald)" }}
          >
            Shareable Manifest
          </h2>
        </div>
        <div
          className="flex items-center gap-1 font-tactical text-[10px] uppercase tracking-widest"
          style={{ color: "var(--text-dim)" }}
        >
          <ShieldCheck size={10} />
          <span>HMAC-SHA256</span>
        </div>
      </header>
      <p className="text-xs leading-relaxed mb-4" style={{ color: "var(--text-secondary)" }}>
        Export every verified step, OPSEC check, note, and pinned topic as a
        signed JSON file. Carry it to a second device, import it back, and
        resume exactly where you left off. Tampered manifests are rejected on
        import — the signature check runs before anything hits Mongo.
      </p>

      <div className="flex flex-wrap gap-3">
        <a
          href={manifestUrl()}
          className="flex items-center gap-2 px-3 py-2 font-tactical text-[10px] uppercase tracking-widest"
          style={{
            background: "var(--accent-emerald)",
            color: "var(--bg-primary)",
          }}
          data-testid="export-manifest-btn"
        >
          <Download size={12} />
          <span>Export session</span>
        </a>
        <button
          type="button"
          onClick={triggerPick}
          disabled={busy}
          data-testid="import-manifest-btn"
          className="flex items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest hover:text-amber-glow"
          style={{
            borderColor: "var(--border-bright)",
            color: "var(--text-secondary)",
            background: "var(--bg-command)",
          }}
        >
          <Upload size={12} />
          <span>{busy ? "Verifying…" : "Import manifest"}</span>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={onFile}
          data-testid="import-manifest-input"
        />
      </div>

      <div
        className="mt-4 flex items-start gap-2 text-[11px] leading-relaxed border-t border-dim pt-3"
        style={{ color: "var(--text-dim)" }}
      >
        <AlertTriangle size={12} style={{ color: "var(--status-warning)" }} className="mt-0.5 shrink-0" />
        <span>
          Import creates a <em>new</em> session on this device; it does not
          merge into the current one. The old session on your other device
          remains untouched.
        </span>
      </div>
    </section>
  );
}
