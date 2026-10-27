# TORUS Operator: the agent works inside Termux, you approve

Claude becomes a hands-on operator on the phone: it reads, runs, edits and installs inside Termux through TORUS, in phases you approve from a summary.
GitHub and file uploads follow as the next build; the uploads change to larger files and zips kept intact.

## Who it's for

The owner, directing analysis and code changes from the phone with taps and short instructions instead of typing commands.

## Core features and experience

**Phase 1 (built now): Operator mode**
- **Operator tab** on the phone page and `torus op "task"` in Termux. A task is anything: "analyse the CSVs in Downloads and chart the monthly totals", "add a retry to fetcherd", "update all packages and re-run the audit".
- **Plan first**: the agent answers with a phased plan: what it will read, what it will change, what it will run, and the estimated Claude cost. One tap approves the plan; the phases then run one after another.
- **Approvals, as chosen**: reads (files, folders, logs, `git status`, inspection commands) run by themselves. Changes inside the approved plan (edits, new files, installs, commits) run without extra taps; at the end of each phase the agent posts a summary and the diff, and the next phase starts unless "pause between phases" is on. Anything outside the plan, or on the danger list (deleting folders, `pkg uninstall`, force-push, touching keys/seal, anything outside the Termux home or shared storage), always stops for a tap, every time.
- **Full Termux reach (as the Termux user, no root)**: shell with the real PATH and environment, every file under the Termux home and shared storage, pkg/pip installs, git with the stored GitHub token, TORUS's own commands. Each phase runs in a workspace folder with before/after snapshots so **Undo phase** restores files in one tap.
- **Phone features through Termux:API** (Termux:API app from F-Droid + `pkg install termux-api`): clipboard in/out, finish notifications, battery/Wi-Fi/device info, opening results in other apps, camera photo, microphone recording, SMS send/read, location, contacts. Each asks Android's permission the first time it is used, with a one-line reason; a denied permission disables that tool only.
- **Live view**: streaming log of commands and outputs, files changed with diffs, tokens and dollars so far, Stop button. Finished tasks are saved as dossiers (plan, log, diff, summary) in the Vault and searchable.
- **Cost guardrails**: per-task budget (default $1, editable at approval), the existing daily cap, Sonnet by default with Opus selectable, long outputs trimmed before they reach Claude. Secrets never reach Claude: the redaction layer already in TORUS applies to every tool result, and key files are unreadable to the agent.
- **Honest limits**: no root, so system settings, other apps' private data and Android itself are out of reach. Background execution survives screen-off only while Termux holds its wake lock (TORUS acquires it for the task). Camera and mic through Termux:API are single shots/recordings, not live streams.

**Phase 2: GitHub read + uploads**
- `torus key set github`; paste repo/file/folder/issue links as sources; cited like web pages; private repos work.
- Attach files on Ask and Upload to Vault: PDF, text/Markdown/HTML/CSV/code, .docx up to **100 MB per file**; **.zip stored intact** (not extracted) with its file list indexed, so it can be opened later by the Operator or unpacked on request; 500 MB per upload request. Files never leave the phone.

**Phase 3: GitHub write + reactions + Console agent**
- Vault backup to a private repo; Coder/Operator commits on a branch with a pull request; webhooks over a Cloudflare Tunnel to guardiaios.app; the Console-built Claude agent wired in if the API can address it, otherwise its prompt becomes a TORUS persona.

## User flow

1. Once: install Termux:API from F-Droid, run `torus install 10` (adds the operator daemon and termux-api), grant Android permissions as they are first needed.
2. Operator tab → type the task → **Plan** → read the phased summary and cost → **Approve** (optionally edit budget, toggle pause-between-phases).
3. Watch the live log; at each phase end read the summary/diff; tap only when something is outside the plan or on the danger list.
4. Done: notification on the phone, result on the clipboard if asked, dossier in the Vault, **Undo phase** available for a day.

## UI/UX feel

Same phone page. The Operator tab is a vertical timeline: plan card on top, one card per phase (pending / running / done / needs you) with a collapsible log, a sticky bottom bar with Stop, cost so far and the single approval button when one is needed. Approval requests read as one sentence in plain words plus the exact command.

## Implementation phases

- Phase 1 (now): Operator mode end to end: operator daemon with tool-use through the existing Claude path, approval states, snapshots/undo, Termux:API tools, Operator tab, `torus op`, Phase-10 installer, audit lines, INSTALL.md step. Built once, in one pass.
- Phase 2: GitHub read + uploads with the new limits.
- Phase 3: GitHub write, webhooks, Console agent.

## Assumptions

- Approval model is the hybrid described: plan approved once; reads automatic; in-plan changes automatic with a summary per phase; danger list and out-of-plan steps always ask. A Settings switch flips to "tap for every change" when wanted.
- No root; the agent is the Termux user. Key files, the seal and the gateway token are excluded from its reach, and tool output passes through redaction before Claude sees it.
- Default model Sonnet, per-task budget $1, daily cap applies; the agent stops with a summary when either is reached.
- Termux:API covers clipboard, notifications, device info, open-with, camera, mic, SMS, location, contacts; Android permission is requested at first use of each, never up front.
- Undo keeps phase snapshots for 24 hours in the Termux home (not shared storage) and skips files over 50 MB.
- Uploads (Phase 2): 100 MB per file, 500 MB per request, zips kept intact and listed; OCR still deferred.
