#!/usr/bin/env python3
"""airgapd - the only process that reads API keys.

Keys live in ~/.torus/airgap/<name>.key (folder 0700, files 0600), written by
`torus key set <name>`. Other daemons ask for a key over the unix socket
~/.torus/airgap/airgap.sock (mode 0600): send "<name>\\n", receive the key.
Every access is logged (name only, never the value) to ~/.torus/logs/airgapd.jsonl.

Phase 8: once `torus seal` has signed the airgap manifest, a key is only handed out
when its file still matches the sealed hash and the ML-DSA-65 signature verifies.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import AIRGAP, AIRGAP_SOCK, LOGS, append_jsonl, log  # noqa: E402

try:
    from torus_seal import KeyGuard  # noqa: E402
    GUARD = KeyGuard()
except ImportError:  # phase 8 not installed yet: unsealed mode
    GUARD = None

NAME = "airgapd"
ALLOWED = {"anthropic", "brave", "cloudflare"}


async def handle(reader, writer):
    name, ok, why = "?", False, ""
    try:
        name = (await asyncio.wait_for(reader.readline(), 5)).decode("utf-8", "ignore").strip()
        p = AIRGAP / f"{name}.key"
        if name in ALLOWED and p.is_file():
            why = GUARD.check(p) if GUARD else ""
            if why:
                log(NAME, "REFUSED", name, "-", why)
                writer.write(f"ERR sealed: {why}\n".encode())
            else:
                writer.write(p.read_text(encoding="utf-8").strip().encode())
                ok = True
        else:
            writer.write(b"ERR missing\n")
        await writer.drain()
    except Exception as e:
        log(NAME, "error", e.__class__.__name__)
    finally:
        writer.close()
        append_jsonl(LOGS / "airgapd.jsonl", {"t": time.time(), "name": name, "ok": ok, **({"refused": why} if why else {})})


async def main():
    AIRGAP.mkdir(parents=True, exist_ok=True)
    os.chmod(AIRGAP, 0o700)
    for k in AIRGAP.glob("*.key"):
        os.chmod(k, 0o600)
    if AIRGAP_SOCK.exists():
        AIRGAP_SOCK.unlink()
    old = os.umask(0o177)
    server = await asyncio.start_unix_server(handle, path=str(AIRGAP_SOCK))
    os.umask(old)
    os.chmod(AIRGAP_SOCK, 0o600)
    log(NAME, "up on", AIRGAP_SOCK, "keys:", ", ".join(sorted(k.stem for k in AIRGAP.glob("*.key"))) or "none",
        "| seal:", ("checked per request" if GUARD and (AIRGAP / "MANIFEST.json").exists() else "not sealed (torus seal)"))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
