#!/usr/bin/env python3
"""gatewayd - TORUS web page + API at http://127.0.0.1:7777 (open it in Brave).

Pure standard-library HTTP server (no FastAPI/uvicorn/uvloop to build on Termux).
Binds to 127.0.0.1 only. Rejects any request whose Host header is not
127.0.0.1/localhost (stops DNS-rebinding). Every POST must carry the header
X-Torus: 1, which a random website open in Brave cannot send without a CORS
preflight that this server never approves.

Ask pipeline (one job per question, saved in ~/.torus/var/jobs, resumes after a reboot):
  1 sources   pasted URLs and/or Brave Search (toggle) + matching pages already in your vault
  2 fetch     through fetcherd (robots.txt, rate limit, cache). Bot checks -> "Needs you"
  3 needs you you open the page in Brave, solve it, paste the page back (or skip)
  4 answer    Claude via sentineld -> llmd when on; else / on failure a local
              extractive summary, so a session never hard-stops
  5 dossier   ~/.torus/vault/answers/<job>.md
"""
import asyncio
import json
import os
import re
import secrets
import sys
import time
import traceback
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx  # noqa: E402
import gateway_code  # noqa: E402
from torus_cite import citation_map, extract_links  # noqa: E402
from torus_common import (ETC, HOME, LLM_TOGGLE, SEARCH_CONF, VAR, VAULT, atomic_write, connect_nats,  # noqa: E402
                          extract, get_secret, key_present, llm_settings, log, pack, rank_sentences, read_conf,
                          search_settings, slugify, split_sentences, write_conf)
from torus_report import build_pdf  # noqa: E402

NAME = "gatewayd"
HOST = "127.0.0.1"
PORT = int(os.environ.get("TORUS_PORT", "7777"))
WEB = HOME / "web" / "index.html"
JOBS_DIR = VAR / "jobs"
ANSWERS = VAULT / "answers"
REPORTS = VAULT / "reports"
TOKEN_FILE = ETC / "gateway.token"
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
MAX_BODY = 12_000_000
MAX_SRC_CHARS = 14_000
MAX_SOURCES = 10
MAX_TOTAL_SOURCES = 20
SERVICES = ["nats-server", "torus-mongo", "fetcherd", "parserd", "indexerd", "vaultd",
            "summarizerd", "airgapd", "sentineld", "llmd", "gatewayd", "coderd"]
ACTIVE = {"queued", "searching", "fetching", "needs_you", "thinking"}
SYSTEM = ("You are TORUS, a careful research assistant running for one person on their phone. "
          "Answer the question using the numbered sources. Cite every factual claim with [n]. "
          "If sources disagree, say so. If the sources do not cover something, say so plainly and "
          "label any outside knowledge as such. Use concise Markdown: a direct answer first, then "
          "details, then a short 'Gaps' line if anything is missing.")

STATE = {"nc": None, "token": ""}
jobs, events, tasks = {}, {}, {}


class Raw:
    """Non-JSON response body (the web page, a PDF)."""

    def __init__(self, data, ctype, filename=None):
        self.data, self.ctype, self.filename = data, ctype, filename


def load_token():
    """Code endpoints can write files and run commands, so they need this secret.
    Only Termux can read the file; `torus open` hands it to Brave in the URL fragment."""
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(TOKEN_FILE, secrets.token_hex(16) + "\n")
    os.chmod(TOKEN_FILE, 0o600)
    STATE["token"] = TOKEN_FILE.read_text().strip()


# ---------------------------------------------------------------- jobs storage
def save_job(j):
    j["updated"] = time.time()
    atomic_write(JOBS_DIR / f"{j['id']}.json", json.dumps(j, ensure_ascii=False))


def load_jobs():
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(JOBS_DIR.glob("*.json"))[-300:]:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            jobs[j["id"]] = j
        except (ValueError, KeyError):
            log(NAME, "unreadable job file", p.name)


def public_job(j):
    out = {k: v for k, v in j.items() if k != "sources"}
    out["sources"] = [{k: v for k, v in s.items() if k not in ("text", "links")} |
                      {"preview": (s.get("text") or "")[:600], "links_count": len(s.get("links") or [])}
                      for s in j.get("sources", [])]
    out["running"] = j["id"] in tasks
    return out


def current_turn(j):
    """The question being answered now: the original, or the latest follow-up."""
    return j["followups"][-1] if j.get("followups") else j


def note(j, text):
    j.setdefault("log", []).append(f"{time.strftime('%H:%M:%S')} {text}")
    j["log"] = j["log"][-80:]
    save_job(j)


# ---------------------------------------------------------------- mesh helpers
async def nats_loop():
    STATE["nc"] = await connect_nats(NAME)
    log(NAME, "connected to NATS")


async def nreq(subject, obj, timeout):
    nc = STATE["nc"]
    while nc is None or not nc.is_connected:
        await asyncio.sleep(2)
        nc = STATE["nc"]
    msg = await nc.request(subject, pack(obj), timeout=timeout)
    return json.loads(msg.data)


async def publish(subject, obj):
    nc = STATE["nc"]
    if nc is not None and nc.is_connected:
        await nc.publish(subject, pack(obj))


async def brave_search(q, count):
    key = await get_secret("brave")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": q, "count": count},
                        headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
        return r.json().get("web", {}).get("results", [])


# ---------------------------------------------------------------- pipeline
def add_source(j, url, origin, limit=MAX_SOURCES, **extra):
    url = (url or "").strip()
    if not url or any(s["url"] == url for s in j["sources"]) or len(j["sources"]) >= limit:
        return None
    s = {"n": len(j["sources"]) + 1, "url": url, "origin": origin, "status": "pending",
         "title": "", "error": "", "reason": "", "chars": 0, "text": ""} | extra
    j["sources"].append(s)
    return s


async def step_sources(j):
    if j.get("sources_ready"):
        return
    j["status"], j["sources"] = "searching", []
    note(j, f"finding sources (mode: {j['mode']})")
    if j["mode"] in ("urls", "both"):
        for u in j.get("urls", []):
            add_source(j, u, "pasted")
    if j["mode"] in ("brave", "both"):
        if key_present("brave"):
            try:
                for r in await brave_search(j["question"], search_settings()["count"]):
                    add_source(j, r.get("url"), "brave", title=r.get("title", ""))
                note(j, "Brave Search done")
            except Exception as e:
                j["warnings"].append(f"Brave Search failed: {e}")
        else:
            j["warnings"].append("Brave Search is on but no key is stored. In Termux: torus key set brave")
    try:
        hits = (await asyncio.wait_for(nreq("torus.index.search", {"q": j["question"], "limit": 3}, 10), 12)).get("results", [])
        for h in hits:
            full = await nreq("torus.index.get", {"url": h["url"]}, 10)
            if full.get("body"):
                add_source(j, h["url"], "vault", title=full.get("title") or h["url"], status="ok",
                           text=full["body"][:MAX_SRC_CHARS], chars=len(full["body"]))
    except Exception as e:
        j["warnings"].append(f"vault search skipped ({e.__class__.__name__}); is indexerd running?")
    j["sources_ready"] = True
    note(j, f"{len(j['sources'])} sources")


async def step_fetch(j):
    j["status"] = "fetching"
    for s in j["sources"]:
        if s["status"] not in ("pending", "fetching"):
            continue
        s["status"] = "fetching"
        note(j, f"fetching [{s['n']}] {s['url']}")
        try:
            res = await nreq("torus.fetch.request", {"url": s["url"], "job_id": j["id"], "fresh": j.get("fresh", False)}, 120)
        except Exception as e:
            s.update(status="error", error=f"fetcherd did not answer ({e.__class__.__name__}); check: torus status")
            continue
        if res.get("robots_blocked"):
            s.update(status="robots", reason="robots.txt asks bots to stay out. You can open it yourself and paste it.")
        elif res.get("blocked"):
            s.update(status="needs_you", reason=f"Bot check or block (HTTP {res.get('status')}). Solve it in Brave, then paste the page.")
        elif not res.get("ok"):
            s.update(status="error", error=res.get("error") or "fetch failed")
        else:
            title, text = await asyncio.to_thread(extract, res.get("body") or "", res.get("final_url") or s["url"], res.get("content_type") or "")
            if "html" in (res.get("content_type") or "").lower():
                s["links"] = extract_links(res.get("body") or "", res.get("final_url") or s["url"])
            if len(text) < 200:
                s.update(status="needs_you", reason="The page came back almost empty (it probably needs JavaScript). Open it in Brave and paste it.")
            else:
                s.update(status="ok", title=title or s["title"] or s["url"], text=text[:MAX_SRC_CHARS], chars=len(text), cached=res.get("cached", False))
        save_job(j)


async def step_wait_user(j):
    while any(s["status"] == "needs_you" for s in j["sources"]):
        if j["status"] != "needs_you":
            j["status"] = "needs_you"
            note(j, "waiting for you: open the job in Brave")
        ev = events.setdefault(j["id"], asyncio.Event())
        ev.clear()
        await ev.wait()


def build_prompt(question, srcs):
    parts = [f"Question: {question}\n", "Sources (cite as [n]):"]
    for s in srcs:
        parts.append(f"\n[{s['n']}] {s.get('title') or s['url']} ({s['url']})\n{s['text']}")
    return "\n".join(parts)


def local_answer(question, srcs):
    pairs = []
    for s in srcs:
        pairs += [(s["n"], sent) for sent in split_sentences(s["text"])[:250]]
    if not pairs:
        return "_No readable sentences in the sources._"
    idx = rank_sentences([p[1] for p in pairs], k=8, query=question)
    lines = "\n".join(f"- {pairs[i][1]} [{pairs[i][0]}]" for i in idx)
    return f"**Local summary** (extractive, no LLM). The most relevant sentences from your sources:\n\n{lines}"


def build_messages(j, srcs):
    """Whole conversation for Claude: sources + first question, then each earlier follow-up and answer."""
    first = build_prompt(j["question"], srcs) if srcs else (
        f"Question: {j['question']}\n\n(No sources could be read. Answer from general knowledge and say so clearly.)")
    turns = [j] + j.get("followups", [])
    msgs = []
    for i, t in enumerate(turns):
        msgs.append({"role": "user", "content": first if i == 0 else
                     f"Follow-up question: {t['question']}\n(Use the same numbered sources; new ones may have been added above.)"})
        if i < len(turns) - 1:
            msgs.append({"role": "assistant", "content": t.get("answer") or "(no answer)"})
    return msgs


async def step_answer(j):
    t = current_turn(j)
    j["status"], t["answer"], t["engine"] = "thinking", "", ""
    t.setdefault("warnings", [])
    note(j, "writing the answer" + (" to the follow-up" if t is not j else ""))
    srcs = [s for s in j["sources"] if s["status"] == "ok" and s.get("text")]
    st = llm_settings()
    model = t.get("model") or j.get("model") or st["model"]
    if st["enabled"] and key_present("anthropic"):
        try:
            res = await nreq("torus.llm.request", {"messages": build_messages(j, srcs), "system": SYSTEM,
                                                   "model": model, "job_id": j["id"]}, 900)
        except Exception as e:
            res = {"ok": False, "error": f"sentineld/llmd did not answer ({e.__class__.__name__})"}
        if res.get("ok") and res.get("text"):
            t["answer"], t["engine"] = res["text"], f"claude:{res.get('model')}"
        else:
            t["warnings"].append(f"Claude unavailable: {res.get('error')}. Used the local summary instead.")
    elif st["enabled"]:
        t["warnings"].append("LLM is on but no Anthropic key is stored. In Termux: torus key set anthropic")
    if not t["answer"]:
        t["engine"] = "local"
        t["answer"] = local_answer(t["question"], srcs) if srcs else (
            "No readable sources and the LLM is off. Paste some URLs, turn on Brave Search, or turn the LLM on.")
    write_dossier(j)


def write_dossier(j):
    path = ANSWERS / f"{j['id']}-{slugify(j['question'], 50)}.md"
    src_lines = "\n".join(f"{s['n']}. [{s['status']}] {s.get('title') or s['url']} - {s['url']}" for s in j["sources"])
    follow = "".join(f"\n\n## Follow-up: {t['question']}\n\n{t.get('answer') or ''}" for t in j.get("followups", []))
    atomic_write(path, f"---\njob: {j['id']}\nengine: {j['engine']}\ncreated: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n---\n\n"
                       f"# {j['question']}\n\n{j['answer']}{follow}\n\n## Sources\n\n{src_lines}\n")
    j["answer_path"] = str(path)


async def run_job(j, mode="full"):
    try:
        if mode == "full":
            await step_sources(j)
        if mode in ("full", "followup"):
            await step_fetch(j)
            await step_wait_user(j)
        await step_answer(j)
        j["status"] = "done"
        note(j, f"done ({current_turn(j)['engine']})")
    except asyncio.CancelledError:
        j["status"] = "cancelled"
        note(j, "cancelled")
        raise
    except Exception as e:
        j["status"], j["error"] = "error", f"{e.__class__.__name__}: {e}"
        note(j, "error: " + j["error"])
        log(NAME, traceback.format_exc())
    finally:
        tasks.pop(j["id"], None)


def start(j, mode="full"):
    tasks[j["id"]] = asyncio.create_task(run_job(j, mode))


# ---------------------------------------------------------------- API handlers
def err(status, msg):
    return status, {"error": msg}


async def api_ask(body):
    q = (body.get("question") or "").strip()
    if not q:
        return err(400, "question is required")
    urls = body.get("urls") or []
    if isinstance(urls, str):
        urls = urls.split()
    urls = [u.strip() for u in urls if u.strip().startswith(("http://", "https://"))][:MAX_SOURCES]
    mode = body.get("mode") or search_settings()["mode"]
    if mode not in ("urls", "brave", "both"):
        return err(400, "mode must be urls, brave or both")
    model = body.get("model") or None
    if model and model not in ("sonnet", "opus"):
        return err(400, "model must be sonnet or opus")
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    j = {"id": jid, "question": q, "urls": urls, "mode": mode, "model": model, "fresh": bool(body.get("fresh")),
         "status": "queued", "created": time.time(), "sources": [], "warnings": [], "log": [],
         "answer": "", "engine": "", "error": "", "followups": []}
    jobs[jid] = j
    save_job(j)
    start(j)
    return 200, {"id": jid}


def find_source(j, body):
    try:
        n = int(body.get("n"))
    except (TypeError, ValueError):
        return None
    return next((s for s in j["sources"] if s["n"] == n), None)


async def api_paste(j, body):
    s = find_source(j, body)
    content = body.get("content") or ""
    if not s:
        return err(404, "no such source")
    if len(content.strip()) < 50:
        return err(400, "paste the page text (at least a few sentences)")
    is_html = "<html" in content[:3000].lower() or "<body" in content[:20000].lower() or "<div" in content[:5000].lower()
    title, text = await asyncio.to_thread(extract, content, s["url"], "text/html" if is_html else "text/plain")
    s.update(status="ok", text=text[:MAX_SRC_CHARS], chars=len(text), title=title or s.get("title") or s["url"],
             origin=s["origin"] + "+pasted", reason="", error="")
    if is_html:
        s["links"] = extract_links(content, s["url"])
    note(j, f"you pasted [{s['n']}] ({len(text)} chars)")
    await publish("torus.parse.request", {"url": s["url"], "final_url": s["url"], "body": content,
                                          "content_type": "text/html" if is_html else "text/plain",
                                          "job_id": j["id"], "source": "pasted"})
    events.setdefault(j["id"], asyncio.Event()).set()
    return 200, public_job(j)


async def api_skip(j, body):
    targets = [s for s in j["sources"] if s["status"] == "needs_you"] if body.get("all") else [find_source(j, body)]
    for s in targets:
        if s and s["status"] in ("needs_you", "robots", "error"):
            s.update(status="skipped")
    note(j, "skipped " + ("all waiting sources" if body.get("all") else f"[{body.get('n')}]"))
    events.setdefault(j["id"], asyncio.Event()).set()
    return 200, public_job(j)


async def api_rerun(j, body):
    if j["id"] in tasks:
        return err(409, "job is still running")
    model = body.get("model")
    if model and model not in ("sonnet", "opus"):
        return err(400, "model must be sonnet or opus")
    t = current_turn(j)
    if t is j:
        j["model"] = model or j.get("model")
    else:
        t["model"] = model or t.get("model")
    t["warnings"] = []
    j["status"] = "queued"
    start(j, "answer")
    return 200, public_job(j)


async def api_followup(j, body):
    if j["id"] in tasks:
        return err(409, "job is still running; wait for it to finish")
    q = (body.get("question") or "").strip()
    if not q:
        return err(400, "question is required")
    model = body.get("model") or None
    if model and model not in ("sonnet", "opus"):
        return err(400, "model must be sonnet or opus")
    urls = body.get("urls") or []
    if isinstance(urls, str):
        urls = urls.split()
    added = [add_source(j, u.strip(), "follow-up", limit=MAX_TOTAL_SOURCES) for u in urls
             if u.strip().startswith(("http://", "https://"))]
    j.setdefault("followups", []).append({"question": q, "answer": "", "engine": "", "warnings": [],
                                          "model": model, "created": time.time()})
    note(j, f"follow-up: {q[:120]}" + (f" (+{sum(1 for a in added if a)} new sources)" if added else ""))
    j["status"] = "queued"
    start(j, "followup")
    return 200, public_job(j)


def write_report(j):
    pdf = build_pdf(j, citation_map(j))
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{j['id']}-{slugify(j['question'], 50)}.pdf"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(pdf)
    os.replace(tmp, path)
    j["report_path"] = str(path)
    save_job(j)
    return pdf, path


async def api_cancel(j):
    t = tasks.get(j["id"])
    if t:
        t.cancel()
    return 200, {"ok": True}


async def sv_status():
    try:
        p = await asyncio.create_subprocess_exec("sv", "status", *SERVICES, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), 10)
    except (FileNotFoundError, asyncio.TimeoutError) as e:
        return [{"name": s, "state": "unknown", "raw": e.__class__.__name__} for s in SERVICES]
    rows = []
    for name, line in zip(SERVICES, out.decode(errors="ignore").splitlines()):
        rows.append({"name": name, "state": line.split(":", 1)[0].strip(), "raw": line.strip()})
    return rows


def settings_payload():
    llm, srch = llm_settings(), search_settings()
    c = read_conf(LLM_TOGGLE)
    return {"llm_enabled": llm["enabled"], "model": llm["model"], "model_id": llm["model_id"],
            "sonnet_id": c.get("sonnet_id") or "claude-sonnet-5-5", "opus_id": c.get("opus_id") or "claude-opus-5-5",
            "search_mode": srch["mode"], "search_count": srch["count"],
            "keys": {"anthropic": key_present("anthropic"), "brave": key_present("brave")}}


async def api_settings(body):
    if "llm_enabled" in body:
        write_conf(LLM_TOGGLE, {"enabled": "true" if body["llm_enabled"] else "false"})
    if "model" in body:
        if body["model"] not in ("sonnet", "opus"):
            return err(400, "model must be sonnet or opus")
        write_conf(LLM_TOGGLE, {"model": body["model"]})
    if "search_mode" in body:
        if body["search_mode"] not in ("urls", "brave", "both"):
            return err(400, "search_mode must be urls, brave or both")
        write_conf(SEARCH_CONF, {"mode": body["search_mode"]})
    if "search_count" in body:
        try:
            write_conf(SEARCH_CONF, {"count": max(1, min(10, int(body["search_count"])))})
        except (TypeError, ValueError):
            return err(400, "search_count must be a number 1-10")
    return 200, settings_payload()


async def route(method, path, query, body, headers=None):
    if method == "GET" and path in ("/", "/index.html"):
        page = WEB.read_bytes() if WEB.exists() else b"<h1>TORUS</h1><p>web/index.html missing: torus install 6</p>"
        return 200, Raw(page, "text/html; charset=utf-8")
    if path.startswith("/api/code"):
        if not secrets.compare_digest((headers or {}).get("x-torus-token", ""), STATE["token"]):
            return err(401, "Code needs your TORUS token. Open the page from Termux with: torus open")
        return await gateway_code.route(method, path, query, body)
    if path == "/api/health":
        nc = STATE["nc"]
        return 200, {"ok": True, "nats": bool(nc and nc.is_connected), "jobs": len(jobs), "running": len(tasks)}
    if method == "GET" and path == "/api/status":
        return 200, {"services": await sv_status(), "settings": settings_payload()}
    if path == "/api/settings":
        return (200, settings_payload()) if method == "GET" else await api_settings(body)
    if method == "POST" and path == "/api/ask":
        return await api_ask(body)
    if method == "GET" and path == "/api/jobs":
        rows = sorted(jobs.values(), key=lambda j: j["created"], reverse=True)[:60]
        return 200, {"jobs": [{k: j.get(k) for k in ("id", "question", "status", "created", "engine")} |
                              {"needs_you": sum(1 for s in j["sources"] if s["status"] == "needs_you")} for j in rows]}
    if method == "GET" and path == "/api/search":
        q = (query.get("q") or [""])[0]
        try:
            return 200, await nreq("torus.index.search", {"q": q, "limit": 10}, 10)
        except Exception as e:
            return err(503, f"indexerd did not answer ({e.__class__.__name__})")
    m = re.fullmatch(r"/api/jobs/([0-9A-Za-z-]+)(?:/(paste|skip|rerun|cancel|followup|citations|report|report\.pdf))?", path)
    if m:
        j = jobs.get(m.group(1))
        if not j:
            return err(404, "no such job")
        action = m.group(2)
        if method == "GET" and not action:
            return 200, public_job(j)
        if method == "GET" and action == "citations":
            return 200, citation_map(j)
        if method == "GET" and action == "report.pdf":
            pdf, path_ = await asyncio.to_thread(write_report, j)
            return 200, Raw(pdf, "application/pdf", path_.name)
        if method == "POST" and action == "report":
            _, path_ = await asyncio.to_thread(write_report, j)
            note(j, f"PDF report saved: {path_}")
            return 200, {"path": str(path_)}
        if method == "POST" and action == "followup":
            return await api_followup(j, body)
        if method == "POST" and action == "paste":
            return await api_paste(j, body)
        if method == "POST" and action == "skip":
            return await api_skip(j, body)
        if method == "POST" and action == "rerun":
            return await api_rerun(j, body)
        if method == "POST" and action == "cancel":
            return await api_cancel(j)
    return err(404, "not found")


# ---------------------------------------------------------------- tiny HTTP server
REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict",
           413: "Payload Too Large", 500: "Internal Server Error", 503: "Service Unavailable"}


async def handle(reader, writer):
    status, payload = 500, {"error": "internal error"}
    try:
        line = await asyncio.wait_for(reader.readline(), 30)
        if not line:
            return
        method, target, _ = line.decode("latin1").split(" ", 2)
        headers = {}
        while True:
            h = await asyncio.wait_for(reader.readline(), 30)
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode("latin1").partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length") or 0)
        u = urlparse(target)
        if headers.get("host") not in ALLOWED_HOSTS:
            status, payload = err(403, "TORUS only answers on 127.0.0.1")
        elif length > MAX_BODY:
            status, payload = err(413, "too large")
        elif method == "POST" and headers.get("x-torus") != "1":
            status, payload = err(403, "missing X-Torus header")
        elif method not in ("GET", "POST"):
            status, payload = err(404, "not found")
        else:
            raw = await asyncio.wait_for(reader.readexactly(length), 60) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = None
            if not isinstance(body, dict):
                status, payload = err(400, "body must be a JSON object")
            else:
                status, payload = await route(method, u.path, parse_qs(u.query), body, headers)
    except Exception:
        log(NAME, "request error\n" + traceback.format_exc())
        status, payload = err(500, "internal error (see torus logs gatewayd)")
    finally:
        extra = ""
        if isinstance(payload, Raw):
            data, ctype = payload.data, payload.ctype
            if payload.filename:
                extra = f'Content-Disposition: attachment; filename="{payload.filename}"\r\n'
        else:
            data, ctype = json.dumps(payload, ensure_ascii=False).encode(), "application/json"
        head = (f"HTTP/1.1 {status} {REASONS.get(status, 'OK')}\r\nContent-Type: {ctype}\r\n{extra}"
                f"Content-Length: {len(data)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                f"X-Frame-Options: DENY\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n")
        try:
            writer.write(head.encode() + data)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        writer.close()


async def main():
    ANSWERS.mkdir(parents=True, exist_ok=True)
    load_token()
    load_jobs()
    asyncio.create_task(nats_loop())
    gateway_code.init(nreq)
    for j in jobs.values():
        if j.get("status") in ACTIVE:
            for s in j.get("sources", []):
                if s["status"] == "fetching":
                    s["status"] = "pending"
            note(j, "resumed after restart")
            start(j)
    server = await asyncio.start_server(handle, HOST, PORT, limit=MAX_BODY)
    log(NAME, f"up on http://{HOST}:{PORT}  ({len(jobs)} jobs loaded)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
