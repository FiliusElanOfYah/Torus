#!/usr/bin/env python3
"""parserd - turns fetched pages into clean text.

listens  torus.parse.request  {url, final_url, content_type, body, job_id?, source?}
emits    torus.index.request + torus.vault.request  {url, final_url, title, markdown, job_id, source}

A page that fails to parse is logged and skipped. It never stops the daemon.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import extract, log, pack, run_daemon  # noqa: E402

NAME = "parserd"


async def on_parse(nc, msg, data):
    url = data.get("url") or ""
    final = data.get("final_url") or url
    title, text = await asyncio.to_thread(extract, data.get("body") or "", final, data.get("content_type") or "")
    if len(text.strip()) < 80:
        log(NAME, "skip (no readable text)", url)
        return
    out = {"url": url, "final_url": final, "title": title or url, "markdown": text,
           "job_id": data.get("job_id"), "source": data.get("source") or "web"}
    await nc.publish("torus.index.request", pack(out))
    await nc.publish("torus.vault.request", pack(out))
    log(NAME, "parsed", url, f"{len(text)} chars")


if __name__ == "__main__":
    run_daemon(NAME, {"torus.parse.request": on_parse})
