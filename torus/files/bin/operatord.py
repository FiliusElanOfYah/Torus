#!/usr/bin/env python3
"""operatord - Claude as a hands-on operator inside Termux (phase 10).

A task ("analyse the CSVs in Downloads", "add a retry to fetcherd", "update packages and re-run the audit")
goes through: plan (Claude proposes phases + cost) -> you approve once -> phases run one after another.
  reads   (files, folders, read-only commands)            run by themselves
  changes (write/delete files, other commands, installs) run inside the approved plan; each phase ends
          with a summary + diff. Settings "ask for every change" makes every change wait for a tap.
  danger  (rm -r, uninstalls, force-push, keys/seal, anything outside the Termux home or shared storage,
          delete, SMS, camera, microphone, commands not in the plan) ALWAYS waits for a tap.
Claude never runs commands itself: it answers JSON actions, this daemon executes them as the Termux user
(no root) and feeds the trimmed output back through sentineld's redaction. Key files are unreadable to it.
Files the agent writes/deletes are snapshotted first so "Undo phase" restores them (24 h, <= 50 MB each);
files changed by shell commands are listed but cannot be restored.
state: var/operator/<id>.json   snapshots: var/operator/snapshots/<id>/<phase>/   dossier: vault/answers/
"""
import asyncio
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import (AIRGAP, ETC, VAR, VAULT, atomic_write, key_present,  # noqa: E402
                          llm_settings, log, pack, read_conf, run_daemon, slugify, truthy)

NAME = "operatord"
CONF = ETC / "operator.conf"
DIR = VAR / "operator"
SNAP = DIR / "snapshots"
ANSWERS = VAULT / "answers"
TERMUX_HOME = Path(os.environ.get("HOME") or Path.home()).resolve()
SHARED = [Path("/storage/emulated/0"), Path("/sdcard"), TERMUX_HOME / "storage"]
PROTECTED = [AIRGAP, ETC / "gateway.token", VAR / "operator"]
MAX_OUT = 6000           # chars of tool output that reach Claude (head + tail)
SNAP_MAX = 50 * 1024 * 1024
SNAP_TTL = 24 * 3600
LLM_TIMEOUT = 900
NC = {"nc": None}
TASKS, EVENTS, RUN = {}, {}, {}
ACTIVE = {"planning", "running"}

READ_CMDS = {"ls", "cat", "head", "tail", "grep", "rg", "find", "fd", "pwd", "which", "du", "df", "wc", "stat", "file", "echo",
             "printf", "tree", "uname", "whoami", "id", "date", "uptime", "ps", "pgrep", "sort", "uniq", "cut", "awk",
             "sed", "tr", "diff", "md5sum", "sha256sum", "less", "more", "jq", "column", "nl", "readlink", "realpath", "basename",
             "dirname", "test", "true", "false", "type", "sv", "torus", "pip", "pkg", "python", "python3", "git", "termux-info"}
# tokens that turn an otherwise read-only program into a writer (find -delete, sed -i, sort -o, awk system(), ...)
WRITE_TOKENS = re.compile(r"(?:^|\s)(-delete|-exec\w*|-ok\w*|-fprint\w*|-i\b|-i\S*|--in-place\S*|-o\b|--output\S*|-w\b|--write\S*)(?=\s|$)|system\s*\(|\bxargs\b|\btee\b|\benv\b|\beval\b|\bexec\b")
SUBSHELL = re.compile(r"\$\(|`|\n|<\(|>\(")
READ_SUB = {"git": {"status", "log", "diff", "show", "branch", "remote", "ls-files", "blame", "rev-parse", "describe", "tag"},
            "pip": {"list", "show", "freeze", "check"}, "pkg": {"list-installed", "list-all", "show", "search", "files"},
            "sv": {"status"}, "torus": {"status", "doctor", "logs", "audit", "files", "backups", "llm", "oracle", "key"},
            "python": {"--version", "-V"}, "python3": {"--version", "-V"}}
SAFE_PROGS = {"python", "python3", "pip", "pkg", "git", "bash", "sh", "cp", "mv", "mkdir", "touch", "sed", "awk", "tee", "chmod",
              "tar", "unzip", "zip", "curl", "wget", "node", "npm", "make", "torus", "sv", "termux-wake-lock", "termux-wake-unlock"}
DANGER_RE = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b"), "deletes a whole folder"),
    (re.compile(r"\brm\s+-[a-zA-Z]*f"), "forces a delete"),
    (re.compile(r"\b(pkg|apt|pip|pip3|npm)\s+(uninstall|remove|purge|autoremove)\b"), "uninstalls a package"),
    (re.compile(r"\bgit\s+push\b.*(\s-f\b|--force)"), "force-pushes"),
    (re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-zA-Z]*f|checkout\s+--\s+\.)"), "throws away local changes"),
    (re.compile(r"\bchmod\s+(-R\s+)?[0-7]*7[0-7]{2}\b"), "makes files world-writable"),
    (re.compile(r"\b(mkfs|dd\s+if=|fdisk|shred)\b"), "touches storage at a low level"),
    (re.compile(r":\(\)\s*\{"), "fork bomb"),
    (re.compile(r"\bkill\s+-9\s+-1\b|\bkillall\b|\bpkill\s+-9\b"), "kills processes wholesale"),
    (re.compile(r"\bsv\s+(down|stop|kill)\b|\btorus\s+(restart|install|rollback|restore)\b"), "stops or reinstalls TORUS services"),
    (re.compile(r"\btorus\s+(key|seal)\b|\btorus_seal\.py|airgap|gateway\.token"), "touches keys or the seal"),
    (re.compile(r"\bcurl\b[^|]*\|\s*(ba)?sh\b|\bwget\b[^|]*\|\s*(ba)?sh\b"), "pipes a download straight into a shell"),
    (re.compile(r"\bsudo\b|\bsu\b|\btsu\b"), "asks for root (there is none)"),
]
PHONE = {  # feature: (termux-api command builder, level)
    "clipboard_get": (lambda a: ["termux-clipboard-get"], "read"),
    "clipboard_set": (lambda a: ["termux-clipboard-set", str(a.get("text", ""))], "change"),
    "notify": (lambda a: ["termux-notification", "--title", str(a.get("title", "TORUS")), "--content", str(a.get("text", ""))], "change"),
    "battery": (lambda a: ["termux-battery-status"], "read"),
    "wifi": (lambda a: ["termux-wifi-connectioninfo"], "read"),
    "device": (lambda a: ["termux-telephony-deviceinfo"], "read"),
    "open": (lambda a: ["termux-open", str(a.get("path", ""))], "change"),
    "camera": (lambda a: ["termux-camera-photo", "-c", str(a.get("camera", 0)), str(a.get("path", "photo.jpg"))], "danger"),
    "record": (lambda a: ["termux-microphone-record", "-l", str(int(a.get("seconds", 10))), "-f", str(a.get("path", "rec.m4a"))], "danger"),
    "sms_send": (lambda a: ["termux-sms-send", "-n", str(a.get("number", "")), str(a.get("text", ""))], "danger"),
    "sms_list": (lambda a: ["termux-sms-list", "-l", str(int(a.get("limit", 10)))], "change"),
    "location": (lambda a: ["termux-location"], "change"),
    "contacts": (lambda a: ["termux-contact-list"], "change"),
}
PHONE_WHY = {"camera": "takes a photo with the camera", "record": "records audio with the microphone", "sms_send": "sends an SMS from your number"}

PLAN_SYSTEM = (
    "You are TORUS Operator, planning work inside Termux on the user's Android phone (aarch64, bash, Python 3, no root, "
    "pkg/pip for packages, home {home}, shared storage under {shared}). Reply with ONE JSON object and nothing else:\n"
    '{{"phases":[{{"title":"short name","reads":["what you will look at"],"changes":["files you will write/delete, installs"],'
    '"runs":["commands you will run, with their program names"],"phone":["termux-api features: clipboard_get clipboard_set notify '
    'battery wifi device open camera record sms_send sms_list location contacts"]}}],"estimate_usd":0.25,"notes":"risks, questions"}}\n'
    "2-6 phases. Be concrete: name files and commands. Estimate the Claude cost from the reads you need. You cannot touch "
    "~/.torus/airgap, the seal or gateway.token; say so in notes if the task needs them.")
WORK_SYSTEM = (
    "You are TORUS Operator executing phase {n} of {total} ({title}) of an approved plan inside Termux on the user's Android "
    "phone (aarch64, bash, Python 3, no root, no sudo; pkg install / pip install; home {home}; shared storage {shared}; "
    "working folder {cwd}). You act ONLY through JSON. Reply with ONE JSON object, nothing else, in one of two shapes:\n"
    '{{"say":"one line on what you are doing","actions":[ ...up to 5 of: '
    '{{"tool":"read","path":"file"}} | {{"tool":"list","path":"folder"}} | {{"tool":"shell","cmd":"command","timeout":120}} | '
    '{{"tool":"write","path":"file","content":"ENTIRE file"}} | {{"tool":"delete","path":"file"}} | '
    '{{"tool":"phone","feature":"clipboard_get|clipboard_set|notify|battery|wifi|device|open|camera|record|sms_send|sms_list|location|contacts","args":{{}}}} ]}}\n'
    'or, when this phase is complete: {{"phase_done":true,"summary":"what changed and what you found, 3-10 lines"}}\n'
    "Rules: edit files with the write tool (whole file; it is snapshotted so the user can undo), use shell for running and "
    "installing. Stay inside this phase and the plan; anything else waits for the user's tap. Never try to read keys. "
    "Outputs are trimmed to {max_out} characters; ask for smaller slices (head/tail/grep) instead of whole big files. "
    "If an action is denied, continue without it or finish the phase explaining what is missing.")


# ---------------------------------------------------------------- settings / state
def op_settings():
    c = read_conf(CONF)
    try:
        budget = max(0.05, float(c.get("default_budget_usd") or 1))
    except ValueError:
        budget = 1.0
    try:
        steps = max(5, min(200, int(c.get("max_steps") or 40)))
    except ValueError:
        steps = 40
    cwd = Path(os.path.expanduser(c.get("cwd") or str(TERMUX_HOME)))
    return {"ask_every_change": truthy(c.get("ask_every_change", "false")), "default_budget_usd": budget,
            "model": c.get("model") or "sonnet", "max_steps": steps, "cwd": str(cwd)}


def save(t):
    DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(DIR / f"{t['id']}.json", json.dumps(t, ensure_ascii=False))


def public(t):
    return {k: v for k, v in t.items() if k != "messages"}


def entry(t, kind, text, **extra):
    t["log"].append({"t": time.time(), "kind": kind, "text": str(text)[:MAX_OUT], **extra})
    if len(t["log"]) > 600:
        t["log"] = t["log"][:50] + [{"t": time.time(), "kind": "note", "text": "[older log entries trimmed]"}] + t["log"][-500:]


def prune_snapshots():
    if not SNAP.exists():
        return
    cutoff = time.time() - SNAP_TTL
    for d in SNAP.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def load():
    DIR.mkdir(parents=True, exist_ok=True)
    SNAP.mkdir(parents=True, exist_ok=True)
    try:
        SNAP.chmod(0o700)
    except OSError:
        pass
    prune_snapshots()
    for p in sorted(DIR.glob("*.json"))[-100:]:
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            TASKS[t["id"]] = t
        except (ValueError, KeyError):
            continue
    for t in TASKS.values():
        if t["status"] in ACTIVE or t["status"] == "needs_you":
            t["status"], t["pending"] = "interrupted", None
            entry(t, "note", "TORUS restarted during this task. Check the files; Undo phase still works for 24 h.")
            save(t)


# ---------------------------------------------------------------- paths + classification
def resolve(path, cwd):
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve()


def is_protected(p):
    s = str(p)
    return any(s == str(x) or s.startswith(str(x) + "/") for x in PROTECTED)


def in_reach(p):
    s = str(p)
    roots = [TERMUX_HOME] + SHARED + [Path("/data/data/com.termux/files")]
    return any(s == str(r) or s.startswith(str(r) + "/") for r in roots)


def mentions_protected(text):
    return bool(re.search(r"\.torus/airgap|gateway\.token|mldsa65|MANIFEST\.sig|/operator/snapshots", text))


def shell_segments(cmd):
    return [s.strip() for s in re.split(r"\|\||&&|;|\|", cmd) if s.strip()]


def classify(action, plan_words, strict):
    """-> (level, why). level: blocked | read | change | danger."""
    tool = action.get("tool")
    if tool in ("read", "list", "write", "delete"):
        try:
            p = resolve(action.get("path", ""), action.get("_cwd", TERMUX_HOME))
        except (OSError, ValueError):
            return "blocked", "bad path"
        if is_protected(p):
            return "blocked", f"{p} holds keys/seal/snapshots and is off limits to the agent"
        if tool in ("read", "list"):
            return "read", ""
        if tool == "delete":
            return "danger", f"deletes {p}"
        if not in_reach(p):
            return "danger", f"writes outside the Termux home / shared storage: {p}"
        return ("danger" if strict else "change"), f"writes {p}"
    if tool == "shell":
        cmd = str(action.get("cmd") or "")
        if mentions_protected(cmd):
            return "blocked", "the command names a key, seal or snapshot file"
        for rx, why in DANGER_RE:
            if rx.search(cmd):
                return "danger", why
        segs = shell_segments(cmd)
        if segs and ">" not in cmd and all(_readonly(s) for s in segs):
            return "read", ""
        for s in segs:
            prog = (s.split() or [""])[0].rsplit("/", 1)[-1]
            if prog and prog not in SAFE_PROGS and prog not in READ_CMDS and prog not in plan_words:
                return "danger", f"`{prog}` is not in the approved plan"
        return ("danger" if strict else "change"), "runs a command that may change files"
    if tool == "phone":
        feat = str(action.get("feature") or "")
        if feat not in PHONE:
            return "blocked", f"unknown phone feature {feat}"
        level = PHONE[feat][1]
        if level == "change" and strict:
            level = "danger"
        return level, PHONE_WHY.get(feat, f"uses the phone's {feat.replace('_', ' ')}")
    return "blocked", f"unknown tool {tool}"


def _readonly(seg):
    words = seg.split()
    if not words:
        return True
    prog = words[0].rsplit("/", 1)[-1]
    if prog not in READ_CMDS:
        return False
    if prog in READ_SUB:
        sub = next((w for w in words[1:] if not w.startswith("-")), "")
        return sub in READ_SUB[prog] or (prog in ("python", "python3") and any(w in ("--version", "-V") for w in words))
    return True


def plan_vocab(plan):
    words = set()
    for ph in plan.get("phases", []):
        for k in ("runs", "changes", "reads"):
            for s in ph.get(k) or []:
                words.update(re.findall(r"[A-Za-z0-9_.+-]+", str(s)))
    return words


# ---------------------------------------------------------------- tools
def trim(text, n=MAX_OUT):
    text = text or ""
    if len(text) <= n:
        return text
    return text[: n // 2] + f"\n... [{len(text) - n} chars trimmed] ...\n" + text[-n // 2:]


def snapshot(t, phase, p):
    """Copy p (if it exists, <= 50 MB) under snapshots/<id>/<phase>/ before changing it. Returns the record."""
    snaps = t["phases"][phase].setdefault("snaps", {})
    key = str(p)
    if key in snaps:
        return snaps[key]
    rec = {"existed": p.exists(), "copy": None, "skipped": ""}
    if p.exists() and p.is_file():
        if p.stat().st_size > SNAP_MAX:
            rec["skipped"] = "over 50 MB, not snapshotted"
        else:
            d = SNAP / t["id"] / str(phase)
            d.mkdir(parents=True, exist_ok=True)
            dst = d / (hashlib.sha256(key.encode()).hexdigest()[:16] + "-" + p.name[:60])
            shutil.copy2(p, dst)
            rec["copy"] = str(dst)
    snaps[key] = rec
    return rec


def undo_phase(t, phase):
    ph = t["phases"][phase]
    restored, missing = [], []
    for key, rec in (ph.get("snaps") or {}).items():
        p = Path(key)
        try:
            if rec.get("copy") and Path(rec["copy"]).exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rec["copy"], p)
                restored.append(key)
            elif not rec.get("existed"):
                if p.exists():
                    p.unlink()
                restored.append(key)
            else:
                missing.append(key)
        except OSError as e:
            missing.append(f"{key} ({e.__class__.__name__})")
    ph["undone"] = time.time()
    entry(t, "undo", f"phase {phase + 1}: restored {len(restored)} file(s)" + (f"; could not restore: {', '.join(missing)}" if missing else ""))
    return {"restored": restored, "missing": missing}


def manifest(cwd, limit=5000):
    out, n = {}, 0
    skip = {".git", "venv", ".venv", "node_modules", "__pycache__", ".cache", "storage"}
    for root, dirs, files in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".torus")]
        for f in files:
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[p] = (int(st.st_mtime), st.st_size)
            n += 1
            if n >= limit:
                return out
    return out


async def run_shell(cmd, cwd, timeout):
    timeout = max(5, min(900, int(timeout or 120)))
    try:
        proc = await asyncio.create_subprocess_shell(cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                     env={**os.environ, "TORUS_OPERATOR": "1"})
    except OSError as e:
        return f"could not start: {e}", -1
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, _ = await proc.communicate()
        return (out or b"").decode("utf-8", "ignore") + f"\n[timed out after {timeout}s]", 124
    return (out or b"").decode("utf-8", "ignore"), proc.returncode


async def run_tool(t, phase, action):
    tool, cwd = action.get("tool"), t["cwd"]
    if tool in ("read", "list"):
        p = resolve(action.get("path", ""), cwd)
        if tool == "list":
            if not p.is_dir():
                return f"not a folder: {p}", False
            rows = []
            for c in sorted(p.iterdir())[:400]:
                try:
                    rows.append(f"{'d' if c.is_dir() else '-'} {c.stat().st_size:>10} {c.name}")
                except OSError:
                    rows.append(f"? {c.name}")
            return "\n".join(rows) or "(empty)", True
        if not p.is_file():
            return f"not a file: {p}", False
        if p.stat().st_size > 2_000_000:
            return f"{p} is {p.stat().st_size} bytes; use shell head/tail/grep for a slice", False
        return p.read_text(encoding="utf-8", errors="replace"), True
    if tool == "write":
        p = resolve(action.get("path", ""), cwd)
        content = str(action.get("content") or "")
        snapshot(t, phase, p)
        old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, content)
        d = "\n".join(difflib.unified_diff(old.splitlines(), content.splitlines(), f"a/{p.name}", f"b/{p.name}", lineterm="", n=2))
        t["phases"][phase].setdefault("diffs", []).append({"path": str(p), "diff": d[:20000], "t": time.time()})
        t["phases"][phase].setdefault("changed", [])
        if str(p) not in t["phases"][phase]["changed"]:
            t["phases"][phase]["changed"].append(str(p))
        return f"wrote {len(content)} chars to {p}", True
    if tool == "delete":
        p = resolve(action.get("path", ""), cwd)
        if not p.is_file():
            return f"not a file: {p}", False
        snapshot(t, phase, p)
        p.unlink()
        t["phases"][phase].setdefault("changed", []).append(str(p))
        return f"deleted {p}", True
    if tool == "shell":
        out, rc = await run_shell(str(action.get("cmd") or ""), cwd, action.get("timeout"))
        return f"[exit {rc}]\n{out}", rc == 0
    if tool == "phone":
        feat = action.get("feature")
        argv = PHONE[feat][0](action.get("args") or {})
        if not shutil.which(argv[0]):
            return f"{argv[0]} is not installed: install Termux:API from F-Droid and run  pkg install termux-api", False
        import shlex
        out, rc = await run_shell(" ".join(shlex.quote(a) for a in argv), cwd, 60)
        if rc != 0 and ("permission" in out.lower() or "denied" in out.lower() or not out.strip()):
            t.setdefault("disabled_phone", []).append(feat)
            return (f"Android denied the {feat} permission (or Termux:API is missing). This tool is off for the rest of the task; "
                    f"the user can allow it under Android Settings → Apps → Termux:API → Permissions."), False
        return f"[exit {rc}]\n{out}", rc == 0
    return f"unknown tool {tool}", False


# ---------------------------------------------------------------- Claude
async def nreq(subject, obj, timeout):
    msg = await NC["nc"].request(subject, pack(obj), timeout=timeout)
    return json.loads(msg.data)


async def llm(t, system, messages, max_tokens=4096):
    st = llm_settings()
    if not st["enabled"] or not key_present("anthropic"):
        raise RuntimeError("Claude is off or has no key: torus llm on / torus key set anthropic")
    res = await nreq("torus.llm.request", {"messages": messages, "system": system, "model": t["model"], "max_tokens": max_tokens,
                                           "job_id": f"op-{t['id']}"}, LLM_TIMEOUT)
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Claude did not answer")
    t["cost"] = round(t.get("cost", 0) + float(res.get("cost") or 0), 6)
    t["calls"] = t.get("calls", 0) + 1
    u = res.get("usage") or {}
    t["tokens_in"] = t.get("tokens_in", 0) + int(u.get("input_tokens") or 0)
    t["tokens_out"] = t.get("tokens_out", 0) + int(u.get("output_tokens") or 0)
    if t["cost"] >= t["budget_usd"]:
        raise BudgetStop(f"task budget ${t['budget_usd']:.2f} reached (spent ${t['cost']:.4f})")
    return res.get("text") or ""


class BudgetStop(Exception):
    pass


class Stopped(Exception):
    pass


def parse_json(text):
    text = text or ""
    i = text.find("{")
    dec = json.JSONDecoder()
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


# ---------------------------------------------------------------- the loop
def sentence(action, why):
    tool = action.get("tool")
    if tool == "shell":
        return f"Run `{str(action.get('cmd'))[:300]}` — {why}."
    if tool == "write":
        return f"Write {action.get('path')} ({len(str(action.get('content') or ''))} chars) — {why}."
    if tool == "delete":
        return f"Delete {action.get('path')} — {why}."
    if tool == "phone":
        return f"Phone: {action.get('feature')} {json.dumps(action.get('args') or {})[:200]} — {why}."
    return f"{tool} — {why}."


async def wait_tap(t, action, why):
    t["status"], t["pending"] = "needs_you", {"action": {k: v for k, v in action.items() if k != "content"},
                                              "why": why, "sentence": sentence(action, why), "t": time.time()}
    entry(t, "ask", t["pending"]["sentence"])
    save(t)
    ev = EVENTS.setdefault(t["id"], asyncio.Event())
    ev.clear()
    await ev.wait()
    ans = t.pop("answer", "deny")
    t["pending"] = None
    if ans == "stop":
        raise Stopped("stopped by you")
    t["status"] = "running"
    entry(t, "tap", "approved" if ans == "approve" else "denied")
    save(t)
    return ans == "approve"


async def plan_task(t):
    t["status"] = "planning"
    save(t)
    msgs = [{"role": "user", "content": f"Task: {t['task']}\nWorking folder: {t['cwd']}"}]
    for attempt in range(3):
        text = await llm(t, PLAN_SYSTEM.format(home=TERMUX_HOME, shared=SHARED[0]), msgs, 3000)
        plan = parse_json(text)
        if plan and isinstance(plan.get("phases"), list) and plan["phases"]:
            break
        msgs += [{"role": "assistant", "content": text}, {"role": "user", "content": "That was not the JSON plan object. Reply with the JSON only."}]
    else:
        raise RuntimeError("Claude did not return a usable plan")
    phases = []
    for ph in plan["phases"][:8]:
        phases.append({"title": str(ph.get("title") or f"Phase {len(phases) + 1}")[:120],
                       "reads": [str(x)[:200] for x in (ph.get("reads") or [])][:20], "changes": [str(x)[:200] for x in (ph.get("changes") or [])][:20],
                       "runs": [str(x)[:300] for x in (ph.get("runs") or [])][:20], "phone": [str(x) for x in (ph.get("phone") or []) if str(x) in PHONE],
                       "status": "pending", "summary": "", "changed": [], "diffs": [], "snaps": {}, "shell_changed": []})
    try:
        est = round(max(0.0, float(plan.get("estimate_usd") or 0)), 2)
    except (TypeError, ValueError):
        est = 0.0
    t.update(phases=phases, plan_notes=str(plan.get("notes") or "")[:2000], estimate_usd=est, status="plan_ready")
    entry(t, "plan", f"{len(phases)} phase(s), estimate ≈ ${est:.2f}" + (f" · phone: {', '.join(sorted({f for p in phases for f in p['phone']}))}" if any(p["phone"] for p in phases) else ""))
    save(t)


async def run_phase(t, i, strict, max_steps):
    ph = t["phases"][i]
    ph["status"], ph["started"] = "running", time.time()
    t["phase"] = i
    entry(t, "phase", f"phase {i + 1}/{len(t['phases'])}: {ph['title']}")
    save(t)
    before = await asyncio.to_thread(manifest, t["cwd"])
    plan_words = plan_vocab(t)
    system = WORK_SYSTEM.format(n=i + 1, total=len(t["phases"]), title=ph["title"], home=TERMUX_HOME, shared=SHARED[0], cwd=t["cwd"], max_out=MAX_OUT)
    context = (f"Task: {t['task']}\n\nApproved plan:\n" + "\n".join(f"{k + 1}. {p['title']} — reads: {'; '.join(p['reads'])} | changes: "
               f"{'; '.join(p['changes'])} | runs: {'; '.join(p['runs'])}" for k, p in enumerate(t["phases"]))
               + "".join(f"\n\nPhase {k + 1} summary: {p['summary']}" for k, p in enumerate(t["phases"][:i]) if p.get("summary"))
               + f"\n\nNow do phase {i + 1}: {ph['title']}. Start.")
    msgs = [{"role": "user", "content": context}]
    bad = 0
    for step in range(max_steps):
        text = await llm(t, system, msgs)
        reply = parse_json(text)
        msgs.append({"role": "assistant", "content": text})
        if not reply:
            bad += 1
            if bad > 2:
                raise RuntimeError("Claude kept answering without the JSON action object")
            msgs.append({"role": "user", "content": "Reply with ONE JSON object only (actions or phase_done)."})
            continue
        if reply.get("phase_done"):
            ph["summary"] = str(reply.get("summary") or "")[:4000]
            break
        if reply.get("say"):
            entry(t, "say", reply["say"])
        results = []
        for action in (reply.get("actions") or [])[:5]:
            if not isinstance(action, dict):
                continue
            action["_cwd"] = t["cwd"]
            if action.get("tool") == "phone" and action.get("feature") in t.get("disabled_phone", []):
                results.append(f"{action.get('feature')}: permission denied earlier; tool is off for this task")
                continue
            level, why = classify(action, plan_words, strict)
            action.pop("_cwd", None)
            label = action.get("cmd") if action.get("tool") == "shell" else f"{action.get('tool')} {action.get('path') or action.get('feature') or ''}"
            if level == "blocked":
                entry(t, "blocked", f"{label}: {why}")
                results.append(f"BLOCKED: {why}")
                continue
            if level == "danger" or (level == "change" and strict):
                if not await wait_tap(t, action, why):
                    results.append(f"User DENIED: {sentence(action, why)}")
                    continue
            entry(t, "cmd", label, level=level)
            out, ok = await run_tool(t, i, action)
            entry(t, "out", trim(out, 2500), ok=ok)
            results.append(f"### {label}\n{trim(out)}")
            save(t)
        msgs.append({"role": "user", "content": "\n\n".join(results) or "No actions were given. Continue or send phase_done."})
        if len(msgs) > 24:  # keep the context (first message) and the latest exchanges
            msgs = msgs[:1] + [{"role": "user", "content": "[earlier steps trimmed]"}] + msgs[-18:]
        save(t)
    else:
        ph["summary"] = ph.get("summary") or f"stopped after {max_steps} steps without phase_done"
    after = await asyncio.to_thread(manifest, t["cwd"])
    ph["shell_changed"] = sorted(p for p in (set(after) | set(before)) if before.get(p) != after.get(p) and p not in ph.get("changed", []))[:200]
    ph["status"], ph["finished"] = "done", time.time()
    entry(t, "summary", ph["summary"], phase=i)
    save(t)


async def run_task(t):
    conf = op_settings()
    strict = conf["ask_every_change"] or t.get("ask_every_change", False)
    wake = shutil.which("termux-wake-lock")
    if wake:
        await run_shell("termux-wake-lock", t["cwd"], 10)
    try:
        for i in range(t.get("phase", 0), len(t["phases"])):
            if t["phases"][i]["status"] == "done":
                continue
            await run_phase(t, i, strict, conf["max_steps"])
            if i + 1 < len(t["phases"]) and t.get("pause_between"):
                t["status"] = "paused"
                entry(t, "note", "paused between phases (tap Continue)")
                save(t)
                ev = EVENTS.setdefault(t["id"], asyncio.Event())
                ev.clear()
                await ev.wait()
                if t.pop("answer", "") == "stop":
                    raise Stopped("stopped by you")
                t["status"] = "running"
        t["status"] = "done"
        entry(t, "done", f"all {len(t['phases'])} phase(s) done · ≈ ${t['cost']:.4f}")
    except BudgetStop as e:
        t["status"], t["stop_reason"] = "stopped", str(e)
        entry(t, "stop", str(e))
    except Stopped as e:
        t["status"], t["stop_reason"] = "stopped", str(e)
        entry(t, "stop", str(e))
    except asyncio.CancelledError:
        t["status"], t["stop_reason"] = "stopped", "stopped by you"
        entry(t, "stop", "stopped by you")
    except Exception as e:
        t["status"], t["stop_reason"] = "error", f"{e.__class__.__name__}: {e}"
        entry(t, "error", t["stop_reason"])
        log(NAME, "task failed", t["id"], t["stop_reason"])
    finally:
        t["pending"], t["finished"] = None, time.time()
        for ph in t["phases"]:
            if ph["status"] == "running":
                ph["status"] = "interrupted"
        write_dossier(t)
        save(t)
        RUN.pop(t["id"], None)
        if wake:
            await run_shell("termux-wake-unlock", t["cwd"], 10)
        if shutil.which("termux-notification"):
            import shlex
            await run_shell(f"termux-notification --title {shlex.quote('TORUS Operator')} --content "
                            f"{shlex.quote(t['status'] + ': ' + t['task'][:80])} --id torus-op", t["cwd"], 10)


def write_dossier(t):
    ANSWERS.mkdir(parents=True, exist_ok=True)
    path = ANSWERS / f"{t['id']}-{slugify(t['task'], 50)}.md"
    parts = [f"---\njob: {t['id']}\nengine: operator:{t['model']}\nstatus: {t['status']}\ncost_usd: {t['cost']}\n"
             f"created: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t['created']))}\n---\n\n# Operator: {t['task']}\n",
             f"**{t['status']}** · {t.get('calls', 0)} Claude calls · ≈ ${t['cost']:.4f}" + (f" · {t['stop_reason']}" if t.get("stop_reason") else "")]
    if t.get("plan_notes"):
        parts.append(f"\n## Plan notes\n\n{t['plan_notes']}")
    for i, ph in enumerate(t["phases"]):
        parts.append(f"\n## Phase {i + 1}: {ph['title']} ({ph['status']})\n\n{ph.get('summary') or '(no summary)'}")
        if ph.get("changed"):
            parts.append("\nFiles written/deleted (undoable):\n" + "\n".join(f"- {p}" for p in ph["changed"]))
        if ph.get("shell_changed"):
            parts.append("\nFiles changed by commands (not undoable):\n" + "\n".join(f"- {p}" for p in ph["shell_changed"][:50]))
        for d in ph.get("diffs") or []:
            parts.append(f"\n```diff\n{d['diff'][:6000]}\n```")
    parts.append("\n## Log\n\n" + "\n".join(f"- {time.strftime('%H:%M:%S', time.localtime(e['t']))} {e['kind']}: {e['text'][:300]}"
                                           for e in t["log"][-120:] if e["kind"] != "out"))
    atomic_write(path, "\n".join(parts) + "\n")
    t["dossier"] = str(path)


# ---------------------------------------------------------------- handlers
def start(t, coro):
    task = asyncio.create_task(coro)
    RUN[t["id"]] = task


async def on_create(nc, msg, data):
    conf = op_settings()
    task = str(data.get("task") or "").strip()
    if len(task) < 3:
        return await reply(nc, msg, {"ok": False, "error": "describe the task"})
    model = data.get("model") or conf["model"]
    if model not in ("sonnet", "opus", "haiku"):
        return await reply(nc, msg, {"ok": False, "error": "model must be sonnet, opus or haiku"})
    try:
        budget = round(max(0.05, min(100.0, float(data.get("budget_usd") or conf["default_budget_usd"]))), 2)
    except (TypeError, ValueError):
        return await reply(nc, msg, {"ok": False, "error": "budget_usd must be a number"})
    cwd = Path(os.path.expanduser(str(data.get("cwd") or conf["cwd"])))
    if not cwd.is_dir() or not in_reach(cwd.resolve()) or is_protected(cwd.resolve()):
        return await reply(nc, msg, {"ok": False, "error": f"working folder must exist inside the Termux home or shared storage: {cwd}"})
    if len([x for x in TASKS.values() if x["status"] in ACTIVE | {"needs_you", "paused"}]) >= 2:
        return await reply(nc, msg, {"ok": False, "error": "two tasks are already active; stop one first"})
    tid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    t = {"id": tid, "task": task[:2000], "model": model, "budget_usd": budget, "pause_between": bool(data.get("pause_between")),
         "ask_every_change": bool(data.get("ask_every_change")), "cwd": str(cwd.resolve()), "created": time.time(), "status": "planning",
         "phases": [], "phase": 0, "log": [], "cost": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "pending": None, "stop_reason": ""}
    TASKS[tid] = t
    save(t)

    async def go():
        try:
            await plan_task(t)
        except Exception as e:
            t["status"], t["stop_reason"] = "error", f"{e.__class__.__name__}: {e}" if not isinstance(e, BudgetStop) else str(e)
            entry(t, "error", t["stop_reason"])
            save(t)
        finally:
            RUN.pop(tid, None)
    start(t, go())
    await reply(nc, msg, {"ok": True, "task": public(t)})


async def on_list(nc, msg, data):
    rows = sorted(TASKS.values(), key=lambda x: x["created"], reverse=True)[:60]
    await reply(nc, msg, {"ok": True, "tasks": [{k: x.get(k) for k in ("id", "task", "status", "created", "cost", "model", "phase", "finished")}
                                                | {"phases": len(x["phases"])} for x in rows], "settings": op_settings(),
                          "termux_api": bool(shutil.which("termux-clipboard-get"))})


async def on_get(nc, msg, data):
    t = TASKS.get(str(data.get("id") or ""))
    if not t:
        return await reply(nc, msg, {"ok": False, "error": "no such task"})
    await reply(nc, msg, {"ok": True, "task": public(t)})


async def on_action(nc, msg, data):
    t = TASKS.get(str(data.get("id") or ""))
    act = str(data.get("action") or "")
    if not t:
        return await reply(nc, msg, {"ok": False, "error": "no such task"})
    if act == "approve_plan":
        if t["status"] != "plan_ready":
            return await reply(nc, msg, {"ok": False, "error": f"task is {t['status']}, not waiting for plan approval"})
        if "budget_usd" in data:
            try:
                t["budget_usd"] = round(max(0.05, min(100.0, float(data["budget_usd"]))), 2)
            except (TypeError, ValueError):
                return await reply(nc, msg, {"ok": False, "error": "budget_usd must be a number"})
        if "pause_between" in data:
            t["pause_between"] = bool(data["pause_between"])
        if "ask_every_change" in data:
            t["ask_every_change"] = bool(data["ask_every_change"])
        t["status"] = "running"
        entry(t, "tap", f"plan approved · budget ${t['budget_usd']:.2f}" + (" · pause between phases" if t["pause_between"] else ""))
        save(t)
        start(t, run_task(t))
    elif act in ("approve", "deny"):
        if t["status"] != "needs_you":
            return await reply(nc, msg, {"ok": False, "error": "nothing is waiting for a tap"})
        t["answer"] = act
        EVENTS.setdefault(t["id"], asyncio.Event()).set()
    elif act == "resume":
        if t["status"] != "paused":
            return await reply(nc, msg, {"ok": False, "error": "task is not paused"})
        t["answer"] = "resume"
        EVENTS.setdefault(t["id"], asyncio.Event()).set()
    elif act == "stop":
        if t["status"] in ("needs_you", "paused"):
            t["answer"] = "stop"
            EVENTS.setdefault(t["id"], asyncio.Event()).set()
        elif t["id"] in RUN:
            RUN[t["id"]].cancel()
        elif t["status"] == "plan_ready":
            t["status"], t["stop_reason"] = "stopped", "plan declined"
            entry(t, "stop", "plan declined")
            save(t)
        else:
            return await reply(nc, msg, {"ok": False, "error": f"task is already {t['status']}"})
    elif act == "undo":
        try:
            i = int(data.get("phase"))
            ph = t["phases"][i]
        except (TypeError, ValueError, IndexError):
            return await reply(nc, msg, {"ok": False, "error": "which phase?"})
        if t["status"] in ACTIVE | {"needs_you"}:
            return await reply(nc, msg, {"ok": False, "error": "stop the task before undoing"})
        if ph.get("undone"):
            return await reply(nc, msg, {"ok": False, "error": "that phase was already undone"})
        res = await asyncio.to_thread(undo_phase, t, i)
        save(t)
        return await reply(nc, msg, {"ok": True, "task": public(t), **res})
    else:
        return await reply(nc, msg, {"ok": False, "error": f"unknown action {act}"})
    await asyncio.sleep(0.05)
    await reply(nc, msg, {"ok": True, "task": public(t)})


async def reply(nc, msg, obj):
    if msg.reply:
        await nc.publish(msg.reply, pack(obj))


async def on_start(nc):
    NC["nc"] = nc
    load()
    log(NAME, f"{len(TASKS)} task(s) loaded; home {TERMUX_HOME}; termux-api {'yes' if shutil.which('termux-clipboard-get') else 'no'}")


if __name__ == "__main__":
    run_daemon(NAME, {"torus.op.create": on_create, "torus.op.list": on_list, "torus.op.get": on_get, "torus.op.action": on_action},
               concurrent=True, on_start=on_start)
