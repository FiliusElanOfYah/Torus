#!/usr/bin/env python3
"""coderd: works inside your Termux projects (default ~/projects, set with `torus code root <dir>`).

Request/reply subjects (all reply {ok, ...} or {ok: false, error}):
  torus.code.projects  {}                          list projects
  torus.code.create    {name}                      make a new project folder
  torus.code.tree      {project}                   files (skips .git, node_modules, venvs...)
  torus.code.read      {project, paths[]}          text contents
  torus.code.write     {project, task_id, files[{path, content|null}]}   whole files; null = delete
  torus.code.rollback  {project, task_id}          undo everything a task wrote
  torus.code.run       {project, cmd, timeout?}    run YOUR command in the project folder

Safety rules: paths can never leave the project folder; every overwritten or deleted
file is backed up to ~/.torus/backups/coder/<task>/ first; only commands you typed
are run (Claude can only propose file contents); runs are time-limited and logged
to ~/.torus/logs/coderd.jsonl.
"""
import asyncio
import json
import os
import re
import shutil
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import ETC, HOME, LOGS, append_jsonl, log, pack, read_conf, run_daemon  # noqa: E402

NAME = "coderd"
CONF = ETC / "coder.conf"
BACKUPS = HOME / "backups" / "coder"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", "build",
             "dist", ".gradle", ".idea", ".cache", ".next", ".expo", "target"}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_FILE = 300_000
MAX_READ_TOTAL = 600_000
MAX_OUTPUT = 60_000


def settings():
    c = read_conf(CONF)
    root = Path(os.path.expanduser(c.get("root") or str(Path.home() / "projects")))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(), int(c.get("run_timeout") or 120)


def project_dir(name):
    root, _ = settings()
    if not name or not NAME_RE.match(name):
        raise ValueError("project names use letters, numbers, dot, dash, underscore")
    d = (root / name).resolve()
    if d.parent != root or not d.is_dir():
        raise ValueError(f"no project called {name} in {root}")
    return d


def safe_path(pdir, rel):
    rel = (rel or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError(f"unsafe path: {rel!r}")
    p = (pdir / rel).resolve()
    if pdir not in p.parents:
        raise ValueError(f"path leaves the project: {rel!r}")
    if ".git" in p.relative_to(pdir).parts:
        raise ValueError("TORUS does not touch .git")
    return p


def replying(fn):
    async def handler(nc, msg, data):
        try:
            out = await fn(data)
            out.setdefault("ok", True)
        except Exception as e:
            out = {"ok": False, "error": f"{e.__class__.__name__}: {e}" if not isinstance(e, ValueError) else str(e)}
        if msg.reply:
            await nc.publish(msg.reply, pack(out))
    return handler


def walk(pdir, limit=3000):
    out = []
    for dirpath, dirnames, filenames in os.walk(pdir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for f in sorted(filenames):
            p = Path(dirpath) / f
            try:
                out.append({"path": str(p.relative_to(pdir)), "size": p.stat().st_size})
            except OSError:
                continue
            if len(out) >= limit:
                return out
    return out


async def projects(data):
    root, _ = settings()
    rows = []
    for d in sorted(root.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and NAME_RE.match(d.name):
            rows.append({"name": d.name, "files": len(walk(d, 2000)), "modified": d.stat().st_mtime})
    return {"root": str(root), "projects": rows}


async def create(data):
    root, _ = settings()
    name = (data.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ValueError("project names use letters, numbers, dot, dash, underscore")
    (root / name).mkdir(exist_ok=True)
    return {"name": name, "path": str(root / name)}


async def tree(data):
    return {"files": walk(project_dir(data.get("project")))}


async def read(data):
    pdir = project_dir(data.get("project"))
    files, total = {}, 0
    for rel in data.get("paths") or []:
        try:
            p = safe_path(pdir, rel)
            if not p.is_file() or p.stat().st_size > MAX_FILE:
                files[rel] = None
                continue
            raw = p.read_bytes()
            if b"\0" in raw[:4096] or total + len(raw) > MAX_READ_TOTAL:
                files[rel] = None
                continue
            files[rel] = raw.decode("utf-8", "replace")
            total += len(raw)
        except ValueError:
            files[rel] = None
    return {"files": files}


def manifest_path(task_id):
    if not re.fullmatch(r"[0-9A-Za-z-]{1,64}", task_id or ""):
        raise ValueError("bad task id")
    return BACKUPS / task_id / "manifest.json"


async def write(data):
    pdir = project_dir(data.get("project"))
    mpath = manifest_path(data.get("task_id"))
    man = json.loads(mpath.read_text()) if mpath.exists() else {"project": pdir.name, "backed": [], "created": []}
    written, deleted = [], []
    for f in data.get("files") or []:
        rel, content = f.get("path"), f.get("content")
        p = safe_path(pdir, rel)
        rel = str(p.relative_to(pdir))
        if p.exists() and rel not in man["backed"] and rel not in man["created"]:
            dst = mpath.parent / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            man["backed"].append(rel)
        elif not p.exists() and content is not None and rel not in man["backed"] and rel not in man["created"]:
            man["created"].append(rel)
        if content is None:
            if p.exists():
                p.unlink()
                deleted.append(rel)
            continue
        if len(content) > MAX_FILE:
            raise ValueError(f"{rel} is too big to write")
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = p.stat().st_mode & 0o777 if p.exists() else (0o755 if content.startswith("#!") else 0o644)
        tmp = p.with_name(p.name + ".torus-tmp")
        tmp.write_text(content, encoding="utf-8")
        os.chmod(tmp, mode)
        os.replace(tmp, p)
        written.append(rel)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(man, indent=1))
    log(NAME, "write", pdir.name, f"{len(written)} written, {len(deleted)} deleted")
    return {"written": written, "deleted": deleted, "backup": str(mpath.parent)}


async def rollback(data):
    pdir = project_dir(data.get("project"))
    mpath = manifest_path(data.get("task_id"))
    if not mpath.exists():
        raise ValueError("this task has not written anything")
    man = json.loads(mpath.read_text())
    restored, removed = [], []
    for rel in man["backed"]:
        shutil.copy2(mpath.parent / "files" / rel, safe_path(pdir, rel))
        restored.append(rel)
    for rel in man["created"]:
        p = safe_path(pdir, rel)
        if p.exists():
            p.unlink()
            removed.append(rel)
    mpath.rename(mpath.with_name(f"manifest.rolled-back-{int(time.time())}.json"))
    log(NAME, "rollback", pdir.name, data.get("task_id"))
    return {"restored": restored, "removed": removed}


def run_env():
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    env = {"HOME": str(Path.home()), "PREFIX": prefix, "PATH": os.environ.get("PATH", f"{prefix}/bin"),
           "TMPDIR": os.environ.get("TMPDIR", f"{prefix}/tmp"), "LANG": "en_US.UTF-8", "TERM": "dumb",
           "PYTHONUNBUFFERED": "1"}
    for lib in ("libtermux-exec-ld-preload.so", "libtermux-exec.so"):  # lets #!/usr/bin/env scripts run
        if Path(prefix, "lib", lib).exists():
            env["LD_PRELOAD"] = f"{prefix}/lib/{lib}"
            break
    return env


async def run(data):
    pdir = project_dir(data.get("project"))
    cmd = (data.get("cmd") or "").strip()
    if not cmd:
        raise ValueError("no command given")
    _, default_t = settings()
    timeout = max(5, min(int(data.get("timeout") or default_t), 1800))
    shell = shutil.which("bash") or "/bin/sh"
    t0 = time.monotonic()
    p = await asyncio.create_subprocess_exec(shell, "-c", cmd, cwd=str(pdir), env=run_env(),
                                             stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    timed_out = False
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = await p.communicate()
    text = out.decode("utf-8", "replace")
    if len(text) > MAX_OUTPUT:
        text = text[:3000] + "\n\n... [output cut] ...\n\n" + text[-(MAX_OUTPUT - 3000):]
    dur = round(time.monotonic() - t0, 2)
    append_jsonl(LOGS / "coderd.jsonl", {"t": time.time(), "project": pdir.name, "cmd": cmd[:300],
                                        "exit": p.returncode, "timed_out": timed_out, "secs": dur})
    log(NAME, "run", pdir.name, repr(cmd[:80]), "exit", p.returncode, "(timeout)" if timed_out else "")
    return {"exit": p.returncode, "timed_out": timed_out, "output": text, "duration": dur, "cmd": cmd}


if __name__ == "__main__":
    run_daemon(NAME, {
        "torus.code.projects": replying(projects), "torus.code.create": replying(create),
        "torus.code.tree": replying(tree), "torus.code.read": replying(read),
        "torus.code.write": replying(write), "torus.code.rollback": replying(rollback),
        "torus.code.run": replying(run),
    }, concurrent=True)
