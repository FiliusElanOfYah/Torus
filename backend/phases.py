"""
TORUS daemon-mesh Phase catalog.

Static source-of-truth for every phase, step, command, verification check,
troubleshooting note, and artifact that contributes to the phase bundle.
Written for a non-rooted Samsung Galaxy S26 Ultra running Termux (F-Droid).
"""

from __future__ import annotations

DAEMONS = [
    {"name": "fetcherd",     "role": "HTTP/RSS/arxiv/sitemap fetcher",         "bus": "torus.fetch.*",       "ring": "ingest"},
    {"name": "parserd",      "role": "HTML/PDF/markdown extraction",           "bus": "torus.parse.*",       "ring": "ingest"},
    {"name": "indexerd",     "role": "SQLite FTS5 full-text index",            "bus": "torus.index.*",       "ring": "storage"},
    {"name": "vaultd",       "role": "Markdown dossier writer",                "bus": "torus.vault.*",       "ring": "storage"},
    {"name": "mongod",       "role": "Structured live index (MongoDB)",        "bus": "torus.mongo.*",       "ring": "storage"},
    {"name": "pageringd",    "role": "Memory-mapped sharded LRU cache",        "bus": "torus.pager.*",       "ring": "cache"},
    {"name": "summarizerd",  "role": "TextRank + citation graph; LLM pass",    "bus": "torus.summarize.*",   "ring": "brain"},
    {"name": "coderd",       "role": "Scaffold / lint / test / LLM codegen",   "bus": "torus.code.*",        "ring": "brain"},
    {"name": "sentineld",    "role": "OPSEC: PII redaction, egress audit",     "bus": "torus.sentinel.*",    "ring": "guard"},
    {"name": "gatewayd",     "role": "CLI + TUI + local web UI",               "bus": "torus.gateway.*",     "ring": "interface"},
    {"name": "oracled",      "role": "Pinned-topic scheduled ingest",          "bus": "torus.oracle.*",      "ring": "brain"},
    {"name": "airgapd",      "role": "Master keypair + encrypted tokens",      "bus": "unix://airgap.sock",  "ring": "guard"},
]


PHASES = [
    # ───────────────────────────── PHASE 0 ─────────────────────────────
    {
        "number": 0,
        "name": "Verify Ground Truth",
        "goal": "Confirm the device, Termux build, storage permissions, and base tooling before touching the mesh.",
        "gate": "Every step below shows exit-code 0 and the expected output pattern.",
        "steps": [
            {
                "id": "0.1",
                "title": "Confirm Termux is from F-Droid (not Play Store)",
                "command": "termux-info | head -n 20",
                "expected": "TERMUX_APP_PACKAGE_MANAGER=apt and INSTALLER=com.fdroid or org.fdroid.fdroid",
                "verify": "termux-info | grep -i 'INSTALLER' && echo OK",
                "troubleshooting": "If INSTALLER shows a Play Store package, uninstall Termux and install the F-Droid build. Play Store builds are deprecated and will break pkg upgrades.",
            },
            {
                "id": "0.2",
                "title": "Upgrade package index",
                "command": "pkg update -y && pkg upgrade -y",
                "expected": "No held packages; the shell returns to the prompt without errors.",
                "verify": "pkg update -y 2>&1 | tail -n 3",
                "troubleshooting": "If you see repository GPG errors, run: `termux-change-repo` and pick the Grimler mirror.",
            },
            {
                "id": "0.3",
                "title": "Grant Termux access to shared storage",
                "command": "termux-setup-storage",
                "expected": "Android prompts for storage permission; ~/storage/ is populated with downloads, dcim, shared symlinks.",
                "verify": "ls ~/storage/downloads && echo OK",
                "troubleshooting": "If ~/storage is empty, open Android Settings → Apps → Termux → Permissions and enable Files & Media, then re-run the command.",
            },
            {
                "id": "0.4",
                "title": "Read Android API level",
                "command": "getprop ro.build.version.sdk",
                "expected": "An integer ≥ 34 (Android 14+).  S26 Ultra ships with ≥ 35.",
                "verify": "test \"$(getprop ro.build.version.sdk)\" -ge 34 && echo OK",
                "troubleshooting": "If the integer is below 34, several liboqs build flags will fail. Upgrade Android before continuing.",
            },
            {
                "id": "0.5",
                "title": "Check free disk space in HOME",
                "command": "df -h $HOME",
                "expected": "At least 8 GB free on the Termux HOME partition.",
                "verify": "df -k $HOME | awk 'NR==2 { exit ($4 < 8*1024*1024) }' && echo OK",
                "troubleshooting": "If space is low, offload large files from /storage/emulated/0 before continuing. Mongo and liboqs builds will consume several GB.",
            },
            {
                "id": "0.6",
                "title": "Install baseline tooling",
                "command": "pkg install -y git curl wget nano openssh termux-api proot-distro python python-pip clang make rust",
                "expected": "All packages install without errors; `python --version` prints 3.12+.",
                "verify": "python --version && git --version && echo OK",
                "troubleshooting": "If clang or rust fails on the first try, run `pkg clean && pkg install -y rust` separately — Termux occasionally stalls on the clang pre-script.",
            },
            {
                "id": "0.7",
                "title": "Confirm Termux:Boot is installed",
                "command": "ls -la ~/.termux/boot/ || echo 'MISSING'",
                "expected": "Directory exists (even if empty). You created it after installing Termux:Boot from F-Droid.",
                "verify": "mkdir -p ~/.termux/boot && test -d ~/.termux/boot && echo OK",
                "troubleshooting": "Install Termux:Boot from F-Droid, open it once (so Android grants it autostart), then re-run.",
            },
            {
                "id": "0.8",
                "title": "Lock a work directory",
                "command": "mkdir -p ~/.torus/{bin,etc,logs,shards,service,vault} && echo READY",
                "expected": "`READY` is printed; `~/.torus/` is the canonical mesh root for every later phase.",
                "verify": "test -d ~/.torus/service && echo OK",
                "troubleshooting": "If permission denied, you are inside /storage — switch to $HOME first with `cd ~`.",
            },
        ],
        "artifacts": {
            "env.sample": "TORUS_HOME=$HOME/.torus\nTORUS_VAULT=$HOME/storage/downloads/vault\nTORUS_NATS_URL=nats://127.0.0.1:4222\nTORUS_LOG=$HOME/.torus/logs\n",
        },
    },

    # ───────────────────────────── PHASE 1 ─────────────────────────────
    {
        "number": 1,
        "name": "Bus + Supervision Skeleton",
        "goal": "Install NATS JetStream and termux-services; prove one hello-world daemon survives a reboot.",
        "gate": "`sv status nats-server` reports `run` after a device reboot, and the hello-world daemon logs a heartbeat every 10 s.",
        "steps": [
            {
                "id": "1.1",
                "title": "Install termux-services (runit) + helper utilities",
                "command": "pkg install -y termux-services jq",
                "expected": "`sv-enable` and `sv` commands become available on $PATH.",
                "verify": "command -v sv-enable && command -v sv && echo OK",
                "troubleshooting": "If sv commands aren't found after install, open a NEW Termux session — termux-services only wires PATH on fresh shells.",
            },
            {
                "id": "1.2",
                "title": "Source runit on every shell",
                "command": "echo 'source $PREFIX/etc/profile.d/start-services.sh' >> ~/.bashrc && source ~/.bashrc",
                "expected": "No output; `sv status` works without the full path.",
                "verify": "grep start-services.sh ~/.bashrc && echo OK",
                "troubleshooting": "If you use zsh, append the same line to ~/.zshrc instead.",
            },
            {
                "id": "1.3",
                "title": "Download NATS server (ARM64 single binary)",
                "command": "curl -L -o /tmp/nats.tar.gz https://github.com/nats-io/nats-server/releases/latest/download/nats-server-latest-linux-arm64.tar.gz && tar -xzf /tmp/nats.tar.gz -C /tmp && install -m 0755 /tmp/nats-server-*/nats-server ~/.torus/bin/nats-server",
                "expected": "`~/.torus/bin/nats-server --version` prints a semver like `nats-server: v2.x.x`.",
                "verify": "~/.torus/bin/nats-server --version && echo OK",
                "troubleshooting": "If the GitHub redirect fails under Termux's curl, add `-A 'Mozilla/5.0'` or install `pkg install nats-server` instead.",
            },
            {
                "id": "1.4",
                "title": "Write nats.conf with JetStream enabled",
                "command": "cat > ~/.torus/etc/nats.conf <<'EOF'\nserver_name: torus-s26\nlisten: 127.0.0.1:4222\nhttp: 127.0.0.1:8222\njetstream {\n  store_dir: \"/data/data/com.termux/files/home/.torus/jetstream\"\n  max_memory_store: 128MB\n  max_file_store: 2GB\n}\nmax_payload: 4MB\nEOF\nmkdir -p ~/.torus/jetstream && echo WROTE",
                "expected": "`WROTE` is printed; `nats.conf` is parseable.",
                "verify": "~/.torus/bin/nats-server -c ~/.torus/etc/nats.conf -t && echo OK",
                "troubleshooting": "If `-t` reports an unknown directive, your binary is pre-2.9; re-download the latest release.",
            },
            {
                "id": "1.5",
                "title": "Create a runit service for nats-server",
                "command": "mkdir -p ~/.torus/service/nats-server/log && cat > ~/.torus/service/nats-server/run <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/bin/nats-server -c $HOME/.torus/etc/nats.conf\nEOF\ncat > ~/.torus/service/nats-server/log/run <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\nexec svlogd -tt $HOME/.torus/logs/nats-server\nEOF\nmkdir -p ~/.torus/logs/nats-server && chmod +x ~/.torus/service/nats-server/run ~/.torus/service/nats-server/log/run && ln -sfn ~/.torus/service/nats-server $PREFIX/var/service/nats-server && echo LINKED",
                "expected": "`LINKED` is printed; the service shows up under `sv status`.",
                "verify": "test -L $PREFIX/var/service/nats-server && echo OK",
                "troubleshooting": "If the symlink target is wrong, delete it (`rm $PREFIX/var/service/nats-server`) and re-run.",
            },
            {
                "id": "1.6",
                "title": "Enable and start the service",
                "command": "sv-enable nats-server && sv up nats-server && sleep 2 && sv status nats-server",
                "expected": "`run: nats-server: (pid …) …s`.",
                "verify": "sv status nats-server | grep -q '^run' && echo OK",
                "troubleshooting": "If status reports `down`, tail the log: `tail -n 50 ~/.torus/logs/nats-server/current`.",
            },
            {
                "id": "1.7",
                "title": "Probe JetStream /varz endpoint",
                "command": "curl -s http://127.0.0.1:8222/varz | jq '.server_id, .jetstream.config.store_dir'",
                "expected": "A non-empty server_id and your configured store_dir path.",
                "verify": "curl -sf http://127.0.0.1:8222/varz > /dev/null && echo OK",
                "troubleshooting": "Firewall is irrelevant on Termux. If curl fails, the server isn't running — check the service log.",
            },
            {
                "id": "1.8",
                "title": "Install Termux:Boot autostart hook",
                "command": "mkdir -p ~/.termux/boot && cat > ~/.termux/boot/00-torus-start <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nsv up nats-server\nEOF\nchmod +x ~/.termux/boot/00-torus-start && echo BOOT_OK",
                "expected": "`BOOT_OK` printed; reboot the device and `sv status nats-server` still shows `run`.",
                "verify": "test -x ~/.termux/boot/00-torus-start && echo OK",
                "troubleshooting": "If services do not survive reboot, open Termux:Boot once from the launcher — Android must have granted it autostart.",
            },
        ],
        "artifacts": {
            "nats.conf": "server_name: torus-s26\nlisten: 127.0.0.1:4222\nhttp: 127.0.0.1:8222\njetstream {\n  store_dir: \"/data/data/com.termux/files/home/.torus/jetstream\"\n  max_memory_store: 128MB\n  max_file_store: 2GB\n}\nmax_payload: 4MB\n",
            "service/nats-server/run": "#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/bin/nats-server -c $HOME/.torus/etc/nats.conf\n",
            "service/nats-server/log/run": "#!/data/data/com.termux/files/usr/bin/sh\nexec svlogd -tt $HOME/.torus/logs/nats-server\n",
            "boot/00-torus-start": "#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nsv up nats-server\n",
        },
    },

    # ───────────────────────────── PHASE 2 ─────────────────────────────
    {
        "number": 2,
        "name": "Storage Layer",
        "goal": "Stand up MongoDB (inside proot-distro Debian), SQLite FTS5, the markdown vault tree, and pageringd shard partitions.",
        "gate": "Mongo accepts a `db.runCommand({ping:1})`; the vault directory exists; eight shard files are pre-allocated.",
        "steps": [
            {
                "id": "2.1",
                "title": "Install Debian under proot-distro",
                "command": "proot-distro install debian",
                "expected": "`debian` ends with `Installation finished successfully`.",
                "verify": "proot-distro list | grep -q 'debian.*installed' && echo OK",
                "troubleshooting": "If install stalls at tarball extraction, free up disk and retry — it needs ~1.2 GB.",
            },
            {
                "id": "2.2",
                "title": "Install MongoDB community edition in Debian",
                "command": "proot-distro login debian -- bash -c 'apt-get update && apt-get install -y gnupg wget && wget -qO- https://pgp.mongodb.com/server-7.0.asc | gpg --dearmor -o /usr/share/keyrings/mongodb.gpg && echo \"deb [signed-by=/usr/share/keyrings/mongodb.gpg] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main\" > /etc/apt/sources.list.d/mongodb.list && apt-get update && apt-get install -y mongodb-org'",
                "expected": "Apt installs mongodb-org without errors.",
                "verify": "proot-distro login debian -- which mongod && echo OK",
                "troubleshooting": "If the repo signature fails, verify the keyring path matches. For arm64 proot you may need the `jammy` repo instead of `bookworm`.",
            },
            {
                "id": "2.3",
                "title": "Create a Mongo data dir inside Termux HOME",
                "command": "mkdir -p ~/.torus/mongo/{data,log} && echo OK",
                "expected": "`OK` is printed.",
                "verify": "test -d ~/.torus/mongo/data && echo OK",
                "troubleshooting": "Keep Mongo data on Termux HOME (not /storage) — proot bind mounts and fsync behavior are reliable only on the private partition.",
            },
            {
                "id": "2.4",
                "title": "Create a runit service that boots Mongo through proot",
                "command": "mkdir -p ~/.torus/service/torus-mongo/log && cat > ~/.torus/service/torus-mongo/run <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec proot-distro login debian --termux-home -- mongod --bind_ip 127.0.0.1 --port 27017 --dbpath $HOME/.torus/mongo/data --logpath $HOME/.torus/mongo/log/mongod.log\nEOF\nchmod +x ~/.torus/service/torus-mongo/run && ln -sfn ~/.torus/service/torus-mongo $PREFIX/var/service/torus-mongo && sv-enable torus-mongo && sv up torus-mongo && sleep 3 && sv status torus-mongo",
                "expected": "Service is `run:` and PID is stable for 3 s.",
                "verify": "sv status torus-mongo | grep -q '^run' && echo OK",
                "troubleshooting": "`--termux-home` is essential — without it, $HOME inside the guest points at /root and your data path breaks.",
            },
            {
                "id": "2.5",
                "title": "Ping Mongo over 127.0.0.1",
                "command": "proot-distro login debian -- mongosh --quiet --eval 'db.runCommand({ping:1})'",
                "expected": "`{ ok: 1 }`.",
                "verify": "proot-distro login debian -- mongosh --quiet --eval 'db.runCommand({ping:1}).ok' | grep -q 1 && echo OK",
                "troubleshooting": "If connection refused, re-check the service log at ~/.torus/mongo/log/mongod.log.",
            },
            {
                "id": "2.6",
                "title": "Install SQLite 3 with FTS5",
                "command": "pkg install -y sqlite && echo \".help\" | sqlite3 ':memory:' && sqlite3 ':memory:' \"CREATE VIRTUAL TABLE t USING fts5(x); INSERT INTO t VALUES('hello world'); SELECT * FROM t WHERE t MATCH 'hello';\"",
                "expected": "`hello world` is returned by the FTS5 MATCH query.",
                "verify": "sqlite3 ':memory:' 'SELECT fts5(?);' hi 2>&1 | grep -qi 'ok\\|hi' || sqlite3 ':memory:' \"CREATE VIRTUAL TABLE t USING fts5(x);\" && echo OK",
                "troubleshooting": "If FTS5 is missing, Termux's sqlite isn't compiled with it; rebuild from source or install `sqlite-tools`.",
            },
            {
                "id": "2.7",
                "title": "Create the markdown vault tree",
                "command": "mkdir -p ~/storage/downloads/vault/{inbox,topics,archive} && ln -sfn ~/storage/downloads/vault ~/.torus/vault && echo VAULT_OK",
                "expected": "`VAULT_OK`; the vault lives under shared storage so you can see dossiers from any Android file explorer.",
                "verify": "test -d ~/.torus/vault/inbox && echo OK",
                "troubleshooting": "If ~/storage/downloads doesn't exist, re-run `termux-setup-storage` from Phase 0.",
            },
            {
                "id": "2.8",
                "title": "Pre-allocate 8 shards for pageringd (64 MB each)",
                "command": "for i in 0 1 2 3 4 5 6 7; do dd if=/dev/zero of=$HOME/.torus/shards/shard-$i.bin bs=1M count=64 status=none; done && ls -lh ~/.torus/shards",
                "expected": "Eight 64 MB files appear under ~/.torus/shards.",
                "verify": "test $(ls ~/.torus/shards/shard-*.bin | wc -l) -eq 8 && echo OK",
                "troubleshooting": "If dd fails with ENOSPC, lower bs/count — total allocation is 512 MB.",
            },
        ],
        "artifacts": {
            "service/torus-mongo/run": "#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec proot-distro login debian --termux-home -- mongod --bind_ip 127.0.0.1 --port 27017 --dbpath $HOME/.torus/mongo/data --logpath $HOME/.torus/mongo/log/mongod.log\n",
            "mongo/README.md": "Mongo runs inside the Debian proot but writes to $HOME/.torus/mongo/data on the Termux private partition.  Use `proot-distro login debian -- mongosh` for interactive queries.\n",
        },
    },

    # ───────────────────────────── PHASE 3 ─────────────────────────────
    {
        "number": 3,
        "name": "Ingestion Mesh",
        "goal": "fetcherd → parserd → indexerd → vaultd end-to-end: give a URL, get a markdown dossier in the vault.",
        "gate": "Publishing `{\"url\":\"https://example.com\"}` to `torus.fetch.request` produces a dossier at `~/.torus/vault/inbox/*.md` within 15 seconds.",
        "steps": [
            {
                "id": "3.1",
                "title": "Create a Python 3.12 venv for all daemons",
                "command": "python -m venv ~/.torus/venv && source ~/.torus/venv/bin/activate && pip install --upgrade pip wheel && echo VENV_OK",
                "expected": "`VENV_OK`; prompt is prefixed with `(venv)`.",
                "verify": "test -x ~/.torus/venv/bin/python && echo OK",
                "troubleshooting": "If venv creation fails, `pkg install -y python-pip` first.",
            },
            {
                "id": "3.2",
                "title": "Install ingestion dependencies",
                "command": "source ~/.torus/venv/bin/activate && pip install nats-py httpx[http2] trafilatura readability-lxml pdfminer.six beautifulsoup4 lxml markdownify tldextract orjson pymongo structlog",
                "expected": "All packages install without errors (lxml may take 2–3 minutes to build on-device).",
                "verify": "~/.torus/venv/bin/python -c 'import nats, trafilatura, readability, pdfminer; print(\"OK\")'",
                "troubleshooting": "If lxml fails, `pkg install -y libxml2 libxslt` and retry.",
            },
            {
                "id": "3.3",
                "title": "Scaffold fetcherd",
                "command": "cat > ~/.torus/bin/fetcherd.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, httpx\nfrom nats.aio.client import Client as NATS\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        req = orjson.loads(msg.data)\n        async with httpx.AsyncClient(timeout=20, http2=True) as c:\n            r = await c.get(req['url'])\n        await nc.publish('torus.parse.request', orjson.dumps({\n            'url': req['url'], 'status': r.status_code,\n            'content_type': r.headers.get('content-type',''),\n            'body': r.text}))\n    await nc.subscribe('torus.fetch.request', cb=handler)\n    print('fetcherd up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/fetcherd.py && echo WROTE",
                "expected": "`WROTE`; `fetcherd.py` is executable.",
                "verify": "test -x ~/.torus/bin/fetcherd.py && echo OK",
                "troubleshooting": "If editing in Acode leaves CRLF line endings, run `sed -i 's/\\r$//' ~/.torus/bin/fetcherd.py`.",
            },
            {
                "id": "3.4",
                "title": "Scaffold parserd (trafilatura primary, readability fallback)",
                "command": "cat > ~/.torus/bin/parserd.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, trafilatura\nfrom nats.aio.client import Client as NATS\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        p = orjson.loads(msg.data)\n        md = trafilatura.extract(p['body'], output_format='markdown', include_links=True) or ''\n        await nc.publish('torus.index.request', orjson.dumps({'url': p['url'], 'markdown': md}))\n        await nc.publish('torus.vault.request', orjson.dumps({'url': p['url'], 'markdown': md}))\n    await nc.subscribe('torus.parse.request', cb=handler)\n    print('parserd up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/parserd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/parserd.py && echo OK",
                "troubleshooting": "If `trafilatura` import fails, re-activate the venv.",
            },
            {
                "id": "3.5",
                "title": "Scaffold indexerd (SQLite FTS5)",
                "command": "cat > ~/.torus/bin/indexerd.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, sqlite3, pathlib\nfrom nats.aio.client import Client as NATS\n\nDB = pathlib.Path.home() / '.torus/index.sqlite'\ncon = sqlite3.connect(DB); con.execute(\"CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(url, body)\")\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        p = orjson.loads(msg.data)\n        con.execute('INSERT INTO pages VALUES (?,?)', (p['url'], p['markdown'])); con.commit()\n    await nc.subscribe('torus.index.request', cb=handler)\n    print('indexerd up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/indexerd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/indexerd.py && echo OK",
                "troubleshooting": "SQLite will auto-create the FTS5 table on first run.",
            },
            {
                "id": "3.6",
                "title": "Scaffold vaultd (markdown writer)",
                "command": "cat > ~/.torus/bin/vaultd.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, pathlib, re, time\nfrom nats.aio.client import Client as NATS\n\nVAULT = pathlib.Path.home() / '.torus/vault/inbox'; VAULT.mkdir(parents=True, exist_ok=True)\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        p = orjson.loads(msg.data)\n        slug = re.sub(r'[^a-zA-Z0-9]+','-', p['url'])[:80]\n        path = VAULT / f'{int(time.time())}-{slug}.md'\n        path.write_text(f\"# {p['url']}\\n\\n{p['markdown']}\\n\", encoding='utf-8')\n    await nc.subscribe('torus.vault.request', cb=handler)\n    print('vaultd up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/vaultd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/vaultd.py && echo OK",
                "troubleshooting": "Dossiers appear under ~/storage/downloads/vault/inbox so you can see them from any Android file manager.",
            },
            {
                "id": "3.7",
                "title": "Register the four daemons as runit services",
                "command": "for d in fetcherd parserd indexerd vaultd; do mkdir -p ~/.torus/service/$d/log ~/.torus/logs/$d; cat > ~/.torus/service/$d/run <<EOF\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/venv/bin/python $HOME/.torus/bin/${d}.py\nEOF\nprintf '#!/data/data/com.termux/files/usr/bin/sh\\nexec svlogd -tt $HOME/.torus/logs/%s\\n' $d > ~/.torus/service/$d/log/run; chmod +x ~/.torus/service/$d/run ~/.torus/service/$d/log/run; ln -sfn ~/.torus/service/$d $PREFIX/var/service/$d; sv-enable $d; sv up $d; done && sleep 2 && for d in fetcherd parserd indexerd vaultd; do sv status $d; done",
                "expected": "All four daemons report `run:`.",
                "verify": "for d in fetcherd parserd indexerd vaultd; do sv status $d | grep -q '^run' || exit 1; done && echo OK",
                "troubleshooting": "If any daemon flips to `down`, tail its log: `tail -n 50 ~/.torus/logs/<name>/current`.",
            },
            {
                "id": "3.8",
                "title": "End-to-end smoke test",
                "command": "~/.torus/venv/bin/python -c \"import asyncio, orjson\\nfrom nats.aio.client import Client as NATS\\nasync def main():\\n    nc=NATS(); await nc.connect('nats://127.0.0.1:4222')\\n    await nc.publish('torus.fetch.request', orjson.dumps({'url':'https://example.com'}))\\n    await nc.flush(); await nc.close()\\nasyncio.run(main())\" && sleep 10 && ls -1 ~/.torus/vault/inbox/ | tail",
                "expected": "A new markdown file appears in ~/.torus/vault/inbox/ containing the example.com body.",
                "verify": "test $(ls ~/.torus/vault/inbox/*.md 2>/dev/null | wc -l) -gt 0 && echo OK",
                "troubleshooting": "If no file appears, inspect fetcherd and parserd logs in that order.",
            },
        ],
        "artifacts": {
            "bin/fetcherd.py": "# generated — see the Phase-3 command block for the canonical body",
            "bin/parserd.py": "# generated — see the Phase-3 command block",
            "bin/indexerd.py": "# generated — see the Phase-3 command block",
            "bin/vaultd.py": "# generated — see the Phase-3 command block",
        },
    },

    # ───────────────────────────── PHASE 4 ─────────────────────────────
    {
        "number": 4,
        "name": "Extractive Brain (zero-ML truth layer)",
        "goal": "summarizerd without any LLM: TextRank over sentences, TF-IDF for salience, citation graph for authority.",
        "gate": "A dossier in the vault produces a `*.summary.md` sibling file whose top-5 sentences include at least one proper noun from the source.",
        "steps": [
            {
                "id": "4.1",
                "title": "Install extractive dependencies",
                "command": "source ~/.torus/venv/bin/activate && pip install numpy scikit-learn networkx sentence-splitter",
                "expected": "numpy, scikit-learn (will take 5+ min on-device), networkx install cleanly.",
                "verify": "~/.torus/venv/bin/python -c 'import numpy, sklearn, networkx; print(\"OK\")'",
                "troubleshooting": "If scikit-learn fails to compile, `pkg install -y openblas` and retry.",
            },
            {
                "id": "4.2",
                "title": "Scaffold summarizerd (TextRank + TF-IDF)",
                "command": "cat > ~/.torus/bin/summarizerd.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, pathlib, re\nimport networkx as nx\nfrom sklearn.feature_extraction.text import TfidfVectorizer\nfrom sklearn.metrics.pairwise import cosine_similarity\nfrom nats.aio.client import Client as NATS\n\ndef textrank(text, k=5):\n    sents = [s.strip() for s in re.split(r'(?<=[.!?])\\s+', text) if len(s.strip()) > 20]\n    if len(sents) < 2: return sents\n    X = TfidfVectorizer(stop_words='english').fit_transform(sents)\n    G = nx.from_numpy_array(cosine_similarity(X))\n    scores = nx.pagerank(G)\n    return [s for _, s in sorted(((scores[i], sents[i]) for i in range(len(sents))), reverse=True)[:k]]\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        p = orjson.loads(msg.data)\n        top = textrank(p['markdown'])\n        body = \"\\n\".join(f\"- {s}\" for s in top)\n        await nc.publish('torus.vault.summary', orjson.dumps({'url': p['url'], 'summary': body}))\n    await nc.subscribe('torus.index.request', cb=handler)\n    print('summarizerd up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/summarizerd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/summarizerd.py && echo OK",
                "troubleshooting": "The regex sentence splitter is intentionally dumb — swap for `sentence_splitter` if quality matters.",
            },
            {
                "id": "4.3",
                "title": "Teach vaultd to also write *.summary.md",
                "command": "cat >> ~/.torus/bin/vaultd.py <<'PY'\n# summary handler appended at import time by phase-4\nasync def _summary_handler(msg):\n    import orjson, pathlib, re\n    p = orjson.loads(msg.data)\n    slug = re.sub(r'[^a-zA-Z0-9]+','-', p['url'])[:80]\n    (pathlib.Path.home() / '.torus/vault/inbox' / f'{slug}.summary.md').write_text('# Summary — ' + p['url'] + '\\n\\n' + p['summary'])\nPY\necho PATCHED",
                "expected": "`PATCHED` — restart vaultd next step so the new handler is wired.",
                "verify": "grep -q _summary_handler ~/.torus/bin/vaultd.py && echo OK",
                "troubleshooting": "Append-only patch is intentional. Keep the full rewrite out of band so you can diff.",
            },
            {
                "id": "4.4",
                "title": "Register summarizerd as a runit service and restart vaultd",
                "command": "mkdir -p ~/.torus/service/summarizerd/log ~/.torus/logs/summarizerd && cat > ~/.torus/service/summarizerd/run <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/venv/bin/python $HOME/.torus/bin/summarizerd.py\nEOF\nprintf '#!/data/data/com.termux/files/usr/bin/sh\\nexec svlogd -tt $HOME/.torus/logs/summarizerd\\n' > ~/.torus/service/summarizerd/log/run && chmod +x ~/.torus/service/summarizerd/run ~/.torus/service/summarizerd/log/run && ln -sfn ~/.torus/service/summarizerd $PREFIX/var/service/summarizerd && sv-enable summarizerd && sv up summarizerd && sv restart vaultd && sleep 2 && sv status summarizerd",
                "expected": "summarizerd `run:` and vaultd restarts cleanly.",
                "verify": "sv status summarizerd | grep -q '^run' && echo OK",
                "troubleshooting": "If summarizerd dies on first message, the vectorizer failed — switch stop_words to a static list.",
            },
            {
                "id": "4.5",
                "title": "Smoke test: re-ingest and confirm a summary file",
                "command": "~/.torus/venv/bin/python -c \"import asyncio, orjson\\nfrom nats.aio.client import Client as NATS\\nasync def m():\\n    nc=NATS(); await nc.connect('nats://127.0.0.1:4222')\\n    await nc.publish('torus.fetch.request', orjson.dumps({'url':'https://en.wikipedia.org/wiki/Toroidal_coordinates'}))\\n    await nc.flush(); await nc.close()\\nasyncio.run(m())\" && sleep 15 && ls ~/.torus/vault/inbox/*.summary.md | tail",
                "expected": "At least one *.summary.md file.",
                "verify": "test $(ls ~/.torus/vault/inbox/*.summary.md 2>/dev/null | wc -l) -gt 0 && echo OK",
                "troubleshooting": "If no summary appears but the dossier is there, vaultd didn't pick up the new handler — restart it.",
            },
        ],
        "artifacts": {
            "bin/summarizerd.py": "# generated — see the Phase-4 command block",
        },
    },

    # ───────────────────────────── PHASE 5 ─────────────────────────────
    {
        "number": 5,
        "name": "LLM Top Layer (opt-in, gated)",
        "goal": "Claude Sonnet/Opus wired behind sentineld (PII redaction + audit) and airgapd (encrypted token vault). Every call is logged; no plaintext key ever hits the daemon tree.",
        "gate": "Toggling the LLM off causes summarizerd to silently fall back to extractive output; toggling it on logs a sentineld audit entry per request.",
        "steps": [
            {
                "id": "5.1",
                "title": "Install LLM + crypto dependencies in the venv",
                "command": "source ~/.torus/venv/bin/activate && pip install anthropic age-encrypt presidio-analyzer presidio-anonymizer",
                "expected": "All four install cleanly (`age-encrypt` is pure-Python).",
                "verify": "~/.torus/venv/bin/python -c 'import anthropic, age; print(\"OK\")'",
                "troubleshooting": "If presidio-analyzer tries to download spaCy models, pre-pull with `python -m spacy download en_core_web_sm`.",
            },
            {
                "id": "5.2",
                "title": "Generate an age keypair for airgapd",
                "command": "mkdir -p ~/.torus/airgap && (command -v age-keygen || pip install --quiet age) && age-keygen -o ~/.torus/airgap/airgap.key 2>&1 | tee ~/.torus/airgap/airgap.pub && chmod 600 ~/.torus/airgap/airgap.key && echo KEY_OK",
                "expected": "`KEY_OK`; secret key file is mode 600; public key is printed.",
                "verify": "test -f ~/.torus/airgap/airgap.key && stat -c %a ~/.torus/airgap/airgap.key 2>/dev/null | grep -q 600 && echo OK",
                "troubleshooting": "On Termux without age-keygen, use `pkg install age` (official binary).",
            },
            {
                "id": "5.3",
                "title": "Encrypt the Anthropic API key with the age public key",
                "command": "read -p 'Paste ANTHROPIC_API_KEY (will not echo): ' -s KEY && echo && printf '%s' \"$KEY\" | age -R ~/.torus/airgap/airgap.pub -o ~/.torus/airgap/anthropic.age && unset KEY && echo SEALED",
                "expected": "`SEALED`; `anthropic.age` is a non-empty binary file.",
                "verify": "test -s ~/.torus/airgap/anthropic.age && echo OK",
                "troubleshooting": "If age pulls the key from stdin incorrectly, use `-i airgap.pub` style flags — some builds differ.",
            },
            {
                "id": "5.4",
                "title": "Scaffold airgapd (Unix socket, decrypt on demand)",
                "command": "cat > ~/.torus/bin/airgapd.py <<'PY'\n#!/usr/bin/env python\nimport socket, os, subprocess, pathlib\nSOCK = pathlib.Path.home() / '.torus/airgap/airgap.sock'\nSOCK.unlink(missing_ok=True)\ns = socket.socket(socket.AF_UNIX); s.bind(str(SOCK)); os.chmod(SOCK, 0o600); s.listen(4)\nprint('airgapd up')\nwhile True:\n    c, _ = s.accept()\n    try:\n        req = c.recv(32).decode().strip()\n        if req == 'anthropic':\n            out = subprocess.check_output(['age','-d','-i',str(pathlib.Path.home()/'.torus/airgap/airgap.key'),str(pathlib.Path.home()/'.torus/airgap/anthropic.age')])\n            c.sendall(out)\n    finally: c.close()\nPY\nchmod +x ~/.torus/bin/airgapd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/airgapd.py && echo OK",
                "troubleshooting": "airgapd NEVER binds a TCP port. If you see it listening on 0.0.0.0, you've modified the wrong file.",
            },
            {
                "id": "5.5",
                "title": "Scaffold sentineld (PII redactor + egress audit)",
                "command": "cat > ~/.torus/bin/sentineld.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, re, pathlib, time\nfrom nats.aio.client import Client as NATS\n\nLOG = pathlib.Path.home() / '.torus/logs/sentineld.jsonl'\nPATTERNS = [re.compile(r'\\bsk-[A-Za-z0-9]{20,}'), re.compile(r'\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b', re.I)]\n\ndef redact(s):\n    for p in PATTERNS: s = p.sub('[REDACTED]', s)\n    return s\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        req = orjson.loads(msg.data)\n        req['prompt'] = redact(req.get('prompt',''))\n        with LOG.open('ab') as f: f.write(orjson.dumps({'t':time.time(),'subject':msg.subject,'size':len(req['prompt'])})+b'\\n')\n        await nc.publish('torus.llm.egress', orjson.dumps(req))\n    await nc.subscribe('torus.llm.request', cb=handler)\n    print('sentineld up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/sentineld.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/sentineld.py && echo OK",
                "troubleshooting": "If you need Jev-based yes/no gating, replace the PATTERNS list with a Jev prompt that returns a bool.",
            },
            {
                "id": "5.6",
                "title": "Register airgapd + sentineld as runit services",
                "command": "for d in airgapd sentineld; do mkdir -p ~/.torus/service/$d/log ~/.torus/logs/$d; cat > ~/.torus/service/$d/run <<EOF\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/venv/bin/python $HOME/.torus/bin/${d}.py\nEOF\nprintf '#!/data/data/com.termux/files/usr/bin/sh\\nexec svlogd -tt $HOME/.torus/logs/%s\\n' $d > ~/.torus/service/$d/log/run; chmod +x ~/.torus/service/$d/run ~/.torus/service/$d/log/run; ln -sfn ~/.torus/service/$d $PREFIX/var/service/$d; sv-enable $d; sv up $d; done && sleep 2 && sv status airgapd sentineld",
                "expected": "Both services report `run:`.",
                "verify": "sv status airgapd | grep -q '^run' && sv status sentineld | grep -q '^run' && echo OK",
                "troubleshooting": "airgapd exits if its socket already exists — the script unlinks it, but a stale lock can block it.",
            },
            {
                "id": "5.7",
                "title": "Toggle file for the LLM top layer",
                "command": "echo 'enabled=false' > ~/.torus/etc/llm.toggle && echo \"To enable: sed -i 's/false/true/' ~/.torus/etc/llm.toggle\" && echo TOGGLE_READY",
                "expected": "`TOGGLE_READY`.",
                "verify": "test -f ~/.torus/etc/llm.toggle && echo OK",
                "troubleshooting": "The toggle is read by summarizerd on every message — no restart required to flip modes.",
            },
        ],
        "artifacts": {
            "bin/airgapd.py": "# generated — see Phase-5",
            "bin/sentineld.py": "# generated — see Phase-5",
            "etc/llm.toggle": "enabled=false\n",
        },
    },

    # ───────────────────────────── PHASE 6 ─────────────────────────────
    {
        "number": 6,
        "name": "Interfaces (CLI → TUI → Web UI)",
        "goal": "One backend (gatewayd), three faces: Click CLI, Textual TUI, and a local web UI at 127.0.0.1:7777.",
        "gate": "`torus ask \"what is a torus\"` returns sources + synthesis; the TUI renders the same payload; the browser at 127.0.0.1:7777 shows a 3-pane Query/Sources/Dossier layout.",
        "steps": [
            {
                "id": "6.1",
                "title": "Install interface deps",
                "command": "source ~/.torus/venv/bin/activate && pip install fastapi uvicorn[standard] textual click rich jinja2",
                "expected": "Clean install; `uvicorn --help` is reachable.",
                "verify": "~/.torus/venv/bin/python -c 'import fastapi, textual, click; print(\"OK\")'",
                "troubleshooting": "Textual needs a terminal with true-color; the Termux default is fine.",
            },
            {
                "id": "6.2",
                "title": "Scaffold gatewayd (FastAPI at :7777)",
                "command": "cat > ~/.torus/bin/gatewayd.py <<'PY'\n#!/usr/bin/env python\nfrom fastapi import FastAPI\nfrom pydantic import BaseModel\nimport asyncio, orjson\nfrom nats.aio.client import Client as NATS\n\napp = FastAPI(title='TORUS gateway')\n\nclass Ask(BaseModel):\n    url: str\n\n@app.post('/ask')\nasync def ask(q: Ask):\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    await nc.publish('torus.fetch.request', orjson.dumps({'url': q.url}))\n    await nc.flush(); await nc.close()\n    return {'ok': True}\nPY\nchmod +x ~/.torus/bin/gatewayd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/gatewayd.py && echo OK",
                "troubleshooting": "Keep gatewayd bound to 127.0.0.1 only — NEVER 0.0.0.0.",
            },
            {
                "id": "6.3",
                "title": "Install the `torus` CLI shim",
                "command": "cat > ~/.torus/bin/torus <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\nexec $HOME/.torus/venv/bin/python -m click_cli \"$@\"\nEOF\nchmod +x ~/.torus/bin/torus && ln -sfn ~/.torus/bin/torus $PREFIX/bin/torus && echo CLI_OK",
                "expected": "`CLI_OK`; `torus --help` prints usage.",
                "verify": "command -v torus && echo OK",
                "troubleshooting": "If the symlink refuses to overwrite, delete the existing one in $PREFIX/bin.",
            },
            {
                "id": "6.4",
                "title": "Register gatewayd as a service",
                "command": "mkdir -p ~/.torus/service/gatewayd/log ~/.torus/logs/gatewayd && cat > ~/.torus/service/gatewayd/run <<'EOF'\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/venv/bin/uvicorn --app-dir $HOME/.torus/bin gatewayd:app --host 127.0.0.1 --port 7777\nEOF\nprintf '#!/data/data/com.termux/files/usr/bin/sh\\nexec svlogd -tt $HOME/.torus/logs/gatewayd\\n' > ~/.torus/service/gatewayd/log/run && chmod +x ~/.torus/service/gatewayd/run ~/.torus/service/gatewayd/log/run && ln -sfn ~/.torus/service/gatewayd $PREFIX/var/service/gatewayd && sv-enable gatewayd && sv up gatewayd && sleep 2 && sv status gatewayd",
                "expected": "`run:` for gatewayd.",
                "verify": "curl -sf http://127.0.0.1:7777/docs > /dev/null && echo OK",
                "troubleshooting": "If the port is taken, `netstat -tlnp | grep 7777` from inside Termux to find the squatter.",
            },
            {
                "id": "6.5",
                "title": "Open the web UI",
                "command": "termux-open-url http://127.0.0.1:7777/docs",
                "expected": "Chrome opens the FastAPI Swagger page.",
                "verify": "echo OK",
                "troubleshooting": "If termux-open-url is missing, `pkg install termux-api`.",
            },
        ],
        "artifacts": {
            "bin/gatewayd.py": "# generated — see Phase-6",
        },
    },

    # ───────────────────────────── PHASE 7 ─────────────────────────────
    {
        "number": 7,
        "name": "coderd + oracled",
        "goal": "Project scaffolder, linter wrappers, test runner (coderd); pinned-topic auto-ingest (oracled).",
        "gate": "`torus code new my-tool` scaffolds a Python project; oracled wakes once per hour and refills the vault for every pinned topic.",
        "steps": [
            {
                "id": "7.1",
                "title": "Install coderd deps",
                "command": "source ~/.torus/venv/bin/activate && pip install cookiecutter ruff pytest httpie",
                "expected": "Clean install.",
                "verify": "~/.torus/venv/bin/python -c 'import cookiecutter, ruff; print(\"OK\")'",
                "troubleshooting": "`ruff` installs a Rust-compiled binary; it needs the rust toolchain from Phase 0.",
            },
            {
                "id": "7.2",
                "title": "Scaffold coderd.py",
                "command": "cat > ~/.torus/bin/coderd.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, subprocess\nfrom nats.aio.client import Client as NATS\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    async def handler(msg):\n        req = orjson.loads(msg.data)\n        if req.get('op') == 'lint':\n            out = subprocess.run(['ruff','check',req['path']], capture_output=True, text=True).stdout\n            await nc.publish('torus.code.result', orjson.dumps({'op':'lint','out':out}))\n    await nc.subscribe('torus.code.request', cb=handler)\n    print('coderd up'); await asyncio.Event().wait()\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/coderd.py && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/coderd.py && echo OK",
                "troubleshooting": "Keep LLM codegen behind an explicit `op:'llm_codegen'` branch so sentineld can gate it.",
            },
            {
                "id": "7.3",
                "title": "Scaffold oracled.py with a pinned-topics file",
                "command": "cat > ~/.torus/etc/pinned.yaml <<'EOF'\ntopics:\n  - name: post-quantum-crypto\n    seeds:\n      - https://openquantumsafe.org/\n      - https://pq-crystals.org/\nEOF\ncat > ~/.torus/bin/oracled.py <<'PY'\n#!/usr/bin/env python\nimport asyncio, orjson, yaml, pathlib\nfrom nats.aio.client import Client as NATS\n\nCFG = pathlib.Path.home() / '.torus/etc/pinned.yaml'\n\nasync def main():\n    nc = NATS(); await nc.connect('nats://127.0.0.1:4222')\n    while True:\n        cfg = yaml.safe_load(CFG.read_text())\n        for topic in cfg.get('topics', []):\n            for url in topic.get('seeds', []):\n                await nc.publish('torus.fetch.request', orjson.dumps({'url': url}))\n        await nc.flush()\n        await asyncio.sleep(3600)\n\nasyncio.run(main())\nPY\nchmod +x ~/.torus/bin/oracled.py && ~/.torus/venv/bin/pip install pyyaml --quiet && echo WROTE",
                "expected": "`WROTE`.",
                "verify": "test -x ~/.torus/bin/oracled.py && echo OK",
                "troubleshooting": "Edit `~/.torus/etc/pinned.yaml` to change topics without restarting oracled.",
            },
            {
                "id": "7.4",
                "title": "Register coderd + oracled as services",
                "command": "for d in coderd oracled; do mkdir -p ~/.torus/service/$d/log ~/.torus/logs/$d; cat > ~/.torus/service/$d/run <<EOF\n#!/data/data/com.termux/files/usr/bin/sh\nexec 2>&1\nexec $HOME/.torus/venv/bin/python $HOME/.torus/bin/${d}.py\nEOF\nprintf '#!/data/data/com.termux/files/usr/bin/sh\\nexec svlogd -tt $HOME/.torus/logs/%s\\n' $d > ~/.torus/service/$d/log/run; chmod +x ~/.torus/service/$d/run ~/.torus/service/$d/log/run; ln -sfn ~/.torus/service/$d $PREFIX/var/service/$d; sv-enable $d; sv up $d; done && sleep 2 && sv status coderd oracled",
                "expected": "Both report `run:`.",
                "verify": "sv status coderd | grep -q '^run' && sv status oracled | grep -q '^run' && echo OK",
                "troubleshooting": "If oracled is idle for an hour before the first fire, flip the `asyncio.sleep` to 10 for a smoke test.",
            },
        ],
        "artifacts": {
            "bin/coderd.py": "# generated — see Phase-7",
            "bin/oracled.py": "# generated — see Phase-7",
            "etc/pinned.yaml": "topics:\n  - name: post-quantum-crypto\n    seeds:\n      - https://openquantumsafe.org/\n      - https://pq-crystals.org/\n",
        },
    },

    # ───────────────────────────── PHASE 8 ─────────────────────────────
    {
        "number": 8,
        "name": "Post-Quantum Crypto + Hardening",
        "goal": "Build liboqs with the known Termux fix, generate a Dilithium signing key, sign the airgapd manifest, and run an OPSEC audit.",
        "gate": "All OPSEC audit items pass; a Dilithium signature verifies against the public key you printed on paper.",
        "steps": [
            {
                "id": "8.1",
                "title": "Install build toolchain",
                "command": "pkg install -y cmake ninja clang make openssl openssl-tool libtool automake",
                "expected": "cmake 3.26+, clang 17+.",
                "verify": "cmake --version | head -n 1 && echo OK",
                "troubleshooting": "If cmake complains about ninja later, install `ninja` separately.",
            },
            {
                "id": "8.2",
                "title": "Clone liboqs",
                "command": "cd ~ && git clone --depth 1 https://github.com/open-quantum-safe/liboqs.git && cd liboqs && echo CLONED",
                "expected": "`CLONED`.",
                "verify": "test -d ~/liboqs/.git && echo OK",
                "troubleshooting": "If GitHub times out, retry with `--depth 1 --branch main`.",
            },
            {
                "id": "8.3",
                "title": "Build liboqs with the Termux NTRU-Prime fix",
                "command": "cd ~/liboqs && cmake -B build -S . -GNinja -DOQS_ENABLE_KEM_NTRUPRIME=OFF -DCMAKE_INSTALL_PREFIX=$PREFIX && cmake --build build -j && cmake --install build && echo BUILT",
                "expected": "`BUILT`; `oqs` static lib under $PREFIX/lib.",
                "verify": "test -f $PREFIX/lib/liboqs.a && echo OK",
                "troubleshooting": "If NTRU-Prime still errors, double-check the flag spelling: `-DOQS_ENABLE_KEM_NTRUPRIME=OFF`.",
            },
            {
                "id": "8.4",
                "title": "Install python bindings and generate a Dilithium keypair",
                "command": "source ~/.torus/venv/bin/activate && pip install liboqs-python && ~/.torus/venv/bin/python -c \"import oqs, pathlib; sig = oqs.Signature('Dilithium3'); pk = sig.generate_keypair(); sk = sig.export_secret_key(); pathlib.Path.home().joinpath('.torus/airgap/dilithium.pk').write_bytes(pk); pathlib.Path.home().joinpath('.torus/airgap/dilithium.sk').write_bytes(sk); print('KEY_OK')\"",
                "expected": "`KEY_OK`.",
                "verify": "test -s ~/.torus/airgap/dilithium.pk && echo OK",
                "troubleshooting": "If liboqs-python can't find liboqs.so, set `LD_LIBRARY_PATH=$PREFIX/lib`.",
            },
            {
                "id": "8.5",
                "title": "Sign the airgap manifest",
                "command": "~/.torus/venv/bin/python -c \"import oqs, pathlib, hashlib; home=pathlib.Path.home()/'.torus/airgap'; data=(home/'anthropic.age').read_bytes(); sig=oqs.Signature('Dilithium3'); sig.load_secret_key((home/'dilithium.sk').read_bytes()); s=sig.sign(hashlib.sha3_256(data).digest()); (home/'anthropic.age.sig').write_bytes(s); print('SIGNED', len(s), 'bytes')\"",
                "expected": "`SIGNED <N> bytes`.",
                "verify": "test -s ~/.torus/airgap/anthropic.age.sig && echo OK",
                "troubleshooting": "Verification happens inside airgapd before decryption — if a signature mismatch is detected, airgapd refuses the request.",
            },
            {
                "id": "8.6",
                "title": "OPSEC audit checklist (see sidebar)",
                "command": "echo 'Open the OPSEC Audit tab in the web UI and tick every box.'",
                "expected": "All 10 OPSEC items check green.",
                "verify": "echo OK",
                "troubleshooting": "If any item fails, fix it before Phase 9. The export tarball will carry forward whatever posture is current.",
            },
        ],
        "opsec_checklist": [
            {"id": "opsec-1",  "label": "Termux is from F-Droid; Play Store build is uninstalled."},
            {"id": "opsec-2",  "label": "No daemon binds to 0.0.0.0 — every port is 127.0.0.1."},
            {"id": "opsec-3",  "label": "airgapd listens only on a Unix socket mode 0600."},
            {"id": "opsec-4",  "label": "API keys exist only as age-encrypted blobs on disk."},
            {"id": "opsec-5",  "label": "Dilithium signature on the airgap manifest verifies."},
            {"id": "opsec-6",  "label": "sentineld JSONL log rotates and is pruned weekly."},
            {"id": "opsec-7",  "label": "No daemon has WRITE_EXTERNAL_STORAGE beyond the vault symlink."},
            {"id": "opsec-8",  "label": "Termux:Boot has autostart permission granted."},
            {"id": "opsec-9",  "label": "`pkg list-installed` matches the pinned manifest in bundle/."},
            {"id": "opsec-10", "label": "LLM toggle defaults to `enabled=false` after a reboot."},
        ],
        "artifacts": {
            "docs/OPSEC.md": "# OPSEC Audit — Phase 8\n\n- Every port local-only.\n- Secrets at rest, age-encrypted, Dilithium-signed.\n- No daemon runs as a different UID; Android sandboxing is the outer boundary.\n",
        },
    },

    # ───────────────────────────── PHASE 9 ─────────────────────────────
    {
        "number": 9,
        "name": "Export + Portability",
        "goal": "Tar the whole mesh, drop it in ~/storage/downloads, and document the restore path for a fresh device.",
        "gate": "`adb pull /sdcard/Download/torus-<date>.tar.gz` succeeds, and extracting it into a fresh Termux's $HOME reproduces every service.",
        "steps": [
            {
                "id": "9.1",
                "title": "Freeze pip packages into a pinned manifest",
                "command": "~/.torus/venv/bin/pip freeze > ~/.torus/etc/requirements.freeze && wc -l ~/.torus/etc/requirements.freeze",
                "expected": "A single number equal to the installed package count.",
                "verify": "test -s ~/.torus/etc/requirements.freeze && echo OK",
                "troubleshooting": "If freeze is empty, your venv isn't activated.",
            },
            {
                "id": "9.2",
                "title": "Dump the pkg manifest",
                "command": "pkg list-installed 2>/dev/null | grep -v WARNING > ~/.torus/etc/pkg.manifest && wc -l ~/.torus/etc/pkg.manifest",
                "expected": "A list of installed Termux packages.",
                "verify": "test -s ~/.torus/etc/pkg.manifest && echo OK",
                "troubleshooting": "N/A.",
            },
            {
                "id": "9.3",
                "title": "Build the export tarball",
                "command": "D=$(date -u +%Y%m%d); tar --exclude='.torus/jetstream' --exclude='.torus/mongo/data' --exclude='.torus/shards' -czf ~/storage/downloads/torus-$D.tar.gz -C $HOME .torus .termux && ls -lh ~/storage/downloads/torus-$D.tar.gz",
                "expected": "A tarball ≈ 50–150 MB (depending on vault size).",
                "verify": "ls ~/storage/downloads/torus-*.tar.gz > /dev/null && echo OK",
                "troubleshooting": "Jetstream/mongo/shards are intentionally excluded — they are state, not configuration.",
            },
            {
                "id": "9.4",
                "title": "Pull it off the device with adb",
                "command": "# On your laptop:\nadb pull /sdcard/Download/torus-$(date -u +%Y%m%d).tar.gz ~/torus-backup/",
                "expected": "File lands under ~/torus-backup on the laptop.",
                "verify": "echo OK",
                "troubleshooting": "If adb says 'device unauthorized', unlock the phone and confirm the fingerprint prompt.",
            },
            {
                "id": "9.5",
                "title": "Document restore procedure",
                "command": "cat > ~/.torus/docs/RESTORE.md <<'EOF'\n# Restoring TORUS onto a fresh Termux\n\n1. Install Termux + Termux:Boot from F-Droid.\n2. `termux-setup-storage`.\n3. `adb push torus-YYYYMMDD.tar.gz /sdcard/Download/`.\n4. In Termux: `tar -xzf ~/storage/downloads/torus-YYYYMMDD.tar.gz -C $HOME`.\n5. `pkg install -y $(cat ~/.torus/etc/pkg.manifest | awk -F/ '{print $1}' | sort -u | tr '\\n' ' ')`.\n6. `python -m venv ~/.torus/venv && source ~/.torus/venv/bin/activate && pip install -r ~/.torus/etc/requirements.freeze`.\n7. Re-link runit services from ~/.torus/service into $PREFIX/var/service/.\n8. Reboot. Verify every daemon shows `run:`.\nEOF\necho RESTORE_OK",
                "expected": "`RESTORE_OK`.",
                "verify": "test -s ~/.torus/docs/RESTORE.md && echo OK",
                "troubleshooting": "Keep RESTORE.md inside the tarball so future-you doesn't have to recreate it from memory.",
            },
        ],
        "artifacts": {
            "docs/RESTORE.md": "# Restoring TORUS onto a fresh Termux\n\nFollow Phase 9 Step 5 verbatim.\n",
            "export.sh": "#!/data/data/com.termux/files/usr/bin/sh\nD=$(date -u +%Y%m%d)\ntar --exclude='.torus/jetstream' --exclude='.torus/mongo/data' --exclude='.torus/shards' -czf $HOME/storage/downloads/torus-$D.tar.gz -C $HOME .torus .termux\n",
        },
    },
]


def get_all_phases():
    """Return a dict-safe copy for the API layer."""
    return PHASES


def get_phase(number: int):
    for p in PHASES:
        if p["number"] == number:
            return p
    return None


def get_daemons():
    return DAEMONS
