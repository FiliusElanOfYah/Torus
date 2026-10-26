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
        "goal": "Project scaffolder, linter wrappers, test runner (coderd); pinned-topic auto-refill on a schedule (oracled).",
        "gate": "`torus code new my-tool` scaffolds a Python project; `torus oracle status` shows every pinned topic with a last-run time and a digest under vault/topics/.",
        "steps": [
            {
                "id": "7.1",
                "title": "Install Phase 7",
                "command": "torus install 7",
                "expected": "`OK` lines for bin/coderd.py, bin/oracled.py, etc/pinned.yaml, etc/oracle.conf, both services `run:`, then `Phase 7 done`.",
                "verify": "sv status coderd | grep -q '^run' && sv status oracled | grep -q '^run' && echo OK",
                "troubleshooting": "The installer copies files from ~/.torus/src/torus/files and never touches an existing pinned.yaml, oracle.conf or coder.conf. If a service is `down`, read `torus logs coderd` / `torus logs oracled`.",
            },
            {
                "id": "7.2",
                "title": "Scaffold a project with coderd",
                "command": "torus code new my-tool && torus code projects",
                "expected": "A new project folder under ~/projects (or the root set in etc/coder.conf), listed by `torus code projects`.",
                "verify": "test -d ~/projects/my-tool && echo OK",
                "troubleshooting": "Change where projects live with `torus code root <dir>`. Run anything inside a project with `torus code run my-tool \"pytest -q\"`; the Code tab on the web page writes, runs and fixes code with Claude when the LLM is on.",
            },
            {
                "id": "7.3",
                "title": "Pin your topics",
                "command": "torus oracle edit   # or: Vault tab → Pinned topics → Edit topics",
                "expected": "pinned.yaml lists `- name: <topic>` blocks, each with `seeds:` and one `- https://...` per line. The file is checked when you save.",
                "verify": "torus oracle check",
                "troubleshooting": "A seed must be an http(s) URL and topic names must be unique; the error says which line is wrong. oracled re-reads the file on every cycle, so no restart is needed.",
            },
            {
                "id": "7.4",
                "title": "Refill now and check the schedule",
                "command": "torus oracle run && sleep 20 && torus oracle status",
                "expected": "Each seed shows `ok` (or `robots`/`blocked`/`empty` with a reason), each topic a digest path like vault/topics/<topic>.md, and `next:` one interval away.",
                "verify": "ls ~/.torus/vault/topics/*.md > /dev/null && echo OK",
                "troubleshooting": "The interval is `torus oracle every <minutes>` (default 60, stored in etc/oracle.conf); `torus oracle off` pauses the schedule but `torus oracle run` still works. Fetched pages also land in your index and vault/inbox like any `torus ask`.",
            },
        ],
        "artifacts": {
            "etc/pinned.yaml": "# TORUS pinned topics. oracled re-reads every seed on its schedule (torus oracle status).\ntopics:\n  - name: \"post-quantum-crypto\"\n    seeds:\n      - https://openquantumsafe.org/\n      - https://pq-crystals.org/\n",
            "etc/oracle.conf": "every_minutes=60\nenabled=true\nfresh=true\nstart_delay_seconds=90\n",
        },
    },

    # ───────────────────────────── PHASE 8 ─────────────────────────────
    {
        "number": 8,
        "name": "Post-Quantum Seal + Hardening",
        "goal": "Sign your API keys and every daemon with an ML-DSA-65 (Dilithium) key held only on the phone, make airgapd refuse anything that no longer matches, and pass the 13-check OPSEC audit.",
        "gate": "`torus seal verify` prints the key fingerprint you wrote on paper; `torus audit` reports 0 FAIL.",
        "steps": [
            {
                "id": "8.1",
                "title": "Install Phase 8",
                "command": "torus install 8",
                "expected": "dilithium-py installed into the venv, `sealed (...)` printed, airgapd restarted, then the OPSEC audit runs once and prints PASS/WARN/FAIL per check, then `Phase 8 done`.",
                "verify": "test -s ~/.torus/airgap/mldsa65.pk && test -s ~/.torus/var/seal.json && echo OK",
                "troubleshooting": "dilithium-py is pure Python — no C library build (cmake, clang, ninja) is needed on Termux. If `sealing failed`, run `torus seal` by hand and read the message; the usual cause is a key file that is not mode 600.",
            },
            {
                "id": "8.2",
                "title": "Write the key fingerprint on paper",
                "command": "torus seal verify",
                "expected": "`OK   seal verifies (ML-DSA-65, sealed <date>, fingerprint <hex>)` — copy the fingerprint somewhere offline. The secret key stays in ~/.torus/airgap/mldsa65.sk (mode 600) and is never exported without --with-keys.",
                "verify": "torus seal verify | grep -q OK && echo OK",
                "troubleshooting": "If the fingerprint ever changes without you running `torus seal`, treat the phone as compromised: stop the mesh (`torus stop`) and rotate your API keys.",
            },
            {
                "id": "8.3",
                "title": "Re-seal after changing a key or updating daemons",
                "command": "torus seal && torus seal verify",
                "expected": "`sealed N files with ML-DSA-65   key fingerprint: ...` then `OK   seal verifies`. airgapd checks a key against the seal before handing it out, so a new key must be sealed before Claude can use it.",
                "verify": "torus seal verify | grep -q OK && echo OK",
                "troubleshooting": "`torus key set anthropic` and `torus install <n>` re-seal automatically. An `ERR sealed:` line in `torus logs airgapd` means a key file changed behind your back — inspect before re-sealing.",
            },
            {
                "id": "8.4",
                "title": "Run the OPSEC audit",
                "command": "torus audit",
                "expected": "13 lines: 10 OPSEC items (opsec-1…10, mirrored on the OPSEC tab here) plus 3 extra checks (gateway token mode, Host header, no CRLF in daemons). Every FAIL line ends with the command that fixes it.",
                "verify": "torus audit | grep -q 'FAIL' && echo FIX_FIRST || echo OK",
                "troubleshooting": "WARN is informational (e.g. no Termux:Boot yet). Results are saved to ~/.torus/var/audit.json and shown under Settings → Audit on the phone's web page.",
            },
            {
                "id": "8.5",
                "title": "Choose the boot posture",
                "command": "cat ~/.torus/etc/hardening.conf",
                "expected": "`llm_off_at_boot=true` (Claude is switched off every reboot until you `torus llm on`) and `prune_logs_days=7`.",
                "verify": "grep -q 'llm_off_at_boot' ~/.torus/etc/hardening.conf && echo OK",
                "troubleshooting": "Edit the file and reboot (or `torus restart`) to apply. The audit's opsec-10 check fails if llm_off_at_boot is false.",
            },
            {
                "id": "8.6",
                "title": "Tick the OPSEC checklist (sidebar)",
                "command": "echo 'Open the OPSEC tab on this page and tick each item as torus audit shows it PASS.'",
                "expected": "All 10 OPSEC items green here and 0 FAIL in `torus audit`.",
                "verify": "echo OK",
                "troubleshooting": "Fix every FAIL before Phase 9 — the export carries your current posture forward.",
            },
        ],
        "opsec_checklist": [
            {"id": "opsec-1",  "label": "Termux is the F-Droid build; Play Store build is uninstalled."},
            {"id": "opsec-2",  "label": "No daemon listens on 0.0.0.0 — every port is 127.0.0.1 only."},
            {"id": "opsec-3",  "label": "airgapd listens only on a Unix socket, mode 0600."},
            {"id": "opsec-4",  "label": "API keys are mode 0600 in private storage and never appear in logs."},
            {"id": "opsec-5",  "label": "ML-DSA-65 seal on keys + daemons verifies (torus seal verify)."},
            {"id": "opsec-6",  "label": "Logs rotate at 1 MB and old ones are pruned (prune_logs_days)."},
            {"id": "opsec-7",  "label": "Only the vault lives on shared storage; everything else stays private."},
            {"id": "opsec-8",  "label": "Termux:Boot script present and autostart permission granted."},
            {"id": "opsec-9",  "label": "Installed packages match the pinned list (etc/pkg.manifest)."},
            {"id": "opsec-10", "label": "Claude defaults to off after a reboot (llm_off_at_boot=true)."},
        ],
        "artifacts": {
            "etc/hardening.conf": "# apply at boot\nllm_off_at_boot=true\nprune_logs_days=7\n",
            "docs/OPSEC.md": "# OPSEC Audit — Phase 8\n\n- Every port local-only.\n- Secrets at rest mode 600, ML-DSA-65 sealed; airgapd refuses a key whose seal does not match.\n- 13 checks: `torus audit` (results in var/audit.json).\n",
        },
    },

    # ───────────────────────────── PHASE 9 ─────────────────────────────
    {
        "number": 9,
        "name": "Export + Restore",
        "goal": "One signed tarball with your whole setup (config, vault, index, seal) in Downloads, and a one-command restore on a fresh phone.",
        "gate": "`torus export check <tarball>` says hash + signature OK, and `torus restore` on a fresh Termux brings every daemon back to `run:`.",
        "steps": [
            {
                "id": "9.1",
                "title": "Install Phase 9",
                "command": "torus install 9",
                "expected": "bin/torus_export.py in place, `restore guide: ~/.torus/docs/RESTORE.md`, a note on where exports go, then `Phase 9 done`.",
                "verify": "test -s ~/.torus/docs/RESTORE.md && echo OK",
                "troubleshooting": "If it warns `no ~/storage/downloads`, run `termux-setup-storage` once; until then exports land in ~/.torus/exports.",
            },
            {
                "id": "9.2",
                "title": "Make an export",
                "command": "torus export",
                "expected": "`torus-<date>-<time>.tar.gz` in ~/storage/downloads with its sha256 and a `.sig` (ML-DSA-65) next to it. The export freezes pip (etc/requirements.freeze) and pkg (etc/pkg.manifest) lists first. API keys, the seal secret and the gateway token are NOT inside.",
                "verify": "torus export list | grep -q 'torus-' && echo OK",
                "troubleshooting": "`--with-keys` includes the keys — only onto media you trust. `--no-vault` leaves the vault out for a small config-only export. Index/JetStream/Mongo data is state, not setup, and is rebuilt on the new phone. The web page (Settings → Export) runs the same command.",
            },
            {
                "id": "9.3",
                "title": "Check it before you rely on it",
                "command": "torus export check ~/storage/downloads/torus-*.tar.gz",
                "expected": "`hash OK`, `signature OK` (needs the public key from Phase 8), the file list and the size.",
                "verify": "echo OK",
                "troubleshooting": "`unsigned` means you exported before `torus seal` — run `torus seal` then export again.",
            },
            {
                "id": "9.4",
                "title": "Copy it off the phone",
                "command": "# Phone: Material Files → Downloads → share, or from a laptop:\nadb pull /sdcard/Download/ ~/torus-backup/ 2>/dev/null; ls ~/torus-backup/ | grep torus-",
                "expected": "The .tar.gz and its .sig sit together off the device.",
                "verify": "echo OK",
                "troubleshooting": "Keep the .sig with the tarball — `torus restore` verifies the pair. If adb says 'device unauthorized', unlock the phone and accept the fingerprint prompt.",
            },
            {
                "id": "9.5",
                "title": "Restore on a fresh phone",
                "command": "# On the new phone, after the one-line bootstrap from torus/INSTALL.md:\ntorus restore ~/storage/downloads/torus-<date>.tar.gz",
                "expected": "Hash and signature are checked, anything overwritten is backed up to ~/.torus/backups/restore-<time>/, your files are unpacked, then it says `next: torus install all then torus seal and torus audit`.",
                "verify": "torus install all && torus status | grep -c run:",
                "troubleshooting": "`torus install all` reinstalls packages (etc/pkg.manifest) and Python pins (etc/requirements.freeze) and every daemon. Keys are not restored unless the export used --with-keys: `torus key set anthropic`, then `torus seal`. Full steps: ~/.torus/docs/RESTORE.md (inside the tarball too).",
            },
        ],
        "artifacts": {
            "docs/RESTORE.md": "# Restoring TORUS onto a fresh phone\n\n1. Termux + Termux:Boot from F-Droid, `termux-setup-storage`.\n2. Copy torus-<date>.tar.gz (+ .sig) into Downloads.\n3. Bootstrap TORUS (one line from torus/INSTALL.md).\n4. `torus restore ~/storage/downloads/torus-<date>.tar.gz`\n5. `torus install all`\n6. `torus key set anthropic` unless the export used --with-keys; then `torus seal` and `torus audit`.\n7. `torus doctor`, reboot once, `torus status`.\n",
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
