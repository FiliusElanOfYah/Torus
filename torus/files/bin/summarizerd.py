#!/usr/bin/env python3
"""summarizerd - pure-Python TextRank summaries (no scikit-learn, no networkx).

listens  torus.index.request  {url, markdown}
emits    torus.vault.summary  {url, summary}

Sentences full of math markup are dropped; recurring proper nouns get a boost.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import log, pack, run_daemon, textrank  # noqa: E402

NAME = "summarizerd"


async def on_page(nc, msg, data):
    url = data.get("url")
    top = await asyncio.to_thread(textrank, data.get("markdown") or "", 6)
    if len(top) < 2:
        log(NAME, "too short to summarise", url)
        return
    await nc.publish("torus.vault.summary", pack({"url": url, "summary": "\n".join(f"- {s}" for s in top)}))
    log(NAME, "summarised", url)


if __name__ == "__main__":
    run_daemon(NAME, {"torus.index.request": on_page})
