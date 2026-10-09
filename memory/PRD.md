# TORUS — Local-First Research & Build Assistant on Termux

## Original problem statement
TORUS is a hybrid, modular daemon-mesh running on a non-rooted Samsung Galaxy
S26 Ultra under Termux (F-Droid). Deterministic, event-driven, scripted
pipelines at the core; Claude Sonnet/Opus as the opt-in reasoning layer. 12
daemons talk over local NATS, supervised by termux-services (runit) + Termux
Boot. The user chose **Option C — Phase-0/1 Setup Generator**: a guided,
copy-pasteable checklist web app that generates verified Termux install
scripts, service unit files, and bootstrap artifacts for every phase (0 → 9).

## User personas
- **Solo operator** — layman coder, non-rooted S26 Ultra, needs
  copy-pasteable, verified steps. No "assume you have X" — every step verified
  before the next.

## Core (static) requirements
- 10 phases (0–9) with ordered steps, every step shows: command, expected
  output, verification probe, troubleshooting.
- Device profile captured once (Termux source, Android API, free disk, Vault
  location, LLM choice, Opus-for-code, Jev gating, etc.).
- Per-step verification is persisted; next phase unlocks when the previous is
  100 % verified.
- Per-step notes capture the actual terminal output and travel in the
  manifest.
- Phase 7 pinned-topics editor generates `~/.torus/etc/pinned.yaml`.
- Phase 8 has a dedicated OPSEC audit (10 items).
- Daemon-mesh topology viewer (12 daemons, 6 rings).
- Phase bundle ZIP + full-mesh ZIP with `bootstrap-all.sh`.
- Signed shareable manifest (HMAC-SHA256) for cross-device resume.
- Dark tactical cockpit aesthetic — amber phosphor + emerald, Azeret /
  JetBrains Mono stack.
- Installable PWA, offline-capable for the shell.
- Session state in Mongo, resumable via `localStorage` session id.

## What's been implemented (growing log)

### 2026-02-A · first ship
- 10-phase, 64-step catalog + 12-daemon registry + Phase-8 OPSEC checklist.
- FastAPI gateway: phases, daemons, sessions, profile, step/opsec toggles,
  progress rollup, per-phase + full-mesh ZIP bundles.
- Frontend: Dashboard, PhaseDetail, Mesh, Opsec pages. Phase stepper,
  command cards, device profile. Tactical cockpit tokens + CRT scanlines.
- Verified: 17/17 backend tests + full Playwright flow, 100 %
  (`/app/test_reports/iteration_1.json`).

### 2026-02-B · four-feature expansion
- **Phase Notes Drawer** — Per-step right-side drawer with autosave textarea
  (`POST /api/sessions/{id}/notes`); captured-output preview on the card.
- **Pinned Topics Editor** — `/pinned` route with topic/seed CRUD + YAML
  download (`GET /api/sessions/{id}/pinned.yaml`).
- **Shareable Manifest** — Signed JSON export
  (`GET /api/sessions/{id}/manifest`, HMAC-SHA256) + verifying import
  (`POST /api/sessions/import`). Signing key in `TORUS_SIGNING_KEY`.
- **PWA Packaging** — `manifest.webmanifest`, service worker (cache-first
  shell / network-first `/api`), SVG icon + maskable variant, apple-touch
  meta, SW registered on window load.
- **Mongo dot-notation fix** — `set_note` now does read-modify-write so step
  IDs like `0.1` are preserved as literal keys.
- Verified: **31/31 backend tests + 100 % frontend flow**
  (`/app/test_reports/iteration_3.json`).

## Prioritised backlog
- **P1 — Multi-device roster** — tabbed session selector so switching
  between phone / laptop / Pi federations is one click.
- **P1 — Dossier preview tab** — once the user runs Phase 3 and starts
  producing `vault/*.md`, show a preview surface here that reads from the
  same signed-manifest contract.
- **P2 — Dark/amber-high-contrast theme toggle** — design guidelines already
  spec the alt "amber-on-black terminal" palette.
- **P2 — Deployment with custom domain** — user offered
  `guardianandroid.app` through Cloudflare; wire it up at deploy time.
- **P3 — End-to-end crypto on manifest** — replace HMAC-SHA256 with the
  real Dilithium3 keypair generated in Phase 8 so the manifest is signed
  by the same key that signs the airgap tokens.

## Decisions already fixed (per user open-decisions)
- Vault location: internal storage (default; configurable in profile).
- Default LLM: Sonnet 4.5 for synthesis, Opus-tier for code.
- Jev yes/no gating inside sentineld: ON.
- Deployment domain (offered): `guardianandroid.app` via Cloudflare —
  wire at deploy time, no code changes needed (frontend already uses
  `REACT_APP_BACKEND_URL` dynamically).
