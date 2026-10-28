# TORUS Workbench: finish your projects from the phone, with Claude as a peer

A project workbench inside TORUS: a project database of your code, notes and conversations that both you and Claude read and write; threads with Claude that pick up where the last session ended; a file manager and editor; commits and pull requests to GitHub.
Utilitarian and logistical: no voice, no sharing, no widgets, no income model. One device, typed input, full logs, no storage cap.

## Who it's for

The owner, finishing already-started SaaS/PaaS builds exclusively from a Samsung Galaxy S26 Ultra in Termux — complex, multi-session work where nothing may be forgotten between sessions.

## Core features and experience

**Phase 1 (built now)**

- **Projects**: register a folder on the phone or clone a GitHub repo. The project page shows branch and `git status`, the code catalog, open threads, Operator tasks, pull requests, and the session summaries.
- **Project database** (on-device, in the project's own folder, no size cap):
  - **Code catalog**: one entry per folder and per file — purpose, key functions/classes, dependencies, last change, open issues, agent notes. Claude fills it on first scan and updates entries it touches; you can edit any entry. Viewable as a tree in the app and as plain Markdown files, searchable.
  - **Agent notes**: everything Claude observes, decides or tries, timestamped, tied to the file or thread it concerns. Nothing is discarded.
  - **Conversation log**: the full text of every thread turn (yours and Claude's), the commands run and their output, timestamped.
  - **Session summaries**: at the end of each session (you tap *End session*, or after 6 hours idle) Claude writes a summary: what was done, key notes, priorities, need-to-knows for the next session, and open questions. The next session starts with the latest summary and the catalog, so nothing is re-explained.
  - **Archive**: the full agent notes and conversation text of each session are compressed and filed under `<project>/.torus/log/<project>-<timestamp>/` with an index; any archived session can be opened and searched from the app or pulled back into a thread for context.
- **Workbench thread** (per project): you and Claude research, analyse, plan and write together. Claude reads project files, the catalog, web pages and GitHub links you paste; answers with text, plans and **change cards** (one file diff each). Nothing is written until you tap **Apply** (or **Apply all**); **Run** on a command card executes it and shows the output; "do it end to end" hands the plan to the Operator. Cost per turn and per thread is shown.
- **Files tab**: file manager for the Termux home, shared storage and projects: folders, sizes, age, search by name and content, new file/folder, rename, move, duplicate; delete asks; open in another Android app. **Editor**: files up to 1 MB open whole; larger files (no cap) open in pages of ~1 MB with a page bar, find-in-file, and save per page — bigger whole-file editing is not practical on a phone browser, pages are. Any file can be attached to a thread or to Ask in one tap.
- **Terminal card**: command box with history and saved per-project run-buttons; output shown in place; the danger list still asks; every error block has **Explain**, which asks Claude in context and logs the answer as an agent note.
- **GitHub read**: `torus key set github`; paste repo/file/folder/issue/PR links as sources in Ask or a thread; private repos work; clone a repo as a project.
- **GitHub write**: **Commit** applied changes on a `torus/<short-task>` branch and **Open pull request** with Claude's summary; both always ask for a tap; pushing to `main`/`master` is on the danger list. PR links land on the project page and in the session summary.
- **Operator, project-aware**: tasks start in the project folder, read the latest summary and catalog first, and write their notes and summary into the database.
- **Unchanged and kept**: Brave Search (`torus key set brave`, modes urls / brave / both, also usable from threads as a source), Ask, Jobs, Vault, pinned topics, Code, spend meter, daily cap, Cloudflare gateway option, seal and audit.
- **Housekeeping from the last test round**: read-only-looking shell commands that change files (`find -delete`, `sed -i`, `sort -o`, subshells, `&`, newlines) count as changes; a settings save with one bad field saves nothing; Operator dossiers link back to the task; the top navigation fits a 390 px screen with 44 px targets.

**Phase 2**
- Scheduled and recurring tasks and questions, results into the database.
- Project database + Vault backup to a private GitHub repo; `torus update` pulls the latest TORUS from your repo and reinstalls.
- Uploads: 100 MB per file, 500 MB per request, zips kept intact with a file list.

**Phase 3**
- Anything that becomes necessary while finishing the builds — added as it appears, nothing pre-planned.

## User flow

1. Once: `torus install 11`, `torus key set github`, **Projects → Add** (folder or repo URL). Claude scans the project and fills the catalog (you approve the cost first).
2. Open the project → the latest session summary and priorities are on top → **New thread** → describe the bug or feature → Claude reads the catalog and files, proposes a plan and change cards.
3. **Apply** the cards you agree with, **Run** tests from the terminal card, **Explain** anything cryptic; iterate in the same thread.
4. **Commit → Open PR** (two taps). Tap **End session**: Claude writes the summary, the full log is compressed and filed, the catalog entries it touched are updated.
5. Next session opens with the summary; archived sessions are one tap away when a detail is needed.
6. Termux mirrors: `torus project`, `torus thread`, `torus files`, `torus gh`, `torus session end`.

## UI/UX feel

Same phone page, two new tabs: **Projects** (list → project page: summary on top, then catalog tree, threads, tasks, PRs, archive) and **Files** (breadcrumb bar, rows with size/age, long-press menu, full-screen monospace editor with sticky Save and a page bar for big files). Threads are chat bubbles with change cards (diff, Apply/Skip) and command cards (Run, output, Explain), sticky composer with attach-file / paste-link, cost in the header. Dense, readable, thumb-reachable; approvals are one sentence plus the exact command.

## Implementation phases

- Phase 1 (now): Projects, project database (catalog, notes, conversation log, session summaries, compressed archive), Workbench threads with change/command cards, Files tab with paged editor, Terminal card with Explain, GitHub read + clone, Commit + PR, project-aware Operator, the four housekeeping fixes, CLI mirrors, phase-11 installer, INSTALL.md step. One build pass, one test round.
- Phase 2: schedules, backups and self-update, large uploads.
- Phase 3: on demand.

## Assumptions

- The database is plain files inside the project (`<project>/.torus/`: `catalog/` mirroring the folder tree, `notes.jsonl`, `threads/`, `sessions/`, `log/`), so it is versioned with the repo when you want it and readable without TORUS. Archive compression is gzip; no deletion ever happens automatically.
- The first catalog scan is done by Claude file by file (Haiku for small files, Sonnet for large or central ones) with a cost estimate shown before it starts; files over 200 KB, binaries, vendored folders and `node_modules`-type folders are listed but not read.
- A session is one project's work between opening it and *End session* (or 6 hours idle). Summaries are written by Sonnet; you can edit them.
- GitHub access is a fine-grained personal access token (Contents read/write, Pull requests read/write, Issues read, Metadata) stored with `torus key set github`; it never leaves the phone and is injected into `git push` per command. Commit author is your git identity; TORUS asks once if git has none.
- Change cards write whole files; a card is skipped if the file changed on disk since Claude read it, and you are told why. Applied changes are snapshotted so Undo works like Operator phases.
- The terminal card runs what you type without read/change classification (you typed it); the danger list and protected paths still apply.
- Claude: Sonnet for drafting, Haiku for short follow-ups (existing auto-route), per-thread budget default $2, daily cap applies; cost shown per turn.
- Credit discipline: one implementation pass, one testing-agent round, fixes folded in, no polish rounds unless something is broken.
