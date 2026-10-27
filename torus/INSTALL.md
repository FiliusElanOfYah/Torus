# TORUS: step-by-step install on your phone (Termux)

Every step is one paste into Termux. Every step is **safe to run again**.
Nothing is ever appended (`cat >>`). Files are written whole, and the old copy
goes to `~/.torus/backups/<time>/` first, so you can always undo.

---

## Step 1: Back up what already works (once)

```
termux-setup-storage
tar --exclude=.torus/venv --exclude=.torus/mongo/data --exclude=.torus/jetstream -czf ~/storage/downloads/torus-before-$(date +%Y%m%d).tar.gz -C ~ .torus
```
You now have `Downloads/torus-before-<date>.tar.gz` (visible in Material Files).

## Step 2: Download TORUS (once; it updates itself after that)

```
curl -fsSL https://raw.githubusercontent.com/FiliusElanOfYah/Torus/main/torus/bootstrap.sh | bash
```
This clones the repo into `~/.torus/src` and adds the `torus` command.

## Step 3: Check the phone

```
torus doctor
```
It lists **OK / WARN / FAIL** for packages, Python, every file, every service,
ports, keys and switches. Each FAIL line tells you the exact command that fixes it.

## Step 4: Install or repair everything

```
torus install all
```
Phases 0 to 9 run in order and stop at the first real problem.
- Your working `nats-server` and `torus-mongo` run scripts, `nats.conf`,
  `llm.toggle` and `search.conf` are marked **yours** and are left alone.
- The Python daemons are replaced by the fixed versions: indexerd removes
  duplicate rows, parserd and indexerd have error handling, logs rotate at 1 MB,
  and the Phase 6 web page is added.

To do one phase at a time: `torus install 3`, then `torus install 4`, and so on.

## Step 5: Test the pipeline

```
torus selftest ingest
```
`OK dossier written: ...` means fetch → parse → index → vault all work.

## Step 6: Add Claude (your own Anthropic key, no Leo quota)

```
torus key set anthropic
torus llm model sonnet
torus llm on
torus llm test
```
Get a key at https://console.anthropic.com/settings/keys. It is typed hidden and
stored in `~/.torus/airgap/anthropic.key` (mode 600, read only by airgapd).
- Opus for harder or code questions: `torus llm model opus`, or pick it for a single question.
- If `torus llm test` says the model id is wrong: `torus llm models` lists the
  valid ids, then `torus llm id sonnet <id>`.
- If Claude is rate-limited, TORUS waits and retries. If it still fails, you get
  a local summary instead of a hard stop.

### Model ids (which Claude answers)

TORUS uses three tiers: **sonnet** for answers and follow-ups, **opus** for code and hard
questions, **haiku** when speed and cost matter more than depth. Anthropic retires model ids
now and then, so once in a while (or when Claude says "model not found"):
```
torus llm verify        # asks Anthropic which models your key can use, points each tier at the newest
torus llm model haiku   # change the default tier;  torus llm id opus <exact-id>  pins one by hand
```
The Settings tab has the same: **Claude model ids → Verify model ids**, with a dropdown per tier.

**Saving money:** `torus llm auto on` (or Settings → *Auto-route to Haiku*) sends short
follow-ups (up to 240 characters, no new URLs) to Haiku; first answers, anything with a model
you picked by hand, and follow-ups that add sources keep the default tier. The job log says
when it happened.

## Step 7 (optional): Brave Search

```
torus key set brave
torus search mode both
```
Key: https://api-dashboard.search.brave.com. Modes: `urls` (only what you paste),
`brave` (search only), `both`. You can also switch it per question on the web page.

## Step 7b (optional): Cloudflare AI Gateway

Puts Cloudflare's free proxy between TORUS and Anthropic. **Your Anthropic key still does the
paying.** You gain: a spend dashboard (every call, model, tokens, cost), caching (an identical
prompt sent again costs $0), and rate/budget caps so a runaway loop cannot drain your balance.
You give up: prompts and answers transit Cloudflare (+30-80 ms), so `torus audit` shows a
**WARN** on check *extra-4* while it is on. Honest default: skip it until you want the dashboard.

1. Cloudflare dashboard -> **AI** -> **AI Gateway** -> **Create** (e.g. name `torus`).
   Note your **Account ID** (dashboard URL or the gateway's "API endpoint") and the gateway name.
   In the gateway settings turn **off** "log request/response body" if you want privacy;
   turn **on** "Authenticated Gateway" if you want it to refuse calls without a token.
2. In Termux:
   ```
   torus llm gateway <account_id> torus      # route Claude calls through the gateway
   torus key set cloudflare                  # only if "Authenticated Gateway" is on (token needs AI Gateway: Run)
   torus llm test                            # one real call; it now shows up in the Cloudflare dashboard
   torus llm gateway status | off            # see it, or go straight to Anthropic again
   ```
`torus llm verify` (the model list) always goes directly to Anthropic; only message calls use
the gateway. `torus llm off` still stops all Claude spend instantly.

### Spending less on Claude

- **See it**: the Ask tab shows today's and this month's Claude cost (estimate) at a glance; each
  answer and follow-up shows what it cost; Settings → *Claude spend* breaks it down by tier.
  `torus llm spend` prints the same in Termux. Prices live in `~/.torus/etc/prices.conf` (list
  prices copied from Anthropic; edit if they change) — the bill at console.anthropic.com is the truth.
- **Cap it**: `torus llm cap 2.50` (or Settings → *Daily cap*). Once today's estimate reaches the
  cap, llmd refuses every Claude call (answers, follow-ups, Coder) until local midnight; TORUS
  falls back to the local summary and says so on the job. `torus llm cap off` removes it.
- `torus llm auto on` (Haiku for short follow-ups, ~20x cheaper than Sonnet).
- Pinned-topic refills (`oracled`) **never call Claude**: they fetch, hash, TextRank and diff
  locally. Refill as often as you like; it costs bandwidth, not money.
- `torus llm off` whenever you do not need Claude: fetch, parse, index, vault and coder all work
  offline for free.
- Short, specific questions with URLs: TORUS summarises locally first and sends only the digest.
- With the gateway on: enable caching and set a daily request cap in its settings. Cached answers
  are marked *cached, free* on the job.

### Claude traffic path (Settings card)

Settings → *Claude traffic path* shows whether calls go **Direct to Anthropic** or **Via Cloudflare
gateway** and switches with one tap. The first time you pick Cloudflare it asks for the Account ID
and gateway name; after that both are remembered, so switching back and forth is one tap.
Same as `torus llm gateway on | off | status`.

## Step 8: Use it

```
torus open
```
Brave opens **http://127.0.0.1:7777**. Tip: add it to your home screen.
Or from the terminal:
```
torus ask "what is a torus" https://en.wikipedia.org/wiki/Torus
```
Answers are saved to `Downloads/vault/answers/`. Fetched pages go to `Downloads/vault/inbox/`.

### When a job shows **Needs you**
A site wanted a human check (CAPTCHA, login wall, or a page that needs JavaScript).
TORUS never tries to get around these.
1. Tap **Open the page in Brave** and pass the check.
2. Select all the article text, copy it, and paste it into the box. Or save the
   page (Share, then Download page) and pick the file.
3. Tap **Use this page**, or **Skip**. The job continues by itself.

### Follow-up questions
When a job is done, scroll down to **Ask a follow-up**. It reuses the job's
sources (you can add URLs), and Claude sees the whole conversation.
In Termux: `torus follow <job-id> "how does that compare to a sphere?"`

### Citation map
Tap **Citation map** on a finished job. You get:
- the sources ranked by how many other sources link to them, plus how often the answer cites them
- who links to whom
- outside pages that 2+ sources point to. **Use in follow-up** adds one as a new source.

### PDF reports
**Save PDF to vault** writes `Downloads/vault/reports/<job>.pdf` (visible in
Material Files, ready to share). **Download PDF** saves a copy through Brave.
In Termux: `torus report <job-id>`. It uses the built-in PDF fonts, so non-Latin
characters show as `?`.

## Step 9: Coder (write, run and fix code)

```
torus install 7
torus open
```
Always open the page with `torus open`. It unlocks the **Code** tab with a
private token, so no other app on the phone can make TORUS write files or run commands.
1. Pick or create a project. Projects live in `~/projects`, which Acode can open
   through Termux. To change it: `torus code root <dir>`.
2. Say what to write or fix, and add a **run command** that checks it (for example `python main.py`).
3. Claude proposes **whole files**. You see a diff and choose **Apply**, **Ask for changes**, or **Reject**.
4. TORUS backs up every file it touches, writes the new ones and runs your command.
   If the command fails, the error goes back to Claude for a fix (up to the number
   of attempts you chose). Tick **Apply fixes without asking** to let it loop by itself.
5. **Undo all changes** puts every file back the way it was.

Claude can only propose file contents. Commands are only ever the ones you typed.
Without AI: `torus code run <project> "<command>"`, or **Quick run** on the page.

### Pinned topics (oracled, same `torus install 7`)

Topics you want kept fresh go in `~/.torus/etc/pinned.yaml` — a name and a list of
URLs each. Every hour (`every_minutes` in `etc/oracle.conf`) oracled re-reads every
URL: the pages go into your index and `vault/inbox` like a normal `torus ask`, and
one digest per topic lands in `vault/topics/<topic>.md` (what was read, when, key sentences).
```
torus oracle edit            # or the Vault tab -> Pinned topics -> Edit topics
torus oracle run             # refill now; torus oracle run <topic> for one
torus oracle                 # schedule, last/next run, every seed with ok/blocked/robots/empty
torus oracle every 120       # change the interval (minutes);  torus oracle off  pauses it
```
No restart is needed after editing the file. No AI is used; it is fetch + extract only.

### Reading what TORUS wrote (Vault → Dossiers)

Every answer, every page read and every topic digest is a Markdown file under `~/.torus/vault/`.
The **Vault** tab lists them (Answers / Pages read / Topics); tap one to read it on the phone
with headings, links and clickable `[n]` citations that jump to the source list. Pinned topics
show an amber **N new** badge when a refill found changed pages since you last looked.
**Find in dossiers** searches every answer, page and digest and opens the matching paragraph.
**Pin this source** under any source of an answer (or a Vault search result) makes that page a
seed of a pinned topic in one tap - pick an existing topic or name a new one; oracled reads it
right away and then on its schedule.
Unpin with the **×** next to a seed, or **Unpin topic**; digests already written stay in `vault/topics`.

Each pinned topic also gets, per refill, a **What changed** list (the sentences added and
removed on each page since the last refill) and one **weekly roll-up** page,
`vault/topics/<topic>-week-<year>-W<week>.md`, that collects every change of the week per
seed — the *This week* button on the topic row opens it.

## Step 10: Hardening (seal + audit)

```
torus install 8
```
- **Seal**: signs every API key file, the gateway token and every daemon with ML-DSA-65 (the
  post-quantum signature standard, FIPS 204). airgapd refuses to hand out a key
  whose file no longer matches the seal. `torus seal verify` prints the key
  fingerprint; write it on paper and compare now and then. `torus install` and
  `torus key set` re-seal by themselves.
- **Audit**: `torus audit` runs 15 checks (F-Droid build, no port on 0.0.0.0,
  key file modes, no key in logs, seal, log rotation, storage scope, Termux:Boot,
  package list, Claude off at boot, gateway token, Host-header guard, CRLF,
  Claude traffic path, Operator reach). Every FAIL names the fix. The same list is on the web page under **Settings**.
- **At boot**: Claude is switched off and rotated logs older than 7 days are
  deleted. Both switches live in `~/.torus/etc/hardening.conf`.

## Step 11: Backup and restore

```
torus install 9
torus export
```
`torus export` writes `~/storage/downloads/torus-<date>.tar.gz` plus `.sha256`
and `.sig`: your config, web page, daemons, vault, index, boot script, the pinned
package list (`etc/pkg.manifest`) and Python pins (`etc/requirements.freeze`).
API keys, the seal secret and the gateway token stay out unless you add `--with-keys`; `--no-vault` makes it small.
`torus export list` shows what you have. The **Settings** page has the same button.

On a new phone: install Termux + Termux:Boot from F-Droid, run the bootstrap line
from Step 2, then `torus restore ~/storage/downloads/torus-<date>.tar.gz`
(hash and signature are checked, overwritten files are backed up), then
`torus install all`, `torus seal`, `torus audit`. The full list is in
`~/.torus/docs/RESTORE.md` and inside every export.

## Step 12: Operator (Claude works inside Termux, you approve)

```
torus install 10
torus op "list the five biggest folders under ~/storage/downloads"
```
Also install **Termux:API** from F-Droid (same source as Termux) for the phone tools.

**What it is.** A task is anything you could do yourself in Termux: "analyse the CSVs in Downloads
and chart the monthly totals", "add a retry to fetcherd", "update all packages and re-run the
audit". Claude answers with a **plan** (phases: what it will read, change, run, which phone
features, rough cost). Nothing happens until you **Approve** (budget editable, default $1;
"pause between phases" and "tap for every change" optional). Then the phases run one after
another on the **Operator** tab (or `torus op` follows in the terminal).

**Approvals.**
- *Reads* (files, folders, logs, `git status`, inspection commands) run by themselves.
- *Changes inside the plan* (file writes, installs, commits, other commands) run without extra
  taps; every phase ends with a summary and the diff.
- *Danger list* — always a tap, every time: deleting files or folders, `pkg/pip uninstall`,
  force-push, `git reset --hard`, stopping or reinstalling TORUS services, anything naming keys
  or the seal, world-writable chmod, piping downloads into a shell, root, writes outside the
  Termux home / shared storage, commands whose program is not in the approved plan, SMS,
  camera, microphone.
- Settings → *Operator: tap for every change* (or `torus op strict on`) makes every change ask.

**Reach.** The agent is the Termux user: the real PATH and environment, every file under the
Termux home and shared storage, pkg/pip, git, TORUS's own commands. No root, so Android itself,
system settings and other apps' private data are out of reach. `~/.torus/airgap` (keys, seal),
`gateway.token` and the snapshot folder are unreadable to it, and every tool output passes
through sentineld's redaction before Claude sees it.

**Phone tools** (Termux:API): clipboard in/out, finish notification, battery / Wi-Fi / device
info, open a result in another app, camera photo, microphone recording, SMS send/read,
location, contacts. Android asks for each permission the first time it is used; a denied
permission switches that tool off for the task (allow it later under Android Settings → Apps →
Termux:API → Permissions). Camera, microphone and SMS-send always ask you first as well.

**Undo.** Files the agent writes or deletes are copied first (snapshots, 24 h, up to 50 MB each)
so **Undo phase** restores them in one tap once the task is stopped or done. Files changed by
shell commands are listed on the phase but cannot be restored — the agent is told to edit
through the write tool for that reason.

**Cost.** Per-task budget (default $1, `torus op budget 2`), the daily cap (`torus llm cap`) and
Sonnet by default (Opus/Haiku selectable). Outputs are trimmed to 6 000 characters before they
reach Claude. The task stops with a summary when a limit is reached.

**Honest limits.** Background work survives screen-off only while Termux holds its wake lock
(TORUS takes it for the task; `termux-wake-lock`). Camera and microphone are single shots, not
streams. Finished tasks become dossiers in Vault → Answers (plan, log, diffs, summaries).

`torus op list | show <id> | follow <id> | yes <id> | no <id> | stop <id> | resume <id> | undo <id> <phase>`.

---

## Every day

| Want to... | Run |
|---|---|
| get the newest version | `torus update` (pulls from GitHub, then installs all) |
| see what's running | `torus status` |
| read a service log | `torus logs gatewayd` (`-f` to follow) |
| undo the last install | `torus backups`, then `torus rollback <stamp>` |
| check after a reboot | `torus doctor` |
| security check | `torus audit` |
| back everything up | `torus export` |

## Changing code yourself (Acode)

Edit the **repo copy** in `~/.torus/src/torus/files/...`, never `~/.torus/bin/`
(that copy gets rewritten). Then run `torus install <phase>`. Windows line endings
from editors are stripped automatically. `torus files` shows what differs.

## Phases

| # | What | Services |
|---|---|---|
| 0 | packages, folders, venv, shared code, `torus` command | none |
| 1 | NATS bus, runit, Termux:Boot | nats-server |
| 2 | vault in Downloads, Mongo | torus-mongo |
| 3 | ingest | fetcherd, parserd, indexerd, vaultd |
| 4 | summaries | summarizerd |
| 5 | keys, redaction, Claude | airgapd, sentineld, llmd |
| 6 | web page, `torus ask`, follow-ups, citation map, PDF reports | gatewayd |
| 7 | coder: write, run and fix code in your projects; oracled: refills pinned topics on a schedule (`torus oracle`) | coderd, oracled |
| 8 | hardening: ML-DSA-65 seal, `torus audit`, Claude off at boot, log pruning | none |
| 9 | backup: `torus export` / `torus restore` (signed tarball) | none |
| 10 | Operator: Claude plans, you approve, it reads/edits/runs/installs inside Termux with undo per phase; Termux:API phone tools (`torus op`) | operatord |
