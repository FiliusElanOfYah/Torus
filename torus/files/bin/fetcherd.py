#!/usr/bin/env python3
"""fetcherd - polite HTTP fetcher for the TORUS mesh.

listens  torus.fetch.request  {url, job_id?, fresh?}   (replies if the sender used request())
emits    torus.parse.request  page body for parserd
         torus.fetch.error    {url, error}

Rules: sets an honest User-Agent, follows redirects, obeys robots.txt,
waits 3 s between hits to the same host, caches pages for 24 h, and never
tries to get around bot checks - it reports them as blocked so you can
solve them yourself in Brave.
"""
import asyncio
import io
import os
import sqlite3
import sys
import time
from urllib import robotparser
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx  # noqa: E402
from torus_common import HOME, ROBOTS_AGENT, USER_AGENT, log, pack, run_daemon  # noqa: E402

NAME = "fetcherd"
CACHE_DB = HOME / "cache" / "fetch.sqlite"
CACHE_TTL = 24 * 3600
MIN_GAP = 3.0
BLOCK_STATUS = {401, 403, 429, 503}
MARKERS = (
    "cf-chl-", "challenge-platform", "/cdn-cgi/challenge", "just a moment...",
    "verify you are human", "are you a robot", "unusual traffic from your computer",
    "captcha-delivery", "px-captcha", "please enable js and disable any ad blocker",
)

CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(CACHE_DB)
db.execute("CREATE TABLE IF NOT EXISTS cache(url TEXT PRIMARY KEY, ts REAL, status INT, final_url TEXT, ctype TEXT, body TEXT)")
db.commit()

client = None
robots = {}   # base url -> (checked_at, RobotFileParser | None)
locks = {}    # host -> asyncio.Lock
last_hit = {}  # host -> monotonic time


async def on_start(nc):
    global client
    client = httpx.AsyncClient(
        follow_redirects=True, max_redirects=8,
        timeout=httpx.Timeout(25, connect=10),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )


async def polite_get(url):
    host = urlparse(url).netloc
    async with locks.setdefault(host, asyncio.Lock()):
        wait = last_hit.get(host, 0) + MIN_GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            return await client.get(url)
        finally:
            last_hit[host] = time.monotonic()


async def robots_ok(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    ent = robots.get(base)
    if not ent or time.time() - ent[0] > 3600:
        rp = None
        try:
            r = await polite_get(base + "/robots.txt")
            if r.status_code == 200:
                rp = robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
        except httpx.HTTPError:
            rp = None
        ent = robots[base] = (time.time(), rp)
    return ent[1] is None or ent[1].can_fetch(ROBOTS_AGENT, url)


def looks_blocked(status, body):
    if status in BLOCK_STATUS:
        return True
    low = body[:200_000].lower()
    return len(low) < 60_000 and any(m in low for m in MARKERS)


def pdf_text(content):
    from pdfminer.high_level import extract_text  # optional: pip install pdfminer.six
    return extract_text(io.BytesIO(content))


async def fetch(url, fresh, limit):
    out = {"ok": False, "url": url, "final_url": url, "status": 0, "content_type": "",
           "body": "", "blocked": False, "robots_blocked": False, "cached": False, "error": ""}
    if not fresh:
        row = db.execute("SELECT ts, status, final_url, ctype, body FROM cache WHERE url=?", (url,)).fetchone()
        if row and time.time() - row[0] < CACHE_TTL:
            out.update(ok=True, status=row[1], final_url=row[2], content_type=row[3], body=row[4], cached=True)
            return out
    if not await robots_ok(url):
        out.update(robots_blocked=True, error="robots.txt asks bots not to fetch this page")
        return out
    try:
        r = await polite_get(url)
    except httpx.HTTPError as e:
        out["error"] = f"{e.__class__.__name__}: {e}"
        return out
    ctype = r.headers.get("content-type", "")
    out.update(status=r.status_code, final_url=str(r.url), content_type=ctype)
    if "pdf" in ctype.lower() or str(r.url).lower().endswith(".pdf"):
        try:
            body = await asyncio.to_thread(pdf_text, r.content)
            out["content_type"] = "text/plain; from=pdf"
        except Exception as e:
            out["error"] = f"PDF text extraction failed ({e.__class__.__name__}); pip install pdfminer.six"
            return out
    else:
        body = r.text
    out["body"] = body.encode("utf-8")[:limit].decode("utf-8", "ignore")
    if looks_blocked(r.status_code, body):
        out.update(blocked=True, error=f"bot check or block (HTTP {r.status_code})")
        return out
    if r.status_code >= 400:
        out["error"] = f"HTTP {r.status_code}"
        return out
    out["ok"] = True
    db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?)",
               (url, time.time(), out["status"], out["final_url"], out["content_type"], out["body"]))
    db.commit()
    return out


async def on_fetch(nc, msg, data):
    url = (data.get("url") or "").strip()
    limit = int(nc.max_payload * 0.75)
    if not url.startswith(("http://", "https://")):
        out = {"ok": False, "url": url, "error": "not an http(s) url"}
    else:
        out = await fetch(url, bool(data.get("fresh")), limit)
    out["job_id"] = data.get("job_id")
    log(NAME, "ok" if out["ok"] else "fail", url, out.get("status"), out.get("error", ""), "(cache)" if out.get("cached") else "")
    if out["ok"]:
        await nc.publish("torus.parse.request", pack({k: out.get(k) for k in ("url", "final_url", "status", "content_type", "body", "job_id")}))
    else:
        await nc.publish("torus.fetch.error", pack({"url": url, "error": out.get("error"), "job_id": out["job_id"]}))
    if msg.reply:
        await nc.publish(msg.reply, pack(out))


if __name__ == "__main__":
    run_daemon(NAME, {"torus.fetch.request": on_fetch}, concurrent=True, on_start=on_start)
