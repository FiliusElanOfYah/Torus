# TORUS

A local research and code assistant that runs on a Samsung Galaxy S26 Ultra in
Termux: a mesh of small Python daemons on a local NATS bus, supervised by runit,
with a web page at `http://127.0.0.1:7777` that you open in Brave.

**Start here:** [INSTALL.md](INSTALL.md)

```
torus/
  bootstrap.sh     one-line installer (clones this repo to ~/.torus/src)
  bin/torus        the CLI: doctor, install, update, rollback, ask, keys, switches
  lib/common.sh    safe writer: whole files, backup first, CRLF stripped, idempotent
  manifest.txt     every file TORUS installs, its phase, and whether it's "own" or "keep"
  services.txt     every runit service and its run template
  phases/          0-base ... 6-gateway (each safe to re-run)
  files/           the canonical sources: daemons, web page, run scripts, configs
  tests/           run_local_mesh.sh: run the mesh on a normal Linux box
```

## Message flow

```
torus ask / web page --> gatewayd --request--> fetcherd --> parserd --> indexerd (SQLite FTS5)
                             |                                     \--> vaultd (Downloads/vault/inbox)
                             |                                      \-> summarizerd --> vaultd (.summary.md)
                             +--> torus.llm.request --> sentineld (redact + audit) --> llmd --> Claude API
                             |                                          llmd <-- airgapd (keys over 0600 socket)
                             +--> answers/<job>.md
```

Subjects: `torus.fetch.request`, `torus.parse.request`, `torus.index.request|search|get`,
`torus.vault.request|summary`, `torus.llm.request|egress|models`.
