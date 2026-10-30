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
CONF = {"llm": LLM_TOGGLE, "search": SEARCH_CONF, "coder": ETC / "coder.conf", "oracle": ETC / "oracle.conf", "operator": ETC / "operator.conf"}


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
        m = m if isinstance(m, dict) else {"id": m}
        print(f"{m['id']:40} {m.get('display_name', '')}")


def cmd_spend(a):
    import runpy
    runpy.run_module("torus_spend", run_name="__main__")


def _op_print_plan(t):
    print(f"\n== plan for: {t['task']}   (model {t['model']}, estimate ≈ ${t.get('estimate_usd', 0):.2f}, budget ${t['budget_usd']:.2f})")
    for i, ph in enumerate(t["phases"]):
        print(f"  {i + 1}. {ph['title']}")
        for k, label in (("reads", "reads"), ("changes", "changes"), ("runs", "runs"), ("phone", "phone")):
            if ph.get(k):
                print(f"       {label}: {'; '.join(ph[k])}")
    if t.get("plan_notes"):
        print(f"  notes: {t['plan_notes']}")


def _op_follow(tid):
    """Print log entries as they appear; answer taps on the terminal."""
    seen = 0
    while True:
        t = http("GET", f"/api/op/{tid}")["task"]
        for e in t["log"][seen:]:
            mark = {"cmd": "$", "out": " ", "say": "·", "ask": "?", "phase": "==", "summary": "##", "tap": "✓", "blocked": "x",
                    "stop": "!!", "error": "!!", "done": "OK", "note": "..", "plan": "==", "undo": "<<"}.get(e["kind"], "-")
            txt = e["text"] if e["kind"] != "out" else "\n".join("   " + l for l in e["text"].splitlines()[:12])
            print(f"{mark} {txt}")
        seen = len(t["log"])
        if t["status"] == "needs_you" and t.get("pending"):
            ans = input(f"\n?? {t['pending']['sentence']}\n   allow? [y/N/stop] ").strip().lower()
            http("POST", f"/api/op/{tid}/{'approve' if ans == 'y' else 'stop' if ans == 'stop' else 'deny'}", {})
        elif t["status"] == "paused":
            ans = input("\n.. paused between phases. continue? [Y/stop] ").strip().lower()
            http("POST", f"/api/op/{tid}/{'stop' if ans == 'stop' else 'resume'}", {})
        elif t["status"] in ("done", "stopped", "error", "interrupted"):
            print(f"\n{t['status']} · {t.get('calls', 0)} calls · ≈ ${t['cost']:.4f}" + (f" · {t['stop_reason']}" if t.get("stop_reason") else ""))
            if t.get("dossier"):
                print(f"dossier: {t['dossier']}   undo a phase: torus op undo {tid} <n>")
            return
        time.sleep(2)


def cmd_op(a):
    args = list(a.args)
    sub = args[0] if args else "list"
    if sub == "create":
        rest, repos = [], []
        it = iter(args[1:])
        for w in it:
            if w == "--repos":
                repos += [x for x in next(it, "").split(",") if x]
            elif w.startswith("--repos="):
                repos += [x for x in w.split("=", 1)[1].split(",") if x]
            else:
                rest.append(w)
        task = " ".join(rest).strip()
        t = http("POST", "/api/op", {"task": task, "repos": repos})["task"]
        print(f"planning {t['id']} ...")
        while t["status"] == "planning":
            time.sleep(2)
            t = http("GET", f"/api/op/{t['id']}")["task"]
        if t["status"] != "plan_ready":
            sys.exit(f"{t['status']}: {t.get('stop_reason') or ''}")
        _op_print_plan(t)
        ans = input("\napprove? [y/N]  (or a number to set the budget in USD, e.g. 2) ").strip().lower()
        if ans in ("y", "yes") or ans.replace(".", "", 1).isdigit():
            body = {"budget_usd": float(ans)} if ans.replace(".", "", 1).isdigit() else {}
            http("POST", f"/api/op/{t['id']}/approve_plan", body)
            _op_follow(t["id"])
        else:
            http("POST", f"/api/op/{t['id']}/stop", {})
            print("plan declined")
    elif sub == "list":
        for t in http("GET", "/api/op")["tasks"]:
            print(f"{t['id']}  {t['status']:10} ≈ ${t['cost'] or 0:.4f}  {t['task'][:70]}")
    elif sub in ("show", "follow") and len(args) > 1:
        t = http("GET", f"/api/op/{args[1]}")["task"]
        if sub == "show":
            _op_print_plan(t) if t["phases"] else print(t["status"])
            for e in t["log"][-30:]:
                print(f"{e['kind']:8} {e['text'][:200]}")
        else:
            _op_follow(args[1])
    elif sub in ("approve", "yes", "no", "stop", "resume") and len(args) > 1:
        act = {"approve": "approve_plan", "yes": "approve", "no": "deny"}.get(sub, sub)
        t = http("POST", f"/api/op/{args[1]}/{act}", {})["task"]
        print(t["status"])
        if act == "approve_plan":
            _op_follow(args[1])
    elif sub == "undo" and len(args) > 2:
        r = http("POST", f"/api/op/{args[1]}/undo", {"phase": int(args[2]) - 1})
        print(f"restored {len(r['restored'])} file(s)" + (f"; could not restore: {', '.join(r['missing'])}" if r["missing"] else ""))
    else:
        sys.exit('usage: torus op "task" | list | show <id> | follow <id> | approve <id> | yes <id> | no <id> | stop <id> | resume <id> | undo <id> <phase>')


def cmd_gh(a):
    """torus gh <op> ... : GitHub through airgapd's token (same code the Operator uses, scope = all for you)."""
    import torus_github as gh
    args = list(a.args)
    op = args[0] if args else ""
    usage = ("usage: torus gh repos | tree <owner/repo> [path] [ref] | read <owner/repo> <path> [ref] | search <owner/repo|-> \"<words>\" | "
             "commits|issues|prs <owner/repo> [n] | pr_diff <owner/repo> <n> | clone <owner/repo> [dest] [ref]")
    if op not in gh.OPS:
        sys.exit(usage)
    act = {"op": op}
    try:
        if op in ("tree", "read"):
            act.update(repo=args[1], path=args[2] if len(args) > 2 else "", ref=args[3] if len(args) > 3 else "")
        elif op == "search":
            act.update(repo="" if args[1] == "-" else args[1], q=" ".join(args[2:]))
        elif op in ("commits", "issues", "prs"):
            act.update(repo=args[1], n=args[2] if len(args) > 2 else 10)
        elif op == "pr_diff":
            act.update(repo=args[1], n=args[2])
        elif op == "clone":
            dest = args[2] if len(args) > 2 else os.path.join(os.path.expanduser("~/projects"), args[1].split("/")[-1])
            act.update(repo=args[1], _dest=os.path.abspath(os.path.expanduser(dest)), ref=args[3] if len(args) > 3 else "")
    except IndexError:
        sys.exit(usage)

    async def go():
        from torus_common import get_secret
        token = await get_secret("github")
        return await gh.run(op, act, token, [], "all")
    try:
        print(asyncio.run(go()))
    except (gh.GitHubError, RuntimeError) as e:
        sys.exit(str(e))


def cmd_verify(a):
    r = http("POST", "/api/llm/verify", {"fix": not a.check_only})
    for t, info in r["tiers"].items():
        mark = {"ok": "OK  ", "outdated": "OLD ", "missing": "GONE", "unknown": "?   "}[info["status"]]
        print(f"{mark} {t:7} {info['current']:40} {info['role']}" + (f"   newest: {info['newest']}" if info["status"] not in ("ok", "unknown") else ""))
    for ch in r["changes"]:
        print(f"fixed {ch['tier']}: {ch['from']} -> {ch['to']}")
    if r["problems"] and a.check_only:
        print(f"{r['problems']} tier(s) need a fix: torus llm verify")
    elif not r["changes"]:
        print("every tier points at the newest model your key can see")


def cmd_search(a):
    res = asyncio.run(nreq("torus.index.search", {"q": " ".join(a.words), "limit": 10}, 15))
    for r in res.get("results", []):
        print(f"- {r['title']}\n  {r['url']}\n  {r['snippet']}\n")
    if not res.get("results"):
        print("no matches in your index")


def cmd_oracle(a):
    if a.action == "check":
        import oracled
        topics, err = oracled.load_pinned()
        if err:
            sys.exit(f"FAIL {err}")
        print(f"OK  {len(topics)} topic(s), {sum(len(t['seeds']) for t in topics)} seed(s). Refill now: torus oracle run")
        return
    if a.action == "run":
        r = http("POST", "/api/oracle/run", {"topic": a.topic or ""})
        if not r.get("ok"):
            sys.exit("FAIL " + str(r.get("error")))
        print(f"refill started for {r.get('topics')} topic(s). Watch: torus oracle   or   torus logs oracled -f")
        return
    o = http("GET", "/api/oracle")
    fmt = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"  # noqa: E731
    if o.get("daemon") == "down":
        print("! oracled is not running: torus status / torus install 7")
    print(f"schedule: {'every ' + str(o['every_minutes']) + ' min' if o['enabled'] else 'paused (torus oracle on)'}"
          f"   last run: {fmt(o['last_run'])}   next: {fmt(o['next_run'])}   runs: {o['runs']}"
          + (f"\nrunning now: {o['current']}" if o.get("running") else ""))
    if o.get("error"):
        print("! " + o["error"])
    for t in o["topics"]:
        print(f"\n{t['name']}   {t['ok']}/{len(t['seeds'])} ok   {fmt(t['when'])}" + (f"   {t['changed']} changed" if t.get("changed") else "")
              + (f"   digest: {t['digest']}" if t.get("digest") else ""))
        res = {r["url"]: r for r in t.get("results", [])}
        for u in t["seeds"]:
            r = res.get(u, {})
            print(f"  {r.get('status', '-'):8} {u}" + ("   (changed)" if r.get("changed") else "") + (f"   {r['error']}" if r.get("error") else ""))
    if not o["topics"]:
        print("no pinned topics yet: torus oracle edit")


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
    sub.add_parser("spend").set_defaults(fn=cmd_spend)
    s = sub.add_parser("op")
    s.add_argument("args", nargs="*")
    s.set_defaults(fn=cmd_op)
    s = sub.add_parser("gh")
    s.add_argument("args", nargs="*")
    s.set_defaults(fn=cmd_gh)
    s = sub.add_parser("verify")
    s.add_argument("--check-only", action="store_true")
    s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.set_defaults(fn=cmd_search)
    sub.add_parser("ping").set_defaults(fn=cmd_ping)
    s = sub.add_parser("oracle")
    s.add_argument("action", choices=["status", "run", "check"])
    s.add_argument("topic", nargs="?")
    s.set_defaults(fn=cmd_oracle)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
