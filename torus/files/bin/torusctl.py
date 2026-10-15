#!/usr/bin/env python3
"""torusctl - the Python half of the `torus` command (ask, selftest, conf, models, search).
You normally run it through `torus`, e.g.  torus ask "what is a torus" https://en.wikipedia.org/wiki/Torus
"""
import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import ETC, LLM_TOGGLE, SEARCH_CONF, VAULT, connect_nats, pack, read_conf, write_conf  # noqa: E402

GW = f"http://127.0.0.1:{os.environ.get('TORUS_PORT', '7777')}"
CONF = {"llm": LLM_TOGGLE, "search": SEARCH_CONF, "coder": ETC / "coder.conf"}


def token():
    try:
        return (ETC / "gateway.token").read_text().strip()
    except FileNotFoundError:
        return ""


def http(method, path, body=None):
    req = urllib.request.Request(GW + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Torus": "1", "Content-Type": "application/json", "X-Torus-Token": token()})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"gateway said: {json.loads(e.read() or b'{}').get('error', e.code)}")
    except urllib.error.URLError:
        sys.exit("gatewayd is not answering on 127.0.0.1:7777. Try: torus status   then   torus restart gatewayd")


async def nreq(subject, obj, timeout):
    nc = await asyncio.wait_for(connect_nats("torusctl"), 10)
    try:
        msg = await nc.request(subject, pack(obj), timeout=timeout)
        return json.loads(msg.data)
    finally:
        await nc.close()


def cmd_ask(a):
    j = http("POST", "/api/ask", {"question": a.question, "urls": a.urls, "mode": a.mode, "model": a.model, "fresh": a.fresh})
    watch(j["id"])


def cmd_follow(a):
    j = http("POST", f"/api/jobs/{a.id}/followup", {"question": a.question, "urls": a.urls, "model": a.model})
    watch(j["id"], len(j.get("log", [])))


def cmd_report(a):
    print("PDF saved:", http("POST", f"/api/jobs/{a.id}/report", {})["path"])


def cmd_code(a):
    if a.action == "projects":
        res = http("GET", "/api/code/projects")
        print("root:", res["root"])
        for p in res["projects"]:
            print(f"  {p['name']:24} {p['files']} files")
    elif a.action == "new":
        print("created", http("POST", "/api/code/projects", {"name": a.args[0]})["path"])
    elif a.action == "run":
        if len(a.args) < 2:
            sys.exit('usage: torus code run <project> "<command>"')
        r = http("POST", "/api/code/run", {"project": a.args[0], "cmd": " ".join(a.args[1:])})
        print(r["output"])
        print(f"--- exit {r['exit']}{' (timed out)' if r['timed_out'] else ''} in {r['duration']}s")
    elif a.action == "tasks":
        for t in http("GET", "/api/code/tasks")["tasks"][:20]:
            print(f"{t['id']}  {t['status']:11} {t['project']:16} {t['instruction'][:50]}")


def watch(jid, seen_log=0):
    print(f"job {jid}   (web view: {GW}/#/job/{jid})")
    told = False
    try:
        while True:
            j = http("GET", f"/api/jobs/{jid}")
            for line in j.get("log", [])[seen_log:]:
                print("  " + line)
            seen_log = len(j.get("log", []))
            if j["status"] == "needs_you" and not told:
                told = True
                print("\n  NEEDS YOU: some pages want a human check. Open this in Brave:")
                print(f"  {GW}/#/job/{jid}\n  (or run: torus open {jid})  The job waits for you here.\n")
            if j["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print(f"\nStopped watching. The job keeps running. See it again with: torus job {jid}")
        return
    show_job(j)


def show_job(j):
    for w in j.get("warnings", []):
        print("  ! " + w)
    if j.get("error"):
        print("  ERROR: " + j["error"])
    print(f"\n===== {j['question']}   [{j.get('engine') or j['status']}]\n")
    print(j.get("answer") or "(no answer yet)")
    for t in j.get("followups", []):
        print(f"\n===== follow-up: {t['question']}   [{t.get('engine') or '...'}]\n")
        for w in t.get("warnings", []):
            print("  ! " + w)
        print(t.get("answer") or "(no answer yet)")
    print("\nSources:")
    for s in j.get("sources", []):
        print(f"  [{s['n']}] {s['status']:9} {s.get('title') or ''} {s['url']}")
    if j.get("answer_path"):
        print(f"\nSaved: {j['answer_path']}")


def cmd_job(a):
    if a.id:
        show_job(http("GET", f"/api/jobs/{a.id}"))
        return
    for j in http("GET", "/api/jobs")["jobs"][:20]:
        flag = f"  NEEDS YOU ({j['needs_you']})" if j.get("needs_you") else ""
        print(f"{j['id']}  {j['status']:9} {j['question'][:60]}{flag}")


def cmd_conf(a):
    path = CONF[a.file]
    if not a.pairs:
        for k, v in read_conf(path).items():
            print(f"{k}={v}")
        return
    upd = dict(p.split("=", 1) for p in a.pairs if "=" in p)
    write_conf(path, upd)
    print("saved", ", ".join(f"{k}={v}" for k, v in upd.items()), "->", path)


def cmd_selftest(a):
    if a.what == "ingest":
        inbox = VAULT / "inbox"
        before = set(inbox.glob("*.md")) if inbox.exists() else set()
        url = "https://example.com/"
        res = asyncio.run(nreq("torus.fetch.request", {"url": url, "fresh": True}, 60))
        print("fetcherd:", "ok" if res.get("ok") else res.get("error"))
        for _ in range(20):
            time.sleep(1)
            new = [p for p in inbox.glob("*.md") if p not in before and not p.name.endswith(".summary.md")]
            if new:
                print("OK  dossier written:", new[0])
                return
        old = [p for p in before if "example-com" in p.name]
        if old:
            print("OK  example.com already in the vault with the same text (vaultd skips duplicates):", old[0])
            return
        sys.exit("FAIL no dossier after 20 s. Check: torus logs parserd / torus logs vaultd")
    res = asyncio.run(nreq("torus.llm.request", {"prompt": "Reply with exactly: TORUS OK", "max_tokens": 20}, 180))
    if res.get("ok"):
        print("OK ", res.get("model"), "->", res.get("text"))
    else:
        sys.exit("FAIL " + str(res.get("error")))


def cmd_models(a):
    res = asyncio.run(nreq("torus.llm.models", {}, 60))
    if not res.get("ok"):
        sys.exit("FAIL " + str(res.get("error")))
    for m in res["models"]:
        print(m)


def cmd_search(a):
    res = asyncio.run(nreq("torus.index.search", {"q": " ".join(a.words), "limit": 10}, 15))
    for r in res.get("results", []):
        print(f"- {r['title']}\n  {r['url']}\n  {r['snippet']}\n")
    if not res.get("results"):
        print("no matches in your index")


def cmd_ping(a):
    try:
        asyncio.run(asyncio.wait_for(_ping(), 6))
        print("ok")
    except Exception:
        sys.exit(1)


async def _ping():
    import nats
    nc = await nats.connect(servers=["nats://127.0.0.1:4222"], connect_timeout=3, max_reconnect_attempts=0, allow_reconnect=False)
    await nc.close()


def main():
    p = argparse.ArgumentParser(prog="torus")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ask")
    s.add_argument("question")
    s.add_argument("urls", nargs="*")
    s.add_argument("--mode", choices=["urls", "brave", "both"])
    s.add_argument("--model", choices=["sonnet", "opus"])
    s.add_argument("--fresh", action="store_true", help="ignore the 24 h page cache")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("follow")
    s.add_argument("id")
    s.add_argument("question")
    s.add_argument("urls", nargs="*")
    s.add_argument("--model", choices=["sonnet", "opus"])
    s.set_defaults(fn=cmd_follow)
    s = sub.add_parser("report")
    s.add_argument("id")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("code")
    s.add_argument("action", choices=["projects", "new", "run", "tasks"])
    s.add_argument("args", nargs="*")
    s.set_defaults(fn=cmd_code)
    s = sub.add_parser("job")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_job)
    s = sub.add_parser("conf")
    s.add_argument("file", choices=list(CONF))
    s.add_argument("pairs", nargs="*")
    s.set_defaults(fn=cmd_conf)
    s = sub.add_parser("selftest")
    s.add_argument("what", choices=["ingest", "llm"])
    s.set_defaults(fn=cmd_selftest)
    sub.add_parser("models").set_defaults(fn=cmd_models)
    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.set_defaults(fn=cmd_search)
    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
