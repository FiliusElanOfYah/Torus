import { useState } from "react";
import { updateProfile } from "../lib/api";
import { toast } from "sonner";
import { Save, Smartphone } from "lucide-react";

const FIELDS = [
  {
    key: "termux_source",
    label: "Termux source",
    type: "select",
    options: ["f-droid", "play-store", "other"],
  },
  {
    key: "android_api",
    label: "Android API level",
    type: "text",
    placeholder: "e.g. 35",
  },
  { key: "free_disk_gb", label: "Free disk (GB)", type: "text", placeholder: "e.g. 42" },
  {
    key: "vault_location",
    label: "Vault location",
    type: "select",
    options: ["internal", "sdcard"],
  },
  {
    key: "default_llm",
    label: "Default LLM",
    type: "select",
    options: ["sonnet", "opus", "haiku"],
  },
];

const BOOLS = [
  { key: "acode_installed", label: "Acode installed" },
  { key: "termux_boot_installed", label: "Termux:Boot installed" },
  { key: "opus_for_code", label: "Use Opus-tier for coderd" },
  { key: "jev_gating", label: "Jev yes/no gating in sentineld" },
];

export default function DeviceProfile({ initial, onSaved }) {
  const [profile, setProfile] = useState(
    initial || {
      device_name: "Samsung Galaxy S26 Ultra",
      termux_source: "f-droid",
      android_api: "",
      free_disk_gb: "",
      acode_installed: false,
      termux_boot_installed: false,
      vault_location: "internal",
      default_llm: "sonnet",
      opus_for_code: true,
      jev_gating: true,
    }
  );
  const [saving, setSaving] = useState(false);

  const set = (k, v) => setProfile((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const r = await updateProfile(profile);
      toast.success("Device profile saved");
      onSaved?.(r);
    } catch (e) {
      toast.error("Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section
      className="border border-dim p-5"
      style={{ background: "var(--bg-card)" }}
      data-testid="device-profile"
    >
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Smartphone size={16} style={{ color: "var(--accent-amber)" }} />
          <h2
            className="font-display text-sm uppercase tracking-widest font-semibold"
            style={{ color: "var(--accent-amber)" }}
          >
            Device Profile
          </h2>
        </div>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          data-testid="save-profile-btn"
          className="flex items-center gap-2 px-3 py-1.5 border font-tactical text-[10px] uppercase tracking-widest transition-colors"
          style={{
            borderColor: "var(--accent-amber)",
            color: "var(--accent-amber)",
            background: "var(--bg-command)",
          }}
        >
          <Save size={12} />
          <span>{saving ? "Writing…" : "Commit"}</span>
        </button>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="col-span-full">
          <label className="font-tactical text-[10px] uppercase tracking-widest" style={{ color: "var(--text-dim)" }}>
            Device
          </label>
          <input
            type="text"
            value={profile.device_name || ""}
            onChange={(e) => set("device_name", e.target.value)}
            className="w-full mt-1 px-3 py-2 text-sm font-code"
            data-testid="input-device_name"
          />
        </div>

        {FIELDS.map((f) => (
          <div key={f.key}>
            <label
              className="font-tactical text-[10px] uppercase tracking-widest"
              style={{ color: "var(--text-dim)" }}
            >
              {f.label}
            </label>
            {f.type === "select" ? (
              <select
                value={profile[f.key] || ""}
                onChange={(e) => set(f.key, e.target.value)}
                className="w-full mt-1 px-3 py-2 text-sm font-code"
                data-testid={`input-${f.key}`}
              >
                {f.options.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type={f.type}
                value={profile[f.key] || ""}
                placeholder={f.placeholder}
                onChange={(e) => set(f.key, e.target.value)}
                className="w-full mt-1 px-3 py-2 text-sm font-code"
                data-testid={`input-${f.key}`}
              />
            )}
          </div>
        ))}

        {BOOLS.map((b) => (
          <label
            key={b.key}
            className="flex items-center gap-3 cursor-pointer select-none px-3 py-2 border border-dim"
            style={{ background: "var(--bg-command)" }}
            data-testid={`toggle-${b.key}`}
          >
            <input
              type="checkbox"
              className="sr-only"
              checked={!!profile[b.key]}
              onChange={(e) => set(b.key, e.target.checked)}
            />
            <span
              className="w-4 h-4 flex items-center justify-center border"
              style={{
                borderColor: profile[b.key] ? "var(--accent-amber)" : "var(--border-bright)",
                background: profile[b.key] ? "var(--accent-amber)" : "transparent",
              }}
            >
              {profile[b.key] && (
                <span
                  style={{
                    width: 6,
                    height: 6,
                    background: "var(--bg-primary)",
                  }}
                />
              )}
            </span>
            <span className="text-xs font-tactical uppercase tracking-wider" style={{ color: "var(--text-secondary)" }}>
              {b.label}
            </span>
          </label>
        ))}
      </div>
    </section>
  );
}
