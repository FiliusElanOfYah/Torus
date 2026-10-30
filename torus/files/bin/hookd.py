#!/usr/bin/env python3
"""hookd - GitHub webhooks for the Operator (phase 11).

Listens on 127.0.0.1:7778 only; Cloudflare Tunnel (service `cloudflared`, config etc/tunnel.yml) forwards ONE public
hostname to it. gatewayd (7777) is never exposed.

  POST /hook/github   X-Hub-Signature-256 verified against airgap/webhook.key (torus hooks secret); X-GitHub-Delivery
                      replay protection; body <= 1 MB. Matching rules in etc/hooks.yaml create Operator tasks, which
                      still wait for your plan-approval tap.
  GET  /hook/health   {"ok": true}

NATS: torus.hooks.status -> {events, rules, tunnel}.  Events log: var/hooks/events.jsonl.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import ETC, VAR, append_jsonl, atomic_write, get_secret, key_present, log, pack, run_daemon  # noqa: E402

NAME = "hookd"
HOST, PORT = "127.0.0.1", int(os.environ.get("TORUS_HOOKD_PORT") or 7778)
RULES = ETC / "hooks.yaml"
TUNNEL = ETC / "tunnel.yml"
HOOKS_DIR = VAR / "hooks"
EVENTS = HOOKS_DIR / "events.jsonl"
SEEN_FILE = HOOKS_DIR / "seen.json"
MAX_BODY = 1_000_000
EVENT_NAMES = {"push", "pull_request", "issues", "issue_comment", "workflow_run", "check_run", "release", "ping"}
NC = {}
SEEN = []
RECENT = []


def load_rules():
    try:
        data = yaml.safe_load(RULES.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        log(NAME, "hooks.yaml unreadable:", e)
        return []
    rules = data.get("rules") if isinstance(data, dict) else None
    return [r for r in (rules or []) if isinstance(r, dict) and r.get("event") and r.get("task")]


def fields(event, p):
    """Flatten the bits of a GitHub payload that rules and task texts use."""
    repo = (p.get("repository") or {}).get("full_name") or ""
    item = p.get("issue") or p.get("pull_request") or p.get("workflow_run") or p.get("check_run") or p.get("release") or {}
    comment = p.get("comment") or {}
    branch = ""
    if event == "push":
        branch = str(p.get("ref") or "").replace("refs/heads/", "")
    elif event == "pull_request":
        branch = ((p.get("pull_request") or {}).get("head") or {}).get("ref") or ""
    elif event == "workflow_run":
        branch = (p.get("workflow_run") or {}).get("head_branch") or ""
    elif event == "check_run":
        branch = ((p.get("check_run") or {}).get("check_suite") or {}).get("head_branch") or ""
    return {"event": event, "action": str(p.get("action") or ""), "repo": repo, "number": str(item.get("number") or ""),
            "title": str(item.get("title") or item.get("name") or (p.get("head_commit") or {}).get("message") or "")[:300],
            "body": str(comment.get("body") or item.get("body") or "")[:4000], "branch": branch,
            "html_url": str(comment.get("html_url") or item.get("html_url") or (p.get("repository") or {}).get("html_url") or ""),
            "sender": str((p.get("sender") or {}).get("login") or ""), "workflow": str(item.get("name") or ""),
            "conclusion": str(item.get("conclusion") or ""), "labels": ", ".join(lb.get("name", "") for lb in (item.get("labels") or []) if isinstance(lb, dict))}


def matches(rule, f):
    if rule.get("event") != f["event"]:
        return False
    for key in ("action", "branch", "conclusion", "sender"):
        if rule.get(key) and str(rule[key]) != f[key]:
            return False
    if rule.get("repo") and rule["repo"] != "*" and str(rule["repo"]).lower() != f["repo"].lower():
        return False
    if rule.get("match") and str(rule["match"]).lower() not in (f["body"] + "\n" + f["title"]).lower():
        return False
    if rule.get("label") and str(rule["label"]).lower() not in f["labels"].lower():
        return False
    return True


def render(template, f):
    return re.sub(r"\{(\w+)\}", lambda m: f.get(m.group(1), m.group(0)), str(template))[:2000]


def verify(secret, body, sig):
    if not sig or not sig.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, sig[7:])


def remember(delivery):
    global SEEN
    if delivery in SEEN:
        return False
    SEEN = (SEEN + [delivery])[-2000:]
    try:
        HOOKS_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write(SEEN_FILE, json.dumps(SEEN))
    except OSError:
        pass
    return True


def record(ev):
    ev["t"] = time.time()
    RECENT.append(ev)
    del RECENT[:-50]
    try:
        append_jsonl(EVENTS, ev)
    except OSError:
        pass


async def create_task(text, repos):
    msg = await NC["nc"].request("torus.op.create", pack({"task": text, "repos": repos, "source": "webhook"}), timeout=20)
    res = json.loads(msg.data)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "operatord refused")
    return res["task"]["id"]


async def handle_github(headers, body):
    event = headers.get("x-github-event", "")
    delivery = headers.get("x-github-delivery", "")
    if not key_present("webhook"):
        record({"event": event, "ok": False, "why": "no webhook secret stored (torus hooks secret)"})
        return 503, {"error": "no webhook secret on this phone yet"}
    try:
        secret = await get_secret("webhook")
    except RuntimeError as e:
        return 503, {"error": str(e)}
    if not verify(secret, body, headers.get("x-hub-signature-256", "")):
        record({"event": event, "delivery": delivery, "ok": False, "why": "bad signature"})
        return 401, {"error": "signature mismatch"}
    if not delivery or not remember(delivery):
        record({"event": event, "delivery": delivery, "ok": False, "why": "replayed or missing delivery id"})
        return 409, {"error": "already seen"}
    if event not in EVENT_NAMES:
        record({"event": event, "delivery": delivery, "ok": True, "why": "event ignored"})
        return 202, {"ok": True, "ignored": event}
    try:
        payload = json.loads(body)
    except ValueError:
        return 400, {"error": "body is not JSON"}
    if event == "ping":
        record({"event": "ping", "delivery": delivery, "ok": True, "repo": (payload.get("repository") or {}).get("full_name", "")})
        return 200, {"ok": True, "pong": True}
    f = fields(event, payload)
    created = []
    for rule in load_rules():
        if not matches(rule, f):
            continue
        repos = rule.get("repos") or ([f["repo"]] if f["repo"] else [])
        try:
            tid = await create_task(render(rule["task"], f), repos)
            created.append({"rule": rule.get("name") or rule["event"], "task": tid})
        except Exception as e:
            created.append({"rule": rule.get("name") or rule["event"], "error": str(e)})
        if rule.get("stop", True):
            break
    record({"event": event, "action": f["action"], "repo": f["repo"], "delivery": delivery, "ok": True, "created": created,
            "title": f["title"][:120]})
    return 200, {"ok": True, "created": created}


async def serve(reader, writer):
    status, payload = 400, {"error": "bad request"}
    try:
        line = await asyncio.wait_for(reader.readline(), 15)
        parts = line.decode("latin-1").split()
        method, path = (parts + ["", ""])[:2]
        headers = {}
        while True:
            h = await asyncio.wait_for(reader.readline(), 15)
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode("latin-1").partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length") or 0)
        if length > MAX_BODY:
            status, payload = 413, {"error": "body over 1 MB"}
        elif method == "GET" and path == "/hook/health":
            status, payload = 200, {"ok": True, "rules": len(load_rules()), "secret": key_present("webhook")}
        elif method == "POST" and path == "/hook/github":
            body = await asyncio.wait_for(reader.readexactly(length), 30) if length else b""
            status, payload = await handle_github(headers, body)
        else:
            status, payload = 404, {"error": "not found"}
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
        status, payload = 400, {"error": "bad request"}
    except Exception as e:  # never let one delivery kill the listener
        log(NAME, "error", f"{e.__class__.__name__}: {e}")
        status, payload = 500, {"error": "internal"}
    data = json.dumps(payload).encode()
    writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)
    try:
        await writer.drain()
    finally:
        writer.close()


def tunnel_info():
    info = {"configured": TUNNEL.exists(), "hostname": "", "ok": True, "why": ""}
    if TUNNEL.exists():
        try:
            cfg = yaml.safe_load(TUNNEL.read_text(encoding="utf-8")) or {}
            for ing in cfg.get("ingress") or []:
                if ing.get("hostname"):
                    info["hostname"] = ing["hostname"]
                    if ing.get("service") != f"http://{HOST}:{PORT}":
                        info["ok"], info["why"] = False, f"{ing['hostname']} points at {ing.get('service')} instead of hookd"
        except (OSError, yaml.YAMLError) as e:
            info["ok"], info["why"] = False, f"tunnel.yml unreadable: {e}"
    return info


async def on_status(nc, msg, data):
    await nc.publish(msg.reply, pack({"ok": True, "events": RECENT[-20:], "rules": [{k: r.get(k) for k in ("name", "event", "repo", "match", "branch")} for r in load_rules()],
                                      "tunnel": tunnel_info(), "secret": key_present("webhook"), "port": PORT}))


async def on_start(nc):
    NC["nc"] = nc
    global SEEN
    try:
        SEEN = json.loads(SEEN_FILE.read_text())[-2000:]
    except (OSError, ValueError):
        SEEN = []
    server = await asyncio.start_server(serve, HOST, PORT, limit=MAX_BODY + 65536)
    NC["server"] = server
    log(NAME, f"listening on {HOST}:{PORT}; {len(load_rules())} rule(s); tunnel {'configured' if TUNNEL.exists() else 'not set up (torus tunnel route <host>)'}")


if __name__ == "__main__":
    run_daemon(NAME, {"torus.hooks.status": on_status}, on_start=on_start)
