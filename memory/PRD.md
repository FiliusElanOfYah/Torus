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

### 2026-10-10 · Self-building foundation (torus/ folder)
- Source of truth now lives in the repo under `torus/`: canonical daemons (files/bin), web page, runit templates, configs.
- `torus` CLI: doctor, install <0-6|all> [--force], update (git pull + install all), files (drift/CRLF), backups, rollback, status, logs, restart, open, ask, job, search, selftest ingest|llm, key set|rm|status, llm on|off|model|id|test|models, search mode|count.
- Safe writer (lib/common.sh): whole-file writes, CRLF stripped, backup to ~/.torus/backups/<stamp>, skip if identical, own/keep policy (nats/mongo/llm.toggle/search.conf = keep).
- Fixed gaps: indexerd dedupe (startup + per-url replace), error-wrapped handlers in every daemon, svlogd rotation 1MB x5, rotating sentineld/airgapd/llmd JSONL, honest UA with contact URL (Wikipedia 403 fix), robots.txt, 3s per-host rate limit, 24h cache.
- Phase 6 gatewayd: stdlib HTTP server on 127.0.0.1:7777 (Host check + X-Torus header), jobs persisted and resumed, Needs-you paste/upload/skip, source mode urls|brave|both, Claude Sonnet/Opus via sentineld->llmd->httpx with retry/backoff and local extractive fallback, answers to vault/answers.
- Removed the published Dilithium secret key from git tracking (.gitignore). The old key is still in GitHub history, so it was rotated.
- Tested: 26/26 gateway API tests, UI flows, installer idempotency/rollback/keep (iteration report).

## Backlog
- P1: verify the real Claude model ids on the device (`torus llm models`); a citation graph (Phase 4 goal); coderd + oracled (Phase 7) as installers; follow-up questions in the same job.
- P2: Phase 8 hardening, Phase 9 export (`torus export`), Mongo use by the daemons.

### 2026-10-15 · Coder, follow-ups, citation map, PDF reports
- Phase 7 coderd (projects root ~/projects; tree/read/write/rollback/run over NATS; path-safe; backups in ~/.torus/backups/coder/<task>; runs only user-typed commands, time-limited).
- gateway_code.py: Claude proposes whole files (<<<FILE>>> blocks), diff review (apply/revise/reject), write, run, auto-fix loop up to N attempts, rollback. /api/code/* needs X-Torus-Token (~/.torus/etc/gateway.token, handed to Brave by `torus open`).
- Follow-ups: job.followups[]; full conversation sent as messages (llmd + sentineld support messages).
- Citation map (torus_cite.py): link extraction, page/site edges, ranking score, shared outside refs.
- PDF reports (torus_report.py, stdlib-only PDF writer): vault/reports/<job>.pdf + download.
- CLI: torus follow/report/code projects|new|run|tasks|root. Tests: iteration 6 22/22 + UI.

### 2026-10-24 · Phase 8 hardening + Phase 9 export/restore (torus/ tree)
- Phase 8 (`torus install 8`): torus_seal.py signs an airgap manifest (sha3-256 of airgap/*.key, etc/gateway.token, bin/*.py) with ML-DSA-65 via pure-Python `dilithium-py` (HMAC-SHA256 fallback). Keypair airgap/mldsa65.{pk,sk}; `torus seal`, `torus seal verify|fingerprint`. airgapd checks the seal per request and refuses tampered keys/manifest (`ERR sealed: ...`). `torus install` and `torus key set|rm` re-seal.
- torus_audit.py: 13 checks (opsec-1..10 + gateway token, Host guard, CRLF) -> var/audit.json + logs/audit.jsonl; `torus audit`; gatewayd GET/POST /api/audit; Settings card on the web page. `torus_audit.py boot` runs from Termux:Boot: Claude off at reboot + prune rotated logs older than 7 days (etc/hardening.conf, keep).
- Phase 9 (`torus install 9`): torus_export.py writes etc/requirements.freeze, etc/pkg.manifest, docs/RESTORE.md and a signed tarball (Downloads/torus-<date>.tar.gz + .sha256 + .sig); keys excluded unless --with-keys; --no-vault. `torus export [list]`, `torus restore <file>` (hash+sig check, backups to ~/.torus/backups/restore-<time>). gatewayd GET /api/export (list), POST /api/export (token). Settings card with Export button.
- Tests: backend/tests/test_torus_phase8_9.py 16/16 + testing-agent suite test_torus_iter7_export_audit.py 15/15 (iteration 7). Fixed after review: restore skips pk+manifest when no seal secret is inside, keygen regenerates a mismatched pair, gateway.token excluded from default export, airgapd fails closed when the manifest is missing, export lock + unique names, `torus seal <sub> <file>` and `torus export check`. Catalog in backend/phases.py (manual Phase 8/9 steps) left as is.

### 2026-10-26 · Auto-route, dossier find, topic diff, weekly rollup
- Auto-route (llm.toggle `auto_route`, default false; Settings toggle; `torus llm auto on|off`): in gatewayd.step_answer a follow-up ≤ 240 chars with no new sources and no hand-picked model goes to the haiku tier when the LLM is on; the job log records it. followups[] carry `new_sources`.
- Dossier find: GET /api/vault/find?q=&dir=all|answers|inbox|topics → paragraph hits {rel, dir, title, idx, para, score, all} (same blank-line split as md()); Vault "Find in dossiers" with single-pass unicode highlighting; #/doc/<rel>?p=<idx> highlights + scrolls to the paragraph (doc-hit). Reader no longer repeats the H1.
- oracled: per-seed sentence store var/oracle/<slug>/<urlhash>.txt → results[].diff {added, removed, n_added, n_removed, first} + diff_when (kept visible after unchanged refills); history.jsonl (change/refill entries); digest "What changed"; weekly rollup vault/topics/<slug>-week-<ISO week>.md rewritten after each refill (grouped per seed, newest first, refills/changes counts). UI: oracle-diff details (+ green / − red), "This week" link.
- md(): headings followed by text in the same block, ordered lists, italics, bare-URL autolinks. Settings validation: auto_route must be a bool.
- Tests: backend/tests/test_torus_autoroute_diff.py 6/6 (local changing page on :8098) + testing-agent test_torus_iter10_t1.py 7/7 (iteration 10); all torus suites green. Fixed after review: rollup headings swallowed the lines, Find highlight markup, string auto_route, hint wording.

## Backlog (after 2026-10-26)
- P1: on the S26 run `torus update && torus install 6 7`, then `torus llm verify` with the real key (the fake list in tests is a stand-in).
- P2: Mongo use by the daemons, multi-device session roster, amber-high-contrast theme, replace HMAC manifest in the web app with the ML-DSA seal.
- Later (user intent): SaaS variant — hosted mesh per tenant, Stripe metered billing on tokens (llmd already logs usage), credit wallet with hard stop.

### 2026-10-26 · Phase 7 oracled + catalog sync
- oracled.py (Phase 7, `torus install 7`): reads etc/pinned.yaml (PyYAML if present, else a built-in parser for the same subset), every `every_minutes` (etc/oracle.conf, keep; default 60, enabled/fresh/start_delay_seconds) asks fetcherd to re-fetch each seed (fresh=true bypasses the 24 h cache), extracts + TextRank key sentences, writes vault/topics/<topic>.md, state in var/oracle.json, log logs/oracle.jsonl, emits torus.oracle.done. NATS: torus.oracle.status|run|reload. Resumes schedule across restarts (next_run from last_run); one refill at a time.
- gatewayd: GET /api/oracle (falls back to file state + daemon:"down" when oracled is silent), POST /api/oracle/run {topic?}, GET/POST /api/oracle/config {yaml} (validated, atomically rewritten). Vault tab card: schedule, per-topic/per-seed status, Refill all / Refill this topic, YAML editor.
- CLI: `torus oracle [status|run [topic]|topics|edit|every <min>|on|off]` (torusctl oracle status|run|check; conf oracle).
- backend/phases.py: Phase 7/8/9 catalog rewritten around `torus install 7/8/9` (coderd/oracled usage, seal + 13-check audit + hardening.conf, export/check/restore); opsec labels match torus_audit.py. 10 opsec items kept.
- Fork housekeeping: backend/.env + frontend/.env were missing after the fork and were recreated (local Mongo, preview URL). The web app's React frontend is not served correctly in this environment (supervisor runs `expo start` against a CRA app) — platform issue, flagged to user.
- Tests: backend/tests/test_torus_oracle.py 15/15 + testing-agent test_torus_oracle_t1.py 18/18 + backend_test.py 33/33 (iteration 8); UI flows verified on the phone page. Fixed after review: ok:false refusals surfaced as toasts, Refill button disabled while running, open topic rows survive refresh, single poll timer, empty-list save asks first, link contrast + 44 px rows.

### 2026-10-26 · Model-id verify, Haiku tier, Dossier reader, audit badge, topic alerts
- torus_models.py (phase 6 file): tiers sonnet/opus/haiku (MODEL_ALIASES gained haiku → claude-haiku-5-5), `check(models)` against Anthropic's live /v1/models (llmd torus.llm.models now returns {id, display_name, created_at}), `fix()` points stale tiers at the newest id of the family, report saved to var/models.json. Tier ids must belong to their family. gatewayd: GET/POST /api/llm/verify {fix}, POST /api/llm/models; settings payload has tiers{}/haiku_id; /api/settings validates everything before writing and accepts sonnet_id/opus_id/haiku_id; model=haiku accepted by ask/followup/rerun/code. CLI `torus llm verify [--check-only]`, `torus llm model|id` take haiku; `torus llm models` shows display names. Settings card "Claude model ids" (status per tier, Verify button, dropdown per tier). Haiku options in Ask/follow-up/re-answer/Code.
- Dossier reader (phone page): GET /api/vault/docs?dir=answers|inbox|topics (+counts), GET /api/vault/doc?path=<dir>/<file>.md (frontmatter → meta, inbox summary attached, path-safe, 400 KB cap). Vault tab "Dossiers" card with folder toggle; route #/doc/<rel> renders markdown (md() now does ordered lists, bare-URL autolinks, italics), [n] citations highlight + scroll to the Sources list item, open-job link.
- Audit badge: /api/health.audit_fail (from var/audit.json) → amber count on the Settings nav link.
- Topic alerts: oracled hashes each seed's text; results carry hash/changed, topics changed/changed_at, digest marks changed seeds and says "N changed since the last refill"; Vault tab shows an amber "N new" pill until the topic row is expanded (localStorage torus_oracle_seen); "Read digest" opens the digest in the reader. `torus oracle` prints "(changed)".
- Tests: backend/tests/test_torus_models_docs.py 19/19 + testing-agent test_torus_iter9_t1.py 20/20; all torus suites green (iteration 9). Fixed after review: link colour in the reader, long URLs wrapping, settings atomic validation, family check on tier ids, dotfiles in counts, fix flag as string, ordered Sources list, bigger cite tap targets, bytes under 1 KB, stale "model id in use".
- fake_llm.py answers torus.llm.models with a canned list; `FAKE_LLM=1 run_local_mesh.sh start` swaps it in for llmd.
