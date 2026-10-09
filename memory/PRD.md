# TORUS — Local-First Research & Build Assistant on Termux

## Original problem statement
TORUS is a hybrid, modular daemon-mesh running on a non-rooted Samsung Galaxy
S26 Ultra under Termux (F-Droid). Deterministic, event-driven, scripted
pipelines at the core; Claude Sonnet/Opus as the opt-in reasoning layer. 12
daemons talk over local NATS, supervised by termux-services (runit) + Termux
Boot. The user chose **Option C — Phase-0/1 Setup Generator**: a guided,
copy-pasteable checklist web app that generates verified Termux install
scripts, service unit files, and bootstrap artifacts for every phase (0 → 9).
No live research pipeline is run here — the web app produces the artefacts the
user runs on their phone.

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
- Phase 8 has a dedicated OPSEC audit (10 items).
- Daemon-mesh topology viewer (12 daemons, 6 rings).
- Phase bundle ZIP (`install.sh` + `README.md` + runit units + env.sample +
  any artefacts), plus a full-mesh ZIP that includes `bootstrap-all.sh`.
- Dark tactical cockpit aesthetic — amber phosphor + emerald, Azeret Mono /
  JetBrains Mono.
- Session state persisted in Mongo, resumable via `localStorage` session id.

## What's been implemented (2026-02 · first ship)
Backend (`/app/backend`):
- `phases.py` — complete 10-phase catalog (64 steps) + 12-daemon registry +
  Phase-8 OPSEC checklist.
- `bundle.py` — per-phase and full-mesh ZIP builders (streamed from memory).
- `server.py` — FastAPI gateway with:
  - `GET /api/phases`, `GET /api/phases/{n}`, `GET /api/daemons`
  - `POST /api/sessions`, `GET /api/sessions/{id}`
  - `PATCH /api/sessions/{id}/profile`
  - `POST /api/sessions/{id}/steps` (toggle)
  - `POST /api/sessions/{id}/opsec` (toggle)
  - `POST /api/sessions/{id}/notes`
  - `GET /api/sessions/{id}/progress`
  - `GET /api/bundle/phase/{n}`, `GET /api/bundle/mesh`

Frontend (`/app/frontend/src`):
- `App.js` — routes `/`, `/phase/:number`, `/mesh`, `/opsec`.
- `pages/Dashboard.js` — hero, phase rail, device profile, phase overview.
- `pages/PhaseDetail.js` — gated step cards + bundle download + "advance to
  next" button.
- `pages/Mesh.js` — 6-ring, 12-daemon grid + event-loop ASCII block.
- `pages/Opsec.js` — 10-item hardening audit with pass banner.
- `components/CommandCard.js`, `PhaseStepper.js`, `DeviceProfile.js`,
  `Shell.js` — reusable primitives.
- `lib/api.js` — axios client + localStorage session id.
- `index.css` — tactical cockpit tokens, CRT scanline overlay, phosphor
  typography stack.

Verified end-to-end by the testing agent: 17/17 backend tests, full
Playwright UI flow, 100 % success rate (`/app/test_reports/iteration_1.json`).

## Prioritised backlog
- **P1 — Phase-specific notes.** The backend already stores per-step notes
  (`POST /api/sessions/{id}/notes`); surface a notes drawer per command card.
- **P1 — Shareable manifest.** Export the session (profile + verified steps +
  OPSEC + notes) as a signed JSON so a second device can resume.
- **P1 — Pinned-topic editor.** UI for `~/.torus/etc/pinned.yaml` (Phase 7)
  before you push the config to the device.
- **P2 — Terminal replay widget.** Paste the step's actual output back into
  the UI; store under `notes[step_id].output` for later review.
- **P2 — Multi-device roster.** Multiple sessions in a tabbed selector so
  future federation (Pi/VPS extension) is just another row.
- **P3 — Dark/amber-high-contrast theme toggle.** Design guidelines already
  spec the alt palette.
- **P3 — PWA packaging** so this companion runs offline on the phone itself.

## Decisions already fixed (per user open-decisions)
- Vault location: internal storage (default; configurable in profile).
- Default LLM: Sonnet 4.5 for synthesis, Opus-tier for code.
- Jev yes/no gating inside sentineld: ON.
