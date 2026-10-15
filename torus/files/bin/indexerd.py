#!/usr/bin/env python3
"""indexerd - SQLite FTS5 full-text index at ~/.torus/index.sqlite.

listens  torus.index.request  {url, title, markdown}   one row per URL (re-index replaces)
         torus.index.search   {q, limit}  -> reply {results:[{url,title,snippet}]}
         torus.index.get      {url}       -> reply {url,title,body} or {}

On start it removes duplicate rows left by older versions (keeps the newest).
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import INDEX_DB, log, pack, run_daemon  # noqa: E402

NAME = "indexerd"

con = sqlite3.connect(INDEX_DB)
con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(url, title, body)")
COLS = [r[1] for r in con.execute("PRAGMA table_info(pages)")]
HAS_TITLE = "title" in COLS
BODY_IDX = COLS.index("body")
before = con.execute("SELECT count(*) FROM pages").fetchone()[0]
con.execute("DELETE FROM pages WHERE rowid NOT IN (SELECT max(rowid) FROM pages GROUP BY url)")
con.commit()
after = con.execute("SELECT count(*) FROM pages").fetchone()[0]
log(NAME, f"index has {after} pages (removed {before - after} duplicates)")


async def on_index(nc, msg, data):
    url, body = data.get("url"), data.get("markdown") or ""
    if not url or not body:
        return
    con.execute("DELETE FROM pages WHERE url = ?", (url,))
    if HAS_TITLE:
        con.execute("INSERT INTO pages(url, title, body) VALUES (?,?,?)", (url, data.get("title") or url, body))
    else:
        con.execute("INSERT INTO pages(url, body) VALUES (?,?)", (url, body))
    con.commit()
    log(NAME, "indexed", url)


def fts_query(q):
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", q or "")][:12]
    return " OR ".join(f'"{w}"' for w in words)


async def on_search(nc, msg, data):
    q = fts_query(data.get("q"))
    results = []
    if q:
        limit = max(1, min(25, int(data.get("limit") or 5)))
        title = "title" if HAS_TITLE else "url"
        rows = con.execute(
            f"SELECT url, {title}, snippet(pages, {BODY_IDX}, '[', ']', '...', 24) FROM pages "
            f"WHERE pages MATCH ? ORDER BY bm25(pages) LIMIT ?", (q, limit)).fetchall()
        results = [{"url": u, "title": t, "snippet": s} for u, t, s in rows]
    if msg.reply:
        await nc.publish(msg.reply, pack({"results": results}))


async def on_get(nc, msg, data):
    title = "title" if HAS_TITLE else "url"
    row = con.execute(f"SELECT url, {title}, body FROM pages WHERE url = ? LIMIT 1", (data.get("url"),)).fetchone()
    if msg.reply:
        await nc.publish(msg.reply, pack({"url": row[0], "title": row[1], "body": row[2]} if row else {}))


if __name__ == "__main__":
    run_daemon(NAME, {"torus.index.request": on_index, "torus.index.search": on_search, "torus.index.get": on_get})
