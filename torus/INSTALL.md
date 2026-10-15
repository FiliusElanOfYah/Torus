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
Phases 0 to 7 run in order and stop at the first real problem.
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

## Step 7 (optional): Brave Search

```
torus key set brave
torus search mode both
```
Key: https://api-dashboard.search.brave.com. Modes: `urls` (only what you paste),
`brave` (search only), `both`. You can also switch it per question on the web page.

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

---

## Every day

| Want to... | Run |
|---|---|
| get the newest version | `torus update` (pulls from GitHub, then installs all) |
| see what's running | `torus status` |
| read a service log | `torus logs gatewayd` (`-f` to follow) |
| undo the last install | `torus backups`, then `torus rollback <stamp>` |
| check after a reboot | `torus doctor` |

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
| 7 | coder: write, run and fix code in your projects | coderd |
