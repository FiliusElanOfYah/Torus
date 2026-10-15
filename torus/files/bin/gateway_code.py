"""Code tasks for gatewayd (phase 6 side of the coder).

Loop: read the project -> Claude proposes WHOLE files -> you review the diff
(apply / ask for changes / reject) -> coderd writes them (backed up) -> coderd runs
YOUR run command -> if it fails, the output goes back to Claude for a fix, up to
max_attempts. With auto_fix on, fixes after the first approval apply without asking.
"""
import asyncio
import difflib
import json
import re
import secrets
import time
import traceback

from torus_common import VAR, atomic_write, key_present, llm_settings, log

DIR = VAR / "code"
TASKS, EVENTS, RUNNING = {}, {}, {}
NREQ = None
ACTIVE = {"queued", "reading", "thinking", "review"}
INTERRUPTIBLE = {"applying", "running"}
FILE_RE = re.compile(r"<<<FILE\s+([^\n>]+?)\s*>>>[ \t]*\n(.*?)<<<END>>>", re.S)
DEL_RE = re.compile(r"<<<DELETE\s+([^\n>]+?)\s*>>>")
TEXT_EXT = {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".md", ".toml", ".yaml",
            ".yml", ".txt", ".html", ".css", ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java", ".kt", ".lua",
            ".rb", ".php", ".sql", ".ini", ".cfg", ".conf", ".env.example", ".xml", ".gradle", ".r", ".swift"}
PLAIN_NAMES = {"Makefile", "Dockerfile", "Procfile", "requirements.txt", ".gitignore"}
LOCKS = {"package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock", "pnpm-lock.yaml", "uv.lock"}
SYSTEM = (
    "You are the coding brain of TORUS, working in the user's project inside Termux on Android "
    "(aarch64, bash, Python 3, no root, no sudo, no apt: packages come from `pkg install` and `pip install`). "
    "You change code ONLY by writing complete files. Format your reply exactly like this:\n"
    "1. A short plan (3-8 lines) in plain text.\n"
    "2. For every file you create or change:\n<<<FILE relative/path/to/file>>>\n(the ENTIRE file content)\n<<<END>>>\n"
    "3. To delete a file: <<<DELETE relative/path>>>\n"
    "Never output partial files, diffs or placeholders such as '... rest unchanged'. Leave files you do not "
    "need to change out of the reply. You cannot run commands. If the user must install something, say so in the plan.")


def init(nreq):
    global NREQ
    NREQ = nreq
    DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(DIR.glob("*.json"))[-200:]:
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            TASKS[t["id"]] = t
        except (ValueError, KeyError):
            continue
    for t in TASKS.values():
        if t["status"] in INTERRUPTIBLE:
            t["status"] = "interrupted"
            note(t, "TORUS restarted during this step. Check the files, then start a new task if needed")
        elif t["status"] in ACTIVE:
            note(t, "resumed after restart")
            start(t)


def save(t):
    t["updated"] = time.time()
    atomic_write(DIR / f"{t['id']}.json", json.dumps(t, ensure_ascii=False))


def note(t, text):
    t.setdefault("log", []).append(f"{time.strftime('%H:%M:%S')} {text}")
    t["log"] = t["log"][-100:]
    save(t)


def public(t):
    out = {k: v for k, v in t.items() if k != "proposals"}
    out["proposals"] = [{k: v for k, v in p.items() if k != "changes"} |
                        {"changes": [{k: v for k, v in c.items() if k != "content"} for c in p["changes"]]}
                        for p in t["proposals"]]
    out["running"] = t["id"] in RUNNING
    return out


async def creq(subject, obj, timeout=30):
    try:
        res = await asyncio.wait_for(NREQ(subject, obj, timeout), timeout + 5)
    except Exception as e:
        raise RuntimeError(f"coderd did not answer ({e.__class__.__name__}). In Termux: torus install 7") from None
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "coderd error")
    return res


def clean_path(raw):
    p = re.sub(r"^(\./)+", "", raw.strip().strip("`'\"").replace("\\", "/"))
    if not p or p.startswith("/") or ".." in p.split("/") or p.split("/")[0] == ".git":
        return None
    return p


def textish(path, size):
    name = path.rsplit("/", 1)[-1]
    if name in LOCKS or size > 40_000:
        return False
    return name in PLAIN_NAMES or any(name.lower().endswith(e) for e in TEXT_EXT)


def pick_files(tree, instruction, must, budget=80_000):
    sizes = {f["path"]: f["size"] for f in tree}
    low = instruction.lower()
    mentioned = [p for p in sizes if textish(p, sizes[p]) and (p.lower() in low or p.rsplit("/", 1)[-1].lower() in low)]
    rest = sorted((p for p in sizes if textish(p, sizes[p])),
                  key=lambda p: (0 if p.lower().startswith("readme") else 1, p.count("/"), sizes[p]))
    out, total = [], 0
    for p in [m for m in must if m in sizes] + mentioned + rest:
        if p in out:
            continue
        if total + sizes[p] > budget and p not in must:
            continue
        out.append(p)
        total += sizes[p]
    return out


def build_prompt(t, tree, files):
    lines = [f"Project: {t['project']} (commands run from the project root)", f"\nFile tree ({len(tree)} files):"]
    lines += [f"  {f['path']} ({f['size']} B)" for f in tree[:400]]
    if len(tree) > 400:
        lines.append(f"  ... {len(tree) - 400} more")
    lines.append("\nCurrent contents of the relevant files:")
    for path, content in files.items():
        if content is not None:
            lines.append(f"<<<CURRENT {path}>>>\n{content}\n<<<END>>>")
    lines.append(f"\nTask: {t['instruction']}")
    lines.append(f"Run command the user will use to check it: {t['run_cmd'] or '(none given)'}")
    prev = t["proposals"][-1] if t["proposals"] else None
    if t.get("fix_from"):
        r = t["fix_from"]
        lines.append(f"\nAttempt {t['attempt'] + 1}: after your last change the run command "
                     f"{'timed out' if r['timed_out'] else 'failed with exit code ' + str(r['exit'])}. Output (tail):\n"
                     f"```\n{r['output'][-8000:]}\n```\nYour previous plan was:\n{prev['plan'] if prev else ''}\n"
                     "Find the cause and fix it. Write the complete files again.")
    if t.get("note"):
        lines.append(f"\nThe user reviewed your last proposal and asked for this change: {t['note']}\n"
                     f"Your previous plan was:\n{prev['plan'] if prev else ''}")
    return "\n".join(lines)


def make_diff(path, old, new):
    d = "\n".join(difflib.unified_diff((old or "").splitlines(), new.splitlines(), f"a/{path}", f"b/{path}",
                                       lineterm="", n=3))
    return d[:30_000] + ("\n... [diff cut]" if len(d) > 30_000 else "")


async def think(t):
    t["status"] = "reading"
    note(t, "reading the project")
    tree = (await creq("torus.code.tree", {"project": t["project"]}))["files"]
    changed = [c["path"] for p in t["proposals"] for c in p["changes"]]
    paths = pick_files(tree, t["instruction"], t["focus"] + changed)
    files = (await creq("torus.code.read", {"project": t["project"], "paths": paths}))["files"]
    st = llm_settings()
    if not st["enabled"] or not key_present("anthropic"):
        raise RuntimeError("Code tasks need Claude. In Termux: torus key set anthropic, then torus llm on")
    t["status"] = "thinking"
    note(t, f"asking Claude ({len([v for v in files.values() if v is not None])} files in context)")
    res = await NREQ("torus.llm.request", {"prompt": build_prompt(t, tree, files), "system": SYSTEM,
                                           "model": t.get("model") or st["model"], "max_tokens": 16000,
                                           "job_id": t["id"]}, 900)
    if not res.get("ok"):
        raise RuntimeError(f"Claude: {res.get('error')}")
    text = res.get("text") or ""
    t["engine"] = f"claude:{res.get('model')}"
    changes, problems = [], []
    for m in FILE_RE.finditer(text):
        path = clean_path(m.group(1))
        if not path:
            problems.append(f"ignored an unsafe path: {m.group(1)!r}")
            continue
        content = m.group(2)
        changes = [c for c in changes if c["path"] != path]
        changes.append({"path": path, "action": "write", "content": content})
    for m in DEL_RE.finditer(text):
        path = clean_path(m.group(1))
        if path:
            changes.append({"path": path, "action": "delete", "content": None})
    if text.count("<<<FILE") > len(FILE_RE.findall(text)):
        problems.append("Claude's reply was cut off, so at least one file is incomplete and was left out. "
                        "Ask for a smaller change, name fewer files, or switch to Opus.")
    if res.get("stop_reason") == "max_tokens" and not problems:
        problems.append("Claude hit its length limit. Check that the plan is complete.")
    missing = [c["path"] for c in changes if c["path"] not in files]
    if missing:
        files.update((await creq("torus.code.read", {"project": t["project"], "paths": missing}))["files"])
    final = []
    for c in changes:
        old = files.get(c["path"])
        if c["action"] == "write" and old == c["content"]:
            continue
        c["is_new"] = old is None and c["action"] == "write"
        c["diff"] = make_diff(c["path"], old, c["content"] or "") if c["action"] == "write" else f"delete {c['path']}"
        c["added"] = sum(1 for ln in c["diff"].splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        c["removed"] = sum(1 for ln in c["diff"].splitlines() if ln.startswith("-") and not ln.startswith("---"))
        final.append(c)
    first = min([m.start() for m in re.finditer(r"<<<(FILE|DELETE)", text)] or [len(text)])
    t["proposals"].append({"attempt": t["attempt"], "plan": text[:first].strip(), "changes": final,
                           "problems": problems, "decided": "pending", "created": time.time()})
    note(t, f"Claude proposed {len(final)} file change(s)")


async def wait_review(t):
    t["status"] = "review"
    t["decision"] = None
    note(t, "waiting for your review")
    ev = EVENTS.setdefault(t["id"], asyncio.Event())
    while not t.get("decision"):
        ev.clear()
        await ev.wait()
    return t.pop("decision")


async def apply(t, p):
    t["status"] = "applying"
    save(t)
    res = await creq("torus.code.write", {"project": t["project"], "task_id": t["id"],
                                          "files": [{"path": c["path"], "content": c["content"]} for c in p["changes"]]})
    p["decided"] = "applied"
    t["applied"] = True
    note(t, f"wrote {len(res['written'])} file(s), deleted {len(res['deleted'])} (backup: {res['backup']})")


async def run_check(t):
    t["status"] = "running"
    note(t, f"running: {t['run_cmd']}")
    r = await creq("torus.code.run", {"project": t["project"], "cmd": t["run_cmd"], "timeout": t["timeout"]},
                   timeout=t["timeout"] + 20)
    t["runs"].append({"attempt": t["attempt"], "cmd": r["cmd"], "exit": r["exit"], "timed_out": r["timed_out"],
                      "output": r["output"][-12_000:], "duration": r["duration"]})
    note(t, f"exit {r['exit']}{' (timed out)' if r['timed_out'] else ''} in {r['duration']}s")
    return r


async def run_task(t):
    try:
        while True:
            pending = t["proposals"][-1] if t["proposals"] and t["proposals"][-1]["decided"] == "pending" else None
            if not pending:
                await think(t)
                pending = t["proposals"][-1]
            if not pending["changes"]:
                pending["decided"] = "empty"
                t["status"] = "done" if t["attempt"] == 0 else "failed"
                note(t, "no file changes proposed. Read the plan for Claude's explanation")
                return
            if t["attempt"] == 0 or not t["auto_fix"]:
                d = await wait_review(t)
                if d["action"] == "reject":
                    pending["decided"] = "rejected"
                    t["status"] = "rejected"
                    note(t, "you rejected the proposal. Nothing was written")
                    return
                if d["action"] == "revise":
                    pending["decided"] = "revised"
                    t["note"] = d.get("note") or "Please improve it."
                    note(t, "you asked for changes: " + t["note"][:200])
                    continue
            await apply(t, pending)
            t["note"], t["fix_from"] = "", None
            if not t["run_cmd"]:
                t["status"] = "done"
                note(t, "done (no run command to check with)")
                return
            r = await run_check(t)
            if r["exit"] == 0 and not r["timed_out"]:
                t["status"] = "done"
                note(t, "run passed")
                return
            if t["attempt"] + 1 >= t["max_attempts"]:
                t["status"] = "failed"
                note(t, f"still failing after {t['max_attempts']} attempt(s). Roll back, or start a new task with more detail")
                return
            t["attempt"] += 1
            t["fix_from"] = r
            note(t, f"asking Claude for a fix (attempt {t['attempt'] + 1} of {t['max_attempts']})")
    except asyncio.CancelledError:
        t["status"] = "cancelled"
        note(t, "cancelled")
        raise
    except Exception as e:
        t["status"], t["error"] = "error", str(e)
        note(t, "error: " + str(e))
        log("gatewayd", traceback.format_exc())
    finally:
        RUNNING.pop(t["id"], None)
        save(t)


def start(t):
    RUNNING[t["id"]] = asyncio.create_task(run_task(t))


def err(status, msg):
    return status, {"error": msg}


async def new_task(body):
    project = (body.get("project") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    if not project or not instruction:
        return err(400, "project and instruction are required")
    model = body.get("model") or None
    if model and model not in ("sonnet", "opus"):
        return err(400, "model must be sonnet or opus")
    try:
        max_attempts = max(1, min(5, int(body.get("max_attempts") or 3)))
        timeout = max(5, min(1800, int(body.get("timeout") or 120)))
    except (TypeError, ValueError):
        return err(400, "max_attempts and timeout must be numbers")
    focus = body.get("focus") or []
    if isinstance(focus, str):
        focus = re.split(r"[\s,]+", focus)
    focus = [p for p in (clean_path(f) for f in focus if f) if p][:20]
    tid = "c" + time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    t = {"id": tid, "project": project, "instruction": instruction, "run_cmd": (body.get("run_cmd") or "").strip(),
         "focus": focus, "model": model, "auto_fix": bool(body.get("auto_fix")), "max_attempts": max_attempts,
         "timeout": timeout, "status": "queued", "attempt": 0, "proposals": [], "runs": [], "log": [],
         "created": time.time(), "engine": "", "error": "", "note": "", "fix_from": None, "applied": False}
    await creq("torus.code.tree", {"project": project})  # fail fast if the project does not exist
    TASKS[tid] = t
    save(t)
    start(t)
    return 200, {"id": tid}


async def route(method, path, query, body):
    q = {k: v[0] for k, v in query.items()}
    try:
        if path == "/api/code/projects":
            if method == "GET":
                return 200, await creq("torus.code.projects", {})
            return 200, await creq("torus.code.create", {"name": body.get("name")})
        if method == "GET" and path == "/api/code/tree":
            return 200, await creq("torus.code.tree", {"project": q.get("project")})
        if method == "GET" and path == "/api/code/file":
            res = await creq("torus.code.read", {"project": q.get("project"), "paths": [q.get("path")]})
            return 200, {"path": q.get("path"), "content": res["files"].get(q.get("path"))}
        if method == "POST" and path == "/api/code/run":
            timeout = max(5, min(1800, int(body.get("timeout") or 120)))
            return 200, await creq("torus.code.run", {"project": body.get("project"), "cmd": body.get("cmd"),
                                                      "timeout": timeout}, timeout=timeout + 20)
        if path == "/api/code/tasks":
            if method == "POST":
                return await new_task(body)
            rows = sorted(TASKS.values(), key=lambda t: t["created"], reverse=True)[:60]
            return 200, {"tasks": [{k: t.get(k) for k in ("id", "project", "instruction", "status", "created", "attempt")}
                                   for t in rows]}
        m = re.fullmatch(r"/api/code/tasks/([0-9A-Za-z-]+)(?:/(review|cancel|rollback))?", path)
        if m:
            t = TASKS.get(m.group(1))
            if not t:
                return err(404, "no such task")
            act = m.group(2)
            if method == "GET" and not act:
                return 200, public(t)
            if method == "POST" and act == "review":
                if t["status"] != "review":
                    return err(409, "this task is not waiting for a review")
                if body.get("action") not in ("apply", "reject", "revise"):
                    return err(400, "action must be apply, reject or revise")
                t["decision"] = {"action": body["action"], "note": (body.get("note") or "").strip()[:2000]}
                EVENTS.setdefault(t["id"], asyncio.Event()).set()
                return 200, {"ok": True}
            if method == "POST" and act == "cancel":
                task = RUNNING.get(t["id"])
                if task:
                    task.cancel()
                return 200, {"ok": True}
            if method == "POST" and act == "rollback":
                if t["id"] in RUNNING:
                    return err(409, "cancel the task before rolling back")
                res = await creq("torus.code.rollback", {"project": t["project"], "task_id": t["id"]})
                t["status"] = "rolled_back"
                note(t, f"rolled back: restored {len(res['restored'])}, removed {len(res['removed'])}")
                return 200, res
    except RuntimeError as e:
        return err(503, str(e))
    except (TypeError, ValueError) as e:
        return err(400, str(e))
    return err(404, "not found")
