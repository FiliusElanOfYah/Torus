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
import gateway_upload  # noqa: E402
from torus_cite import citation_map, extract_links  # noqa: E402
import torus_models  # noqa: E402
import torus_spend  # noqa: E402
from torus_common import (ETC, HOME, LLM_TOGGLE, MODEL_ALIASES, SEARCH_CONF, VAR, VAULT, atomic_write,  # noqa: E402
                          connect_nats, extract, get_secret, key_present, llm_settings, log, pack, rank_sentences,
                          read_conf, resolve_model, search_settings, slugify, split_sentences, truthy, write_conf)
from torus_report import build_pdf  # noqa: E402

NAME = "gatewayd"
AUTO_ROUTE_CHARS = 240  # follow-ups up to this long (and without new URLs) go to Haiku when auto_route=true
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
            "summarizerd", "airgapd", "sentineld", "llmd", "gatewayd", "coderd", "oracled", "operatord"]
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
    if st["enabled"] and st["auto_route"] and not t.get("model") and not j.get("model") and t is not j and model != "haiku":
        if len(t["question"]) <= AUTO_ROUTE_CHARS and not t.get("new_sources"):
            model = "haiku"
            note(j, f"auto-route: short follow-up, using Haiku (turn off in Settings or: torus llm auto off)")
    if st["enabled"] and key_present("anthropic"):
        try:
            res = await nreq("torus.llm.request", {"messages": build_messages(j, srcs), "system": SYSTEM,
                                                   "model": model, "job_id": j["id"]}, 900)
        except Exception as e:
            res = {"ok": False, "error": f"sentineld/llmd did not answer ({e.__class__.__name__})"}
        if res.get("ok") and res.get("text"):
            t["answer"], t["engine"] = res["text"], f"claude:{res.get('model')}"
            t["cost"] = res.get("cost")
            t["usage"] = {k: int(res.get("usage", {}).get(k) or 0) for k in ("input_tokens", "output_tokens")}
            t["cached"] = bool(res.get("cached"))
        else:
            if res.get("cap_hit"):
                note(j, "daily Claude cap reached; local summary instead")
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
    if model and model not in MODEL_ALIASES:
        return err(400, "model must be sonnet, opus or haiku")
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
    if model and model not in MODEL_ALIASES:
        return err(400, "model must be sonnet, opus or haiku")
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
    if model and model not in MODEL_ALIASES:
        return err(400, "model must be sonnet, opus or haiku")
    urls = body.get("urls") or []
    if isinstance(urls, str):
        urls = urls.split()
    added = [add_source(j, u.strip(), "follow-up", limit=MAX_TOTAL_SOURCES) for u in urls
             if u.strip().startswith(("http://", "https://"))]
    j.setdefault("followups", []).append({"question": q, "answer": "", "engine": "", "warnings": [],
                                          "model": model, "new_sources": sum(1 for a in added if a), "created": time.time()})
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
    last = torus_models.last() or {}
    return {"llm_enabled": llm["enabled"], "model": llm["model"], "model_id": llm["model_id"], "auto_route": llm["auto_route"],
            "sonnet_id": resolve_model("sonnet", c), "opus_id": resolve_model("opus", c), "haiku_id": resolve_model("haiku", c),
            "tiers": {t: {"id": resolve_model(t, c), "role": torus_models.TIER_ROLE[t],
                          "status": (last.get("tiers", {}).get(t) or {}).get("status", "unchecked")} for t in torus_models.TIERS},
            "models_checked": last.get("when", 0), "models_problems": last.get("problems", 0),
            "search_mode": srch["mode"], "search_count": srch["count"],
            "keys": {"anthropic": key_present("anthropic"), "brave": key_present("brave"), "cloudflare": key_present("cloudflare"),
                     "github": key_present("github")},
            "cf_account": llm["cf_account"], "cf_gateway": llm["cf_gateway"], "cf_enabled": bool(llm["cf_account"] and llm["cf_gateway"]),
            "cf_saved_account": c.get("cf_saved_account", ""), "cf_saved_gateway": c.get("cf_saved_gateway", ""),
            "daily_cap_usd": torus_spend.daily_cap(),
            "op_ask_every_change": truthy(read_conf(ETC / "operator.conf").get("ask_every_change", "false")),
            "github_scope": "all" if (read_conf(ETC / "operator.conf").get("github_scope") or "task").strip().lower() == "all" else "task"}


CF_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def api_settings(body):
    # validate everything first so a bad field never leaves a half-applied request
    if "model" in body and body["model"] not in MODEL_ALIASES:
        return err(400, "model must be sonnet, opus or haiku")
    if "auto_route" in body and not isinstance(body["auto_route"], bool):
        return err(400, "auto_route must be true or false")
    if "daily_cap_usd" in body:
        try:
            body["daily_cap_usd"] = round(max(0.0, min(1000.0, float(body["daily_cap_usd"] or 0))), 2)
        except (TypeError, ValueError):
            return err(400, "daily_cap_usd must be a number (0 = no cap)")
    cf = None
    if "cf_enabled" in body:
        if not isinstance(body["cf_enabled"], bool):
            return err(400, "cf_enabled must be true or false")
        if body["cf_enabled"]:
            cur = read_conf(LLM_TOGGLE)
            acct = str(body.get("cf_account") or cur.get("cf_saved_account") or "").strip()
            gw = str(body.get("cf_gateway") or cur.get("cf_saved_gateway") or "").strip()
            if not (CF_ID.match(acct) and CF_ID.match(gw)):
                return err(400, "Enter your Cloudflare Account ID and gateway name (letters, digits, - and _)")
            cf = {"cf_account": acct, "cf_gateway": gw, "cf_saved_account": acct, "cf_saved_gateway": gw}
        else:
            cur = llm_settings()
            cf = {"cf_account": "", "cf_gateway": ""}
            if cur["cf_account"] and cur["cf_gateway"]:
                cf.update(cf_saved_account=cur["cf_account"], cf_saved_gateway=cur["cf_gateway"])
    if "search_mode" in body and body["search_mode"] not in ("urls", "brave", "both"):
        return err(400, "search_mode must be urls, brave or both")
    if "search_count" in body:
        try:
            body["search_count"] = max(1, min(10, int(body["search_count"])))
        except (TypeError, ValueError):
            return err(400, "search_count must be a number 1-10")
    tier_ids = {t: str(body[f"{t}_id"]).strip() for t in torus_models.TIERS if f"{t}_id" in body}
    for t, mid in tier_ids.items():
        why = torus_models.validate_tier_id(t, mid)
        if why:
            return err(400, why)
    if "llm_enabled" in body:
        write_conf(LLM_TOGGLE, {"enabled": "true" if body["llm_enabled"] else "false"})
    if "auto_route" in body:
        write_conf(LLM_TOGGLE, {"auto_route": "true" if body["auto_route"] is True else "false"})
    if "daily_cap_usd" in body:
        write_conf(LLM_TOGGLE, {"daily_cap_usd": body["daily_cap_usd"] or ""})
    if "op_ask_every_change" in body:
        if not isinstance(body["op_ask_every_change"], bool):
            return err(400, "op_ask_every_change must be true or false")
        write_conf(ETC / "operator.conf", {"ask_every_change": "true" if body["op_ask_every_change"] else "false"})
    if "github_scope" in body:
        if body["github_scope"] not in ("task", "all"):
            return err(400, "github_scope must be task or all")
        write_conf(ETC / "operator.conf", {"github_scope": body["github_scope"]})
    if cf is not None:
        write_conf(LLM_TOGGLE, cf)
    if "model" in body:
        write_conf(LLM_TOGGLE, {"model": body["model"]})
    if tier_ids:
        write_conf(LLM_TOGGLE, {f"{t}_id": mid for t, mid in tier_ids.items()})
        last = torus_models.last()
        if last:  # the saved report is about the old ids; re-check against the same model list
            rep = torus_models.check(last["models"])
            rep["changes"] = []
            torus_models.save(rep)
    if "search_mode" in body:
        write_conf(SEARCH_CONF, {"mode": body["search_mode"]})
    if "search_count" in body:
        write_conf(SEARCH_CONF, {"count": body["search_count"]})
    return 200, settings_payload()


async def api_audit(method):
    try:
        import torus_audit
    except ImportError:
        return err(503, "phase 8 is not installed: torus install 8")
    if method == "POST":
        return 200, await asyncio.to_thread(torus_audit.run_audit)
    return 200, torus_audit.last_audit() or {"ok": None, "counts": {}, "items": [], "when": ""}


async def api_op(method, path, body):
    """Operator (phase 10): GET /api/op, POST /api/op {task,...}, GET /api/op/<id>, POST /api/op/<id>/<action>.
    Console (phase 3b): GET /api/op/console, POST /api/op/console/{send|clear|task|approve|deny|stop|undo}."""
    cm = re.fullmatch(r"/api/op/console(?:/(send|clear|task))?", path)
    if cm:
        try:
            if cm.group(1):
                if method != "POST":
                    return err(405, "POST")
                res = await nreq("torus.op.console", {**(body or {}), "action": cm.group(1)}, 30)
            else:
                res = await nreq("torus.op.console", {"action": "get"}, 10)
        except Exception as e:
            return err(503, f"operatord did not answer ({e.__class__.__name__}): torus install 10  /  torus status")
        if not res.get("ok"):
            return err(400, res.get("error") or "console refused")
        return 200, res
    m = re.fullmatch(r"/api/op(?:/([0-9A-Za-z-]+)(?:/(approve_plan|approve|deny|stop|resume|undo))?)?", path)
    if not m:
        return err(404, "not found")
    tid, action = m.group(1), m.group(2)
    try:
        if not tid:
            if method == "POST":
                res = await nreq("torus.op.create", body or {}, 20)
            else:
                res = await nreq("torus.op.list", {}, 10)
        elif action:
            if method != "POST":
                return err(405, "POST")
            res = await nreq("torus.op.action", {**(body or {}), "id": tid, "action": action}, 30)
        else:
            res = await nreq("torus.op.get", {"id": tid}, 10)
    except Exception as e:
        return err(503, f"operatord did not answer ({e.__class__.__name__}): torus install 10  /  torus status")
    if not res.get("ok"):
        return err(400, res.get("error") or "operator refused")
    return 200, res


def audit_failures():
    try:
        return int(json.loads((VAR / "audit.json").read_text(encoding="utf-8"))["counts"].get("fail", 0))
    except (OSError, ValueError, KeyError, TypeError):
        return 0


async def api_llm(method, path, body):
    """Model ids. GET /api/llm/verify = last report; POST /api/llm/verify {fix} = live check against Anthropic."""
    if path == "/api/llm/verify" and method == "GET":
        return 200, torus_models.last() or {"ok": None, "tiers": {}, "models": [], "changes": [], "problems": 0, "when": 0}
    if path in ("/api/llm/verify", "/api/llm/models"):
        if not key_present("anthropic"):
            return err(409, "no Anthropic key yet: torus key set anthropic")
        try:
            res = await asyncio.wait_for(nreq("torus.llm.models", {}, 60), 65)
        except Exception as e:
            return err(503, f"llmd did not answer ({e.__class__.__name__}); check: torus status")
        if not res.get("ok"):
            return err(502, f"Anthropic did not list models: {res.get('error')}")
        if path == "/api/llm/models":
            return 200, {"ok": True, "models": res["models"]}
        rep = await asyncio.to_thread(torus_models.check, res["models"])
        fix = body.get("fix", True)
        if truthy(fix) if isinstance(fix, str) else bool(fix):
            await asyncio.to_thread(torus_models.fix, rep)
        await asyncio.to_thread(torus_models.save, rep)
        for ch in rep["changes"]:
            log(NAME, f"model id fixed: {ch['tier']} {ch['from']} -> {ch['to']}")
        return 200, rep | {"settings": settings_payload()}
    return err(404, "not found")


# ---------------------------------------------------------------- vault documents (dossiers)
DOC_DIRS = {"inbox": "Pages read (inbox)", "answers": "Answers", "topics": "Topic digests"}
DOC_MAX_BYTES = 400_000


def doc_meta(text):
    """Frontmatter -> dict, plus the first heading. Values stay strings."""
    meta, body = {}, text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            for ln in text[4:end].splitlines():
                k, _, v = ln.partition(":")
                if _ and k.strip():
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] == '"':
                        try:
                            v = json.loads(v)
                        except ValueError:
                            v = v[1:-1]
                    meta[k.strip()] = v
            body = text[end + 4:]
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return meta, (m.group(1).strip() if m else "")


def safe_doc_path(rel):
    rel = (rel or "").strip().lstrip("/")
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] not in DOC_DIRS or not parts[1].endswith(".md") or "/" in parts[1] or parts[1].startswith("."):
        return None
    p = (VAULT / parts[0] / parts[1])
    try:
        p.resolve().relative_to(VAULT.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def list_docs(folder, limit=200):
    d = VAULT / folder
    rows = []
    if not d.is_dir():
        return rows
    files = sorted((p for p in d.iterdir() if p.suffix == ".md" and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    summaries = {p.name for p in files if p.name.endswith(".summary.md")}
    for p in files:
        if p.name.endswith(".summary.md"):
            continue
        st = p.stat()
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                head = f.read(2048)
        except OSError:
            continue
        meta, title = doc_meta(head)
        rows.append({"rel": f"{folder}/{p.name}", "name": p.name, "title": meta.get("title") or title or p.stem,
                     "mtime": st.st_mtime, "bytes": st.st_size, "url": meta.get("url", ""), "job": meta.get("job", ""),
                     "engine": meta.get("engine", ""), "has_summary": p.name[:-3] + ".summary.md" in summaries})
        if len(rows) >= limit:
            break
    return rows


def doc_body(text):
    """Same split the web page's md() uses, so paragraph indexes line up."""
    body = text.split("\n---", 1)[1] if text.startswith("---\n") and "\n---" in text else text
    return body.lstrip("\n")


def find_in_docs(q, folders, limit=40, max_files=400):
    words = [w for w in re.findall(r"\w+", q.lower()) if len(w) >= 2]
    if not words:
        return []
    hits = []
    for folder in folders:
        d = VAULT / folder
        if not d.is_dir():
            continue
        files = sorted((p for p in d.glob("*.md") if not p.name.startswith(".") and not p.name.endswith(".summary.md")),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:DOC_MAX_BYTES]
            except OSError:
                continue
            meta, title = doc_meta(text)
            title = meta.get("title") or title or p.stem
            for idx, para in enumerate(re.split(r"\n{2,}", doc_body(text))):
                low = para.lower()
                found = [w for w in words if w in low]
                if not found:
                    continue
                score = len(found) * 10 + sum(low.count(w) for w in found)
                snippet = " ".join(re.sub(r"(?m)^\s*(#+|[-*>]+|\d+\.)\s*", "", para).split())
                low = snippet.lower()
                pos = min(low.find(w) for w in found)
                start = max(0, pos - 80)
                snippet = ("…" if start else "") + snippet[start:start + 320] + ("…" if len(snippet) > start + 320 else "")
                hits.append({"rel": f"{folder}/{p.name}", "dir": folder, "title": title, "idx": idx, "para": snippet,
                             "score": score, "all": len(found) == len(words), "mtime": p.stat().st_mtime})
    hits.sort(key=lambda h: (h["all"], h["score"], h["mtime"]), reverse=True)
    return hits[:limit]


async def api_vault_docs(path, query):
    if path == "/api/vault/find":
        q = (query.get("q") or [""])[0].strip()
        folder = (query.get("dir") or ["all"])[0]
        if folder != "all" and folder not in DOC_DIRS:
            return err(400, "dir must be all, inbox, answers or topics")
        if len(q) < 2:
            return err(400, "type at least two characters")
        hits = await asyncio.to_thread(find_in_docs, q, list(DOC_DIRS) if folder == "all" else [folder])
        return 200, {"q": q, "hits": hits}
    if path == "/api/vault/docs":
        folder = (query.get("dir") or ["answers"])[0]
        if folder not in DOC_DIRS:
            return err(400, "dir must be inbox, answers or topics")
        rows = await asyncio.to_thread(list_docs, folder)
        counts = {k: sum(1 for p in (VAULT / k).glob("*.md") if not p.name.endswith(".summary.md") and not p.name.startswith("."))
                  if (VAULT / k).is_dir() else 0
                  for k in DOC_DIRS}
        return 200, {"dir": folder, "label": DOC_DIRS[folder], "docs": rows, "counts": counts}
    if path == "/api/vault/doc":
        p = safe_doc_path((query.get("path") or [""])[0])
        if not p:
            return err(404, "no such document")
        raw = await asyncio.to_thread(p.read_bytes)
        text = raw[:DOC_MAX_BYTES].decode("utf-8", errors="replace")
        meta, title = doc_meta(text)
        out = {"rel": f"{p.parent.name}/{p.name}", "name": p.name, "title": meta.get("title") or title or p.stem, "meta": meta,
               "text": doc_body(text), "bytes": len(raw), "truncated": len(raw) > DOC_MAX_BYTES, "mtime": p.stat().st_mtime}
        summary = p.with_name(p.name[:-3] + ".summary.md")
        if p.parent.name == "inbox" and summary.is_file():
            out["summary"] = summary.read_text(encoding="utf-8", errors="replace")
        return 200, out
    return err(404, "not found")


async def api_export(method, body, headers):
    try:
        import torus_export
    except ImportError:
        return err(503, "phase 9 is not installed: torus install 9")
    if method == "GET":
        return 200, {"exports": torus_export.list_exports()}
    if not secrets.compare_digest((headers or {}).get("x-torus-token", ""), STATE["token"]):
        return err(401, "Export needs your TORUS token. Open the page from Termux with: torus open")
    try:
        info = await asyncio.to_thread(torus_export.export, bool(body.get("with_keys")), not body.get("no_vault"))
    except OSError as e:
        return err(500, f"export failed: {e}")
    return 200, info


async def api_oracle(method, path, body):
    """Pinned topics. Status and the editor read files directly (work even when oracled is down);
    'run' goes over NATS to the daemon."""
    try:
        import oracled
    except ImportError:
        return err(503, "phase 7 is not installed: torus install 7")
    if path == "/api/oracle/run":
        topic = (body.get("topic") or "").strip()
        try:
            return 200, await asyncio.wait_for(nreq("torus.oracle.run", {"topic": topic}, 8), 10)
        except Exception as e:
            return err(503, f"oracled did not answer ({e.__class__.__name__}); check: torus status")
    if path == "/api/oracle/pin":
        url = (body.get("url") or "").strip()
        name = re.sub(r"\s+", " ", (body.get("topic") or "").strip())[:80]
        if not url.startswith(("http://", "https://")):
            return err(400, "url must start with http:// or https://")
        if not name:
            return err(400, "give the topic a name")
        topics, perr = oracled.load_pinned()
        if perr and not oracled.PINNED.exists():
            topics = []
        elif perr:
            return err(409, perr + " - fix the file first (Vault tab, Edit topics)")
        topic = next((t for t in topics if t["name"].lower() == name.lower()), None)
        created = topic is None
        if created:
            if len(topics) >= oracled.MAX_TOPICS:
                return err(400, f"you already have {oracled.MAX_TOPICS} topics; unpin one first")
            topic = {"name": name, "seeds": []}
            topics.append(topic)
        already = url in topic["seeds"]
        if not already:
            if len(topic["seeds"]) >= oracled.MAX_SEEDS:
                return err(400, f"'{topic['name']}' already has {oracled.MAX_SEEDS} seeds")
            topic["seeds"].append(url)
            await asyncio.to_thread(oracled.atomic_write, oracled.PINNED, oracled.dump_pinned(topics))
        refilling = False
        if not already:
            try:
                refilling = bool((await asyncio.wait_for(nreq("torus.oracle.run", {"topic": topic["name"]}, 8), 10)).get("ok"))
            except Exception:
                refilling = False
        return 200, {"ok": True, "topic": topic["name"], "seeds": topic["seeds"], "created": created, "already": already,
                     "refilling": refilling}
    if path == "/api/oracle/unpin":
        url = (body.get("url") or "").strip()
        name = (body.get("topic") or "").strip()
        topics, perr = oracled.load_pinned()
        if perr:
            return err(409, perr + " - fix the file first (Vault tab, Edit topics)")
        topic = next((t for t in topics if t["name"].lower() == name.lower()), None)
        if not topic:
            return err(404, f"no pinned topic called '{name}'")
        removed_topic = False
        if url:
            if url not in topic["seeds"]:
                return err(404, "that page is not a seed of this topic")
            topic["seeds"].remove(url)
        else:  # no url = remove the whole topic
            topics.remove(topic)
            removed_topic = True
        await asyncio.to_thread(oracled.atomic_write, oracled.PINNED, oracled.dump_pinned(topics))
        return 200, {"ok": True, "topic": topic["name"], "seeds": topic["seeds"], "removed_topic": removed_topic}
    if path == "/api/oracle/config":
        if method == "POST":
            try:
                topics = await asyncio.to_thread(oracled.save_pinned, body.get("yaml") or "")
            except Exception as e:
                return err(400, f"pinned.yaml not saved: {e}")
            return 200, {"ok": True, "topics": topics, "yaml": oracled.PINNED.read_text(encoding="utf-8")}
        topics, perr = oracled.load_pinned()
        text = oracled.PINNED.read_text(encoding="utf-8") if oracled.PINNED.exists() else oracled.dump_pinned([])
        return 200, {"topics": topics, "yaml": text, "error": perr}
    if method == "GET" and path == "/api/oracle":
        try:
            return 200, await asyncio.wait_for(nreq("torus.oracle.status", {}, 5), 6)
        except Exception:
            oracled.load_state()
            return 200, oracled.status_payload() | {"daemon": "down", "error": "oracled is not answering: torus status"}
    return err(404, "not found")


async def route(method, path, query, body, headers=None):
    if method == "GET" and path in ("/", "/index.html"):
        page = WEB.read_bytes() if WEB.exists() else b"<h1>TORUS</h1><p>web/index.html missing: torus install 6</p>"
        return 200, Raw(page, "text/html; charset=utf-8")
    if path.startswith("/api/code"):
        if not secrets.compare_digest((headers or {}).get("x-torus-token", ""), STATE["token"]):
            return err(401, "Code needs your TORUS token. Open the page from Termux with: torus open")
        return await gateway_code.route(method, path, query, body)
    if path.startswith("/api/op"):
        if not secrets.compare_digest((headers or {}).get("x-torus-token", ""), STATE["token"]):
            return err(401, "Operator needs your TORUS token. Open the page from Termux with: torus open")
        return await api_op(method, path, body)
    if path.startswith("/api/upload"):
        if not secrets.compare_digest((headers or {}).get("x-torus-token", ""), STATE["token"]):
            return err(401, "Uploads need your TORUS token. Open the page from Termux with: torus open")
        return await gateway_upload.route(method, path, query, body)
    if path == "/api/health":
        nc = STATE["nc"]
        return 200, {"ok": True, "nats": bool(nc and nc.is_connected), "jobs": len(jobs), "running": len(tasks),
                     "audit_fail": audit_failures()}
    if path == "/api/audit":
        return await api_audit(method)
    if method == "GET" and path == "/api/spend":
        return 200, torus_spend.summary()
    if path.startswith("/api/llm/"):
        return await api_llm(method, path, body)
    if method == "GET" and path.startswith("/api/vault/"):
        return await api_vault_docs(path, query)
    if path == "/api/export":
        return await api_export(method, body, headers)
    if path.startswith("/api/oracle"):
        return await api_oracle(method, path, body)
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
            if u.path.startswith("/api/upload/") and headers.get("content-type", "").startswith("application/octet-stream"):
                status, payload = await route(method, u.path, parse_qs(u.query), {"_raw": raw}, headers)
            else:
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
