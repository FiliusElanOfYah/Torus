import { useEffect, useRef, useState } from "react";
import {
  Download,
  Upload,
  FileJson,
  ShieldCheck,
  AlertTriangle,
  KeyRound,
  Copy,
  Check,
} from "lucide-react";
import { toast } from "sonner";
import { manifestUrl, importManifest, getPubkey, pubkeyUrl } from "../lib/api";

export default function ManifestCard({ sessionId, onImported }) {
  const [busy, setBusy] = useState(false);
  const [pk, setPk] = useState(null);
  const [showPk, setShowPk] = useState(false);
  const [copied, setCopied] = useState(false);
  const fileRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        setPk(await getPubkey());
      } catch {
        /* non-fatal */
      }
    })();
  }, []);

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

  const copyPk = async () => {
    if (!pk) return;
    try {
      await navigator.clipboard.writeText(pk.public_key_b64);
      setCopied(true);
      toast.success("Public key copied", { duration: 1200 });
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("Clipboard blocked");
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
          style={{ color: "var(--accent-amber)" }}
          data-testid="manifest-algo-badge"
        >
          <ShieldCheck size={10} />
          <span>Dilithium3 · PQ</span>
        </div>
      </header>
      <p className="text-xs leading-relaxed mb-4" style={{ color: "var(--text-secondary)" }}>
        Export every verified step, OPSEC check, note, and pinned topic as a
        <strong style={{ color: "var(--text-amber)" }}> post-quantum-signed </strong>
        JSON file (ML-DSA-65 / Dilithium3, FIPS 204). Carry it to another
        device, import it back, and resume where you left off. Tampered
        manifests and foreign-key signatures are rejected before anything
        touches Mongo.
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
        <button
          type="button"
          onClick={() => setShowPk((s) => !s)}
          data-testid="toggle-pubkey-btn"
          className="flex items-center gap-2 px-3 py-2 border font-tactical text-[10px] uppercase tracking-widest hover:text-amber-glow"
          style={{
            borderColor: "var(--border-bright)",
            color: "var(--accent-amber)",
            background: "var(--bg-command)",
          }}
        >
          <KeyRound size={12} />
          <span>{showPk ? "Hide pubkey" : "Pubkey"}</span>
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

      {/* Pubkey panel */}
      {showPk && pk && (
        <div
          className="mt-4 border p-3"
          style={{
            borderColor: "var(--accent-amber)",
            background: "var(--bg-command)",
          }}
          data-testid="pubkey-panel"
        >
          <div className="flex items-center justify-between mb-2">
            <div
              className="font-tactical text-[10px] uppercase tracking-widest"
              style={{ color: "var(--accent-amber)" }}
            >
              Gateway public key
            </div>
            <button
              type="button"
              onClick={copyPk}
              data-testid="copy-pubkey-btn"
              className="flex items-center gap-1 px-2 py-1 border font-tactical text-[10px] uppercase tracking-widest"
              style={{
                borderColor: copied ? "var(--accent-emerald)" : "var(--border-dim)",
                color: copied ? "var(--accent-emerald)" : "var(--text-secondary)",
                background: "var(--bg-primary)",
              }}
            >
              {copied ? <Check size={10} /> : <Copy size={10} />}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>
          <div className="font-code text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <div>
              <span style={{ color: "var(--text-dim)" }}>algo</span> &nbsp;
              <span style={{ color: "var(--text-amber)" }}>{pk.algo}</span>
            </div>
            <div data-testid="pubkey-fingerprint">
              <span style={{ color: "var(--text-dim)" }}>fingerprint</span> &nbsp;
              <span style={{ color: "var(--text-amber)" }}>{pk.fingerprint}</span>
            </div>
            <div className="pt-2">
              <span style={{ color: "var(--text-dim)" }}>public_key_b64</span>
              <div
                className="mt-1 break-all max-h-24 overflow-auto"
                style={{ color: "var(--text-amber)" }}
                data-testid="pubkey-b64"
              >
                {pk.public_key_b64}
              </div>
            </div>
            <div className="pt-2 text-[10px]" style={{ color: "var(--text-dim)" }}>
              Pin this fingerprint on-device at <span className="font-code" style={{ color: "var(--text-amber)" }}>~/.torus/airgap/gateway.pub.fp</span>; airgapd rejects any manifest whose <span className="font-code">pk_fp</span> field disagrees.
            </div>
          </div>
        </div>
      )}

      <div
        className="mt-4 flex items-start gap-2 text-[11px] leading-relaxed border-t border-dim pt-3"
        style={{ color: "var(--text-dim)" }}
      >
        <AlertTriangle size={12} style={{ color: "var(--status-warning)" }} className="mt-0.5 shrink-0" />
        <span>
          Import creates a <em>new</em> session on this device; it does not
          merge into the current one. The old session on your other device
          remains untouched. Manifests are only accepted if the signature
          verifies <em>and</em> the embedded <span className="font-code">pk_fp</span> matches this gateway.
        </span>
      </div>
    </section>
  );
}
