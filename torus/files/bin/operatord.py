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
import torus_github as gh  # noqa: E402
import gateway_upload as up  # noqa: E402
from torus_common import (AIRGAP, ETC, VAR, VAULT, atomic_write, get_secret, key_present,  # noqa: E402
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
    "~/.torus/airgap, the seal or gateway.token; say so in notes if the task needs them.{github}")
GITHUB_PLAN = ("\nGitHub: a github tool is available for {repos}: reads (repos, tree, read, search, commits, issues, prs, pr_diff) and "
               "writes (clone, branch, commit, pr, comment, revert, pr_close; merge and delete_branch always need the user's tap; commits "
               "straight to the default branch need a tap, so prefer a branch + PR). Name repos, files, branches and PRs in `reads`/`changes`.")
WORK_SYSTEM = (
    "You are TORUS Operator executing phase {n} of {total} ({title}) of an approved plan inside Termux on the user's Android "
    "phone (aarch64, bash, Python 3, no root, no sudo; pkg install / pip install; home {home}; shared storage {shared}; "
    "working folder {cwd}). You act ONLY through JSON. Reply with ONE JSON object, nothing else, in one of two shapes:\n"
    '{{"say":"one line on what you are doing","actions":[ ...up to 5 of: '
    '{{"tool":"read","path":"file"}} | {{"tool":"list","path":"folder"}} | {{"tool":"shell","cmd":"command","timeout":120}} | '
    '{{"tool":"write","path":"file","content":"ENTIRE file"}} | {{"tool":"delete","path":"file"}} | '
    '{{"tool":"phone","feature":"clipboard_get|clipboard_set|notify|battery|wifi|device|open|camera|record|sms_send|sms_list|location|contacts","args":{{}}}}{github} ]}}\n'
    'or, when this phase is complete: {{"phase_done":true,"summary":"what changed and what you found, 3-10 lines"}}\n'
    "Rules: edit files with the write tool (whole file; it is snapshotted so the user can undo), use shell for running and "
    "installing. Stay inside this phase and the plan; anything else waits for the user's tap. Never try to read keys. "
    "Outputs are trimmed to {max_out} characters; ask for smaller slices (head/tail/grep) instead of whole big files. "
    "If an action is denied, continue without it or finish the phase explaining what is missing.")
GITHUB_WORK = (' | {{"tool":"github","op":"repos|tree|read|search|commits|issues|prs|pr_diff|clone","repo":"owner/name",'
               '"path":"","ref":"","q":"","n":10,"state":"open","dest":"folder for clone"}} | '
               '{{"tool":"github","op":"branch","repo":"owner/name","branch":"new-branch","from":"main"}} | '
               '{{"tool":"github","op":"commit","repo":"owner/name","branch":"branch","message":"why","files":[{{"path":"a.py","content":"ENTIRE file"}},{{"path":"old.txt","delete":true}}]}} | '
               '{{"tool":"github","op":"pr","repo":"owner/name","head":"branch","base":"main","title":"...","body":"...","draft":false}} | '
               '{{"tool":"github","op":"comment|pr_close|merge","repo":"owner/name","n":12,"body":"..."}} | '
               '{{"tool":"github","op":"revert|delete_branch","repo":"owner/name","branch":"branch"}} | '
               '{{"tool":"github","op":"push","repo":"owner/name","path":"local git clone","branch":"branch","remote":"origin"}}'
               '  (GitHub, repos: {repos}. Writes need a branch; the default branch, merge, delete_branch and force need the user\'s tap)')


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
            "model": c.get("model") or "sonnet", "max_steps": steps, "cwd": str(cwd), "github_scope": gh.scope(),
            "github_key": key_present("github")}


def uploads_text(t):
    if not t.get("uploads") or not Path(t["uploads"]).is_dir():
        return ""
    return f"\nUploaded files (in {t['uploads']}; zips are kept intact, each has a <name>.list):\n" + (up.files_text(t["uploads"]) or "(none)")


def github_repos_text(t):
    """What Claude may reach on GitHub for this task ('' when GitHub is not available to it)."""
    if not t.get("github_key"):
        return ""
    if t.get("github_scope") == "all":
        return "any repo the token can see" + (f" (the user named: {', '.join(t['repos'])})" if t.get("repos") else "")
    return ", ".join(t.get("repos") or []) or ""


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
    try:
        days = int(read_conf(ETC / "hardening.conf").get("uploads_keep_days") or 7)
    except ValueError:
        days = 7
    keep = {x["id"] for x in TASKS.values() if x["status"] in ACTIVE | {"needs_you", "paused", "plan_ready"}}
    n = up.prune(days, keep)
    if n:
        log(NAME, f"pruned {n} upload folder(s) older than {days} days")
    for t in TASKS.values():
        if t["id"] == CONSOLE_ID:
            t["status"], t["pending"] = "idle", None
            continue
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
        if segs and ">" not in cmd and not SUBSHELL.search(cmd) and not WRITE_TOKENS.search(cmd) and all(_readonly(s) for s in segs):
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
    if tool == "github":
        op = str(action.get("op") or "")
        if op not in gh.OPS:
            return "blocked", f"unknown GitHub op {op}"
        if not action.get("_github_key", True):
            return "blocked", "no GitHub token stored: torus key set github"
        repo = str(action.get("repo") or "").strip()
        mode = action.get("_gh_scope") or "task"
        if op == "repos" or (op == "search" and not repo):
            pass
        elif not gh.REPO_RE.match(repo):
            return "blocked", "repo must look like owner/name"
        elif not gh.in_scope(repo, action.get("_repos") or [], mode):
            return "blocked", f"{repo} is not in this task's repo list (add it to the task, or Settings → GitHub scope: all)"
        if op in gh.READ_OPS:
            return "read", ""
        if op in gh.DANGER_OPS:
            return "danger", {"merge": f"merges PR #{action.get('n')} in {repo}", "delete_branch": f"deletes branch {action.get('branch')} in {repo}"}[op]
        if op == "push":
            try:
                p = resolve(action.get("path") or "", action.get("_cwd", TERMUX_HOME))
            except (OSError, ValueError):
                return "blocked", "bad clone path"
            if is_protected(p) or not in_reach(p):
                return "blocked", f"{p} is outside the Termux home"
            if action.get("force"):
                return "danger", f"force-pushes {action.get('branch') or 'HEAD'} to {repo}"
            if action.get("_default") and action.get("branch") == action["_default"]:
                return "danger", f"pushes straight to the default branch {action['_default']} of {repo}"
            return ("danger" if strict else "change"), f"pushes {action.get('branch') or 'the current branch'} from {p} to {repo}"
        if op == "commit" and action.get("_default") and action.get("branch") == action["_default"]:
            return "danger", f"commits straight to the default branch {action['_default']} of {repo}"
        if op != "clone":
            why = {"branch": f"creates branch {action.get('branch')} in {repo}", "commit": f"commits {len(action.get('files') or [])} file(s) to {action.get('branch')} in {repo}",
                   "pr": f"opens a pull request in {repo}", "comment": f"comments on #{action.get('n')} in {repo}", "revert": f"reverts the latest commit on {action.get('branch')} in {repo}",
                   "pr_close": f"closes PR #{action.get('n')} in {repo}"}[op]
            return ("danger" if strict else "change"), why
        try:
            p = resolve(action.get("dest") or action.get("path") or "", action.get("_cwd", TERMUX_HOME))
        except (OSError, ValueError):
            return "blocked", "bad clone destination"
        if is_protected(p):
            return "blocked", f"{p} holds keys/seal/snapshots and is off limits to the agent"
        if not in_reach(p):
            return "danger", f"clones {repo} outside the Termux home / shared storage: {p}"
        return ("danger" if strict else "change"), f"clones {repo} into {p}"
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


async def run_shell(cmd, cwd, timeout, env=None):
    timeout = max(5, min(900, int(timeout or 120)))
    try:
        proc = await asyncio.create_subprocess_shell(cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                     env={**os.environ, "TORUS_OPERATOR": "1", **(env or {})})
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
    if tool == "github":
        if not key_present("github"):
            return "no GitHub token stored: torus key set github", False
        op = str(action.get("op") or "")
        a = dict(action)
        if op == "push":
            p = resolve(action.get("path") or "", cwd)
            if not (p / ".git").exists():
                return f"{p} is not a git clone (no .git); use commit for tarball snapshots", False
            try:
                argv, env = gh.push_command(a, Path(__file__).with_name("torus_askpass.py"))
            except gh.GitHubError as e:
                return str(e), False
            import shlex
            out, rc = await run_shell(" ".join(shlex.quote(x) for x in argv), str(p), 300, env)
            if rc == 0:
                t["phases"][phase].setdefault("github", []).append({"op": "push", "repo": action.get("repo"), "text": f"pushed {action.get('branch') or 'HEAD'}",
                                                                   "url": f"https://github.com/{action.get('repo')}/tree/{action.get('branch') or ''}".rstrip("/")})
            return f"[exit {rc}]\n{out}", rc == 0
        if op == "clone":
            dest = resolve(action.get("dest") or action.get("path") or "", cwd)
            a["_dest"] = str(dest)
        try:
            token = await get_secret("github")
            out = await gh.run(op, a, token, t.get("repos") or [], t.get("github_scope") or "task")
        except (gh.GitHubError, RuntimeError) as e:
            return str(e), False
        if op == "clone":
            t["phases"][phase].setdefault("cloned", []).append(str(a["_dest"]))
        elif op not in gh.READ_OPS:
            url = re.search(r"https://github\.com/\S+", out)
            t["phases"][phase].setdefault("github", []).append({"op": op, "repo": action.get("repo"), "text": out.splitlines()[0][:160], "url": url.group(0) if url else ""})
        return out, True
    return f"unknown tool {tool}", False


async def github_default(t, repo):
    """Default branch of repo (cached on the task) so writes to it can be escalated to danger before they run."""
    cache = t.setdefault("gh_default", {})
    if repo in cache:
        return cache[repo]
    try:
        import httpx
        token = await get_secret("github")
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as client:
            cache[repo] = await gh.default_branch(client, token, repo)
    except Exception:
        cache[repo] = "main"
    return cache[repo]


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
    if tool == "github":
        return f"GitHub {action.get('op')} {action.get('repo') or ''} {action.get('branch') or action.get('path') or action.get('dest') or (('#' + str(action.get('n'))) if action.get('n') else '')} — {why}."
    return f"{tool} — {why}."


def action_label(action):
    tool = action.get("tool")
    if tool == "shell":
        return action.get("cmd")
    if tool == "github":
        return f"github {action.get('op')} {action.get('repo') or ''} {action.get('branch') or action.get('path') or action.get('q') or action.get('dest') or (('#' + str(action.get('n'))) if action.get('n') and action.get('op') not in ('commits', 'issues', 'prs') else '')}".rstrip()
    return f"{tool} {action.get('path') or action.get('feature') or ''}"


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
    msgs = [{"role": "user", "content": f"Task: {t['task']}\nWorking folder: {t['cwd']}" + (f"\nGitHub repos: {', '.join(t['repos'])}" if t.get("repos") else "") + uploads_text(t)}]
    repos_text = github_repos_text(t)
    for attempt in range(3):
        text = await llm(t, PLAN_SYSTEM.format(home=TERMUX_HOME, shared=SHARED[0], github=GITHUB_PLAN.format(repos=repos_text) if repos_text else ""), msgs, 3000)
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
    repos_text = github_repos_text(t)
    system = WORK_SYSTEM.format(n=i + 1, total=len(t["phases"]), title=ph["title"], home=TERMUX_HOME, shared=SHARED[0], cwd=t["cwd"], max_out=MAX_OUT,
                                github=GITHUB_WORK.format(repos=repos_text) if repos_text else "")
    context = (f"Task: {t['task']}{uploads_text(t)}\n\nApproved plan:\n" + "\n".join(f"{k + 1}. {p['title']} — reads: {'; '.join(p['reads'])} | changes: "
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
            if action.get("tool") == "github":
                action["_repos"], action["_gh_scope"], action["_github_key"] = t.get("repos") or [], t.get("github_scope") or "task", bool(t.get("github_key"))
                if action.get("op") in gh.BRANCH_OPS and gh.REPO_RE.match(str(action.get("repo") or "")) and gh.in_scope(action["repo"], action["_repos"], action["_gh_scope"]):
                    action["_default"] = await github_default(t, action["repo"])
            if action.get("tool") == "phone" and action.get("feature") in t.get("disabled_phone", []):
                results.append(f"{action.get('feature')}: permission denied earlier; tool is off for this task")
                continue
            level, why = classify(action, plan_words, strict)
            for k in ("_cwd", "_repos", "_gh_scope", "_github_key", "_default"):
                action.pop(k, None)
            label = action_label(action)
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
             f"**{t['status']}** · {t.get('calls', 0)} Claude calls · ≈ ${t['cost']:.4f}" + (f" · {t['stop_reason']}" if t.get("stop_reason") else "")
             + (f" · GitHub: {', '.join(t['repos'])}" if t.get("repos") else "")]
    if t.get("plan_notes"):
        parts.append(f"\n## Plan notes\n\n{t['plan_notes']}")
    if t.get("files"):
        parts.append(f"\n## Uploaded files ({t['uploads']})\n\n" + "\n".join(f"- {f['name']} ({f['size']} bytes)" + (f" · zip: {f['zip']['summary']}" if f.get("zip") else "") for f in t["files"]))
    for i, ph in enumerate(t["phases"]):
        parts.append(f"\n## Phase {i + 1}: {ph['title']} ({ph['status']})\n\n{ph.get('summary') or '(no summary)'}")
        if ph.get("changed"):
            parts.append("\nFiles written/deleted (undoable):\n" + "\n".join(f"- {p}" for p in ph["changed"]))
        if ph.get("shell_changed"):
            parts.append("\nFiles changed by commands (not undoable):\n" + "\n".join(f"- {p}" for p in ph["shell_changed"][:50]))
        if ph.get("cloned"):
            parts.append("\nGitHub clones (snapshots without .git):\n" + "\n".join(f"- {p}" for p in ph["cloned"]))
        if ph.get("github"):
            parts.append("\nGitHub writes:\n" + "\n".join(f"- {g['op']} {g['repo']}: {g['text']}" + (f" <{g['url']}>" if g.get("url") else "") for g in ph["github"]))
        for d in ph.get("diffs") or []:
            parts.append(f"\n```diff\n{d['diff'][:6000]}\n```")
    parts.append("\n## Log\n\n" + "\n".join(f"- {time.strftime('%H:%M:%S', time.localtime(e['t']))} {e['kind']}: {e['text'][:300]}"
                                           for e in t["log"][-120:] if e["kind"] != "out"))
    atomic_write(path, "\n".join(parts) + "\n")
    t["dossier"] = str(path)


# ---------------------------------------------------------------- console (phase 3b): one persistent chat-terminal session
CONSOLE_ID = "console"
CONSOLE_SYSTEM = (
    "You are TORUS Console: an interactive operator inside Termux on the user's Android phone (aarch64, bash, Python 3, no root, "
    "no sudo; pkg/pip; home {home}; shared storage {shared}; working folder {cwd}). The user types plain words or raw commands; you "
    "act ONLY through JSON. Reply with ONE JSON object: {{\"say\":\"one or two lines for the user\",\"actions\":[ ...up to 5 of: "
    '{{"tool":"read","path":"file"}} | {{"tool":"list","path":"folder"}} | {{"tool":"shell","cmd":"command","timeout":120}} | '
    '{{"tool":"write","path":"file","content":"ENTIRE file"}} | {{"tool":"delete","path":"file"}} | '
    '{{"tool":"phone","feature":"clipboard_get|clipboard_set|notify|battery|wifi|device|open|camera|record|sms_send|sms_list|location|contacts","args":{{}}}}{github} ]}}\n'
    "Leave actions empty (or out) when you are answering or done — that ends the turn. If the user typed a command, run it as given. "
    "Reads run at once; anything that changes something shows the user an Allow/Deny and may be denied — then continue without it. "
    "Never try to read keys. Outputs are trimmed to {max_out} characters; ask for slices (head/tail/grep). Be brief.")
CONSOLE_ROUNDS = 6


def console():
    """The console session is a task record with a fixed id so taps, snapshots, undo and the LLM budget all reuse the task code."""
    c = TASKS.get(CONSOLE_ID)
    if c and c.get("phases"):
        return c
    conf = op_settings()
    c = {"id": CONSOLE_ID, "task": "console", "model": conf["model"], "budget_usd": conf["default_budget_usd"], "cwd": conf["cwd"], "created": time.time(),
         "status": "idle", "phases": [{"title": "console", "status": "running", "summary": "", "changed": [], "diffs": [], "snaps": {}, "shell_changed": []}],
         "phase": 0, "log": [], "messages": [], "cost": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "pending": None, "stop_reason": "",
         "repos": [], "github_scope": conf["github_scope"], "github_key": conf["github_key"]}
    TASKS[CONSOLE_ID] = c
    return c


async def console_turn(t, text):
    conf = op_settings()
    t.update(model=conf["model"], budget_usd=conf["default_budget_usd"], github_scope="all" if conf["github_scope"] == "all" else "task",
             github_key=conf["github_key"], status="running", stop_reason="")
    if not Path(t["cwd"]).is_dir():
        t["cwd"] = conf["cwd"]
    repos_text = github_repos_text(t)
    system = CONSOLE_SYSTEM.format(home=TERMUX_HOME, shared=SHARED[0], cwd=t["cwd"], max_out=MAX_OUT,
                                   github=GITHUB_WORK.format(repos=repos_text) if repos_text else "")
    msgs = t["messages"]
    msgs.append({"role": "user", "content": text})
    save(t)
    try:
        for _ in range(CONSOLE_ROUNDS):
            raw = await llm(t, system, msgs)
            rep = parse_json(raw)
            msgs.append({"role": "assistant", "content": raw})
            if not rep:
                entry(t, "say", raw.strip()[:2000] or "(no answer)")
                break
            if rep.get("say"):
                entry(t, "say", str(rep["say"])[:2000])
            actions = [a for a in (rep.get("actions") or [])[:5] if isinstance(a, dict)]
            if not actions:
                break
            results = []
            for action in actions:
                action["_cwd"] = t["cwd"]
                if action.get("tool") == "github":
                    action["_repos"], action["_gh_scope"], action["_github_key"] = t.get("repos") or [], t["github_scope"], bool(t["github_key"])
                    if action.get("op") in gh.BRANCH_OPS and gh.REPO_RE.match(str(action.get("repo") or "")) and gh.in_scope(action["repo"], action["_repos"], action["_gh_scope"]):
                        action["_default"] = await github_default(t, action["repo"])
                level, why = classify(action, set(), False)
                for k in ("_cwd", "_repos", "_gh_scope", "_github_key", "_default"):
                    action.pop(k, None)
                label = action_label(action)
                if level == "blocked":
                    entry(t, "blocked", f"{label}: {why}")
                    results.append(f"BLOCKED: {why}")
                    continue
                if level != "read" and not await wait_tap(t, action, why):
                    results.append(f"User DENIED: {sentence(action, why)}")
                    continue
                entry(t, "cmd", label, level=level)
                out, ok = await run_tool(t, 0, action)
                entry(t, "out", trim(out, 2500), ok=ok)
                results.append(f"### {label}\n{trim(out)}")
                save(t)
            msgs.append({"role": "user", "content": "\n\n".join(results)})
        else:
            entry(t, "note", f"stopped after {CONSOLE_ROUNDS} rounds for this message; say 'continue' to go on")
    except BudgetStop as e:
        t["stop_reason"] = str(e)
        entry(t, "stop", f"{e} — Clear the console to start a new session, or raise the default budget in Settings")
    except Stopped:
        entry(t, "stop", "stopped by you")
    except asyncio.CancelledError:
        entry(t, "stop", "stopped by you")
    except Exception as e:
        entry(t, "error", f"{e.__class__.__name__}: {e}")
        log(NAME, "console turn failed", f"{e.__class__.__name__}: {e}")
    finally:
        if len(msgs) > 24:
            del msgs[:len(msgs) - 20]
            msgs.insert(0, {"role": "user", "content": "[earlier console turns trimmed]"})
            msgs.insert(1, {"role": "assistant", "content": "{\"say\":\"ok\"}"})
        t["status"], t["pending"] = "idle", None
        save(t)
        RUN.pop(t["id"], None)


async def on_console(nc, msg, data):
    t = console()
    act = str(data.get("action") or "get")
    if act == "get":
        return await reply(nc, msg, {"ok": True, "console": public(t)})
    if act == "send":
        text = str(data.get("text") or "").strip()[:8000]
        if not text:
            return await reply(nc, msg, {"ok": False, "error": "type something first"})
        if t["status"] != "idle":
            return await reply(nc, msg, {"ok": False, "error": "the console is busy: answer the tap or press Stop"})
        if data.get("cwd"):
            p = resolve(data["cwd"], TERMUX_HOME)
            if not p.is_dir() or is_protected(p) or not in_reach(p):
                return await reply(nc, msg, {"ok": False, "error": f"{p} is not a folder inside the Termux home"})
            t["cwd"] = str(p)
        if "repos" in data:
            try:
                t["repos"] = gh.parse_repos(data.get("repos") or [])
            except ValueError as e:
                return await reply(nc, msg, {"ok": False, "error": str(e)})
        entry(t, "user", text)
        start(t, console_turn(t, text))
        await asyncio.sleep(0.05)
        return await reply(nc, msg, {"ok": True, "console": public(t)})
    if act == "clear":
        if t["status"] != "idle":
            return await reply(nc, msg, {"ok": False, "error": "stop the console first"})
        shutil.rmtree(SNAP / CONSOLE_ID, ignore_errors=True)
        TASKS.pop(CONSOLE_ID, None)
        (DIR / f"{CONSOLE_ID}.json").unlink(missing_ok=True)
        return await reply(nc, msg, {"ok": True, "console": public(console())})
    if act == "task":
        if t["status"] != "idle":
            return await reply(nc, msg, {"ok": False, "error": "stop the console first"})
        text = str(data.get("text") or "").strip()
        if not text:
            users = [e["text"] for e in t["log"] if e["kind"] == "user"]
            text = users[-1] if users else ""
        if len(text) < 3:
            return await reply(nc, msg, {"ok": False, "error": "say what the task is"})
        recent = [f"{e['kind']}: {e['text'][:200]}" for e in t["log"][-16:] if e["kind"] in ("user", "say", "cmd", "summary")]
        task = text + ("\n\nContext from the console:\n" + "\n".join(recent) if recent else "")
        ok, payload = await create_task({"task": task, "repos": t.get("repos") or [], "cwd": t["cwd"]})
        if ok:
            entry(t, "note", f"handed to Operator task {payload['task']['id']}")
            save(t)
        return await reply(nc, msg, {"ok": ok, **payload})
    return await reply(nc, msg, {"ok": False, "error": f"unknown console action {act}"})


# ---------------------------------------------------------------- handlers
def start(t, coro):
    task = asyncio.create_task(coro)
    RUN[t["id"]] = task


async def on_create(nc, msg, data):
    ok, payload = await create_task(data)
    await reply(nc, msg, {"ok": ok, **payload})


async def create_task(data):
    conf = op_settings()
    task = str(data.get("task") or "").strip()
    if len(task) < 3:
        return False, {"error": "describe the task"}
    model = data.get("model") or conf["model"]
    if model not in ("sonnet", "opus", "haiku"):
        return False, {"error": "model must be sonnet, opus or haiku"}
    try:
        budget = round(max(0.05, min(100.0, float(data.get("budget_usd") or conf["default_budget_usd"]))), 2)
    except (TypeError, ValueError):
        return False, {"error": "budget_usd must be a number"}
    cwd = Path(os.path.expanduser(str(data.get("cwd") or conf["cwd"])))
    if not cwd.is_dir() or not in_reach(cwd.resolve()) or is_protected(cwd.resolve()):
        return False, {"error": f"working folder must exist inside the Termux home or shared storage: {cwd}"}
    if len([x for x in TASKS.values() if x["id"] != CONSOLE_ID and x["status"] in ACTIVE | {"needs_you", "paused"}]) >= 2:
        return False, {"error": "two tasks are already active; stop one first"}
    try:
        repos = gh.parse_repos(data.get("repos") or [])
    except ValueError as e:
        return False, {"error": str(e)}
    if repos and not key_present("github"):
        return False, {"error": "no GitHub token stored yet: torus key set github"}
    tid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    uploads = ""
    batch = str(data.get("uploads") or "").strip()
    if batch:
        try:
            src = up.batch_dir(batch)
        except ValueError as e:
            return False, {"error": str(e)}
        if src.is_dir():
            dst = up.UPLOADS / tid
            os.replace(src, dst)
            uploads = str(dst)
    t = {"id": tid, "task": task[:2000], "model": model, "budget_usd": budget, "pause_between": bool(data.get("pause_between")),
         "ask_every_change": bool(data.get("ask_every_change")), "cwd": str(cwd.resolve()), "created": time.time(), "status": "planning",
         "phases": [], "phase": 0, "log": [], "cost": 0.0, "calls": 0, "tokens_in": 0, "tokens_out": 0, "pending": None, "stop_reason": "",
         "repos": repos, "github_scope": conf["github_scope"], "github_key": conf["github_key"],
         "uploads": uploads, "files": up.listing(Path(uploads)) if uploads else []}
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
    return True, {"task": public(t)}


async def on_list(nc, msg, data):
    rows = sorted((x for x in TASKS.values() if x["id"] != CONSOLE_ID), key=lambda x: x["created"], reverse=True)[:60]
    await reply(nc, msg, {"ok": True, "tasks": [{k: x.get(k) for k in ("id", "task", "status", "created", "cost", "model", "phase", "finished")}
                                                | {"phases": len(x["phases"])} for x in rows], "settings": op_settings(),
                          "termux_api": bool(shutil.which("termux-clipboard-get")),
                          "uploads_dir": str(up.UPLOADS), "projects_root": str(up.projects_root())})


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
        if t["id"] == CONSOLE_ID:  # the console keeps going: forget the restored snapshots so the next edits are undoable again
            shutil.rmtree(SNAP / CONSOLE_ID, ignore_errors=True)
            ph.update(snaps={}, changed=[], diffs=[], undone=None)
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
    run_daemon(NAME, {"torus.op.create": on_create, "torus.op.list": on_list, "torus.op.get": on_get, "torus.op.action": on_action,
                      "torus.op.console": on_console},
               concurrent=True, on_start=on_start)
