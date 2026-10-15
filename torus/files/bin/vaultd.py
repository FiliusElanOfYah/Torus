#!/usr/bin/env python3
"""vaultd - writes Markdown dossiers into ~/.torus/vault/inbox (your Downloads/vault).

listens  torus.vault.request  {url, title, markdown, job_id?, source?}  -> <stamp>-<slug>.md
         torus.vault.summary  {url, summary}  -> <dossier>.summary.md next to the dossier

The same page with the same text is not written twice. If a summary arrives
before its dossier, it waits and is written as soon as the dossier lands.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import VAR, VAULT, atomic_write, log, run_daemon, slugify  # noqa: E402

NAME = "vaultd"
INBOX = VAULT / "inbox"
INBOX.mkdir(parents=True, exist_ok=True)
MAP_FILE = VAR / "vault-map.json"
pending = {}  # url -> summary text


def load_map():
    try:
        return json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def write_summary(dossier, url, summary):
    p = Path(dossier)
    out = p.with_name(p.stem + ".summary.md")
    atomic_write(out, f"# Summary: {url}\n\n{summary}\n")
    log(NAME, "summary", out.name)


async def on_dossier(nc, msg, data):
    url, md = data.get("url"), data.get("markdown") or ""
    if not url or not md:
        return
    digest = hashlib.sha256(md.encode("utf-8")).hexdigest()[:16]
    m = load_map()
    ent = m.get(url)
    if ent and ent.get("hash") == digest and Path(ent["path"]).exists():
        log(NAME, "unchanged", url)
        path = ent["path"]
    else:
        title = (data.get("title") or url).replace("\n", " ")
        path = str(INBOX / f"{time.strftime('%Y%m%d-%H%M%S')}-{slugify(url)}.md")
        front = (f"---\nurl: {url}\ntitle: {json.dumps(title)}\nfetched: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n"
                 f"source: {data.get('source') or 'web'}\njob: {data.get('job_id') or ''}\n---\n\n")
        atomic_write(path, f"{front}# {title}\n\n{md}\n")
        m[url] = {"path": path, "hash": digest}
        atomic_write(MAP_FILE, json.dumps(m, indent=1))
        log(NAME, "dossier", Path(path).name)
    if url in pending:
        write_summary(path, url, pending.pop(url))


async def on_summary(nc, msg, data):
    url, summary = data.get("url"), data.get("summary") or ""
    if not url or not summary:
        return
    ent = load_map().get(url)
    if ent and Path(ent["path"]).exists():
        write_summary(ent["path"], url, summary)
    else:
        pending[url] = summary


if __name__ == "__main__":
    run_daemon(NAME, {"torus.vault.request": on_dossier, "torus.vault.summary": on_summary})
