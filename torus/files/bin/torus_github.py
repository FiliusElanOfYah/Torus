"""torus_github - GitHub for the Operator (phase 10).

The token lives in ~/.torus/airgap/github.key (torus key set github; a fine-grained PAT). operatord asks airgapd for it
per call and keeps it in memory only; it is never written into a clone, a log or a dossier.

Read side (level "read", no tap): repos, tree, read (<= 1 MB), search, commits, issues, prs, pr_diff.
Change side: clone (tarball download, so the clone holds no token).
Scope (etc/operator.conf github_scope=task|all): "task" = only the repos typed into the task; "all" = every repo the
token can see. Out-of-scope repos are blocked before any network call.
"""
import base64
import io
import os
import re
import tarfile
import time
from pathlib import Path

import httpx

from torus_common import ETC, USER_AGENT, read_conf

API = os.environ.get("TORUS_GITHUB_API", "https://api.github.com").rstrip("/")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
REF_RE = re.compile(r"^[A-Za-z0-9_./-]{0,200}$")
MAX_FILE = 1_000_000
MAX_TARBALL = 200 * 1024 * 1024
READ_OPS = ("repos", "tree", "read", "search", "commits", "issues", "prs", "pr_diff")
CHANGE_OPS = ("clone",)
OPS = READ_OPS + CHANGE_OPS
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}


class GitHubError(Exception):
    pass


def scope():
    return "all" if (read_conf(ETC / "operator.conf").get("github_scope") or "task").strip().lower() == "all" else "task"


def parse_repos(value):
    """'owner/a, owner/b' or a list -> ['owner/a', 'owner/b'] (lower-cased, deduped, max 10). Raises ValueError."""
    items = value if isinstance(value, list) else re.split(r"[,\s]+", str(value or ""))
    out = []
    for r in items:
        r = str(r).strip().strip("/")
        r = re.sub(r"^https?://github\.com/", "", r)
        r = re.sub(r"\.git$", "", r)
        if not r:
            continue
        if not REPO_RE.match(r):
            raise ValueError(f"repo must look like owner/name: {r!r}")
        if r.lower() not in [x.lower() for x in out]:
            out.append(r)
    if len(out) > 10:
        raise ValueError("at most 10 repos per task")
    return out


def in_scope(repo, allowed, mode):
    if mode == "all":
        return True
    return bool(repo) and repo.lower() in [a.lower() for a in (allowed or [])]


def _ref(a):
    ref = str(a.get("ref") or "").strip()
    if not REF_RE.match(ref) or ".." in ref:
        raise GitHubError("ref may only contain letters, digits, . _ / -")
    return ref


def _n(a, default=10, cap=50):
    try:
        return max(1, min(cap, int(a.get("n") or default)))
    except (TypeError, ValueError):
        return default


async def call(client, token, method, path, params=None, accept=None, stream_to=None):
    headers = {**HEADERS, "Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    url = path if path.startswith("http") else API + path
    try:
        if stream_to is not None:
            async with client.stream(method, url, params=params, headers=headers, follow_redirects=True) as r:
                if r.status_code >= 400:
                    await r.aread()
                    _raise(r, path)
                got = 0
                async for chunk in r.aiter_bytes(1 << 16):
                    got += len(chunk)
                    if got > MAX_TARBALL:
                        raise GitHubError("tarball is over 200 MB; clone a smaller ref or use git in a shell")
                    stream_to.write(chunk)
                return r
        r = await client.request(method, url, params=params, headers=headers, follow_redirects=True)
    except httpx.HTTPError as e:
        raise GitHubError(f"GitHub did not answer ({e.__class__.__name__}): check the connection") from None
    if r.status_code >= 400:
        _raise(r, path)
    return r


def _raise(r, path):
    try:
        msg = r.json().get("message", "")
    except ValueError:
        msg = ""
    if r.status_code == 401:
        raise GitHubError("GitHub rejected the token (401): torus key set github   (fine-grained PAT, Contents: read)")
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        wait = max(0, int(r.headers.get("x-ratelimit-reset") or 0) - int(time.time()))
        raise GitHubError(f"GitHub rate limit hit; try again in {wait // 60 + 1} min")
    if r.status_code in (403, 404):
        raise GitHubError(f"GitHub says {r.status_code} for {path}: not found, or the token has no access to it ({msg})")
    if r.status_code == 422:
        raise GitHubError(f"GitHub refused the request (422): {msg}")
    raise GitHubError(f"GitHub error {r.status_code} for {path}: {msg}")


def _footer(r):
    try:
        left = int(r.headers.get("x-ratelimit-remaining"))
    except (TypeError, ValueError):
        return ""
    return f"\n[GitHub: {left} API calls left this hour]" if left < 100 else ""


def _repo(a, allowed, mode):
    repo = str(a.get("repo") or "").strip()
    if not REPO_RE.match(repo):
        raise GitHubError("repo must look like owner/name")
    if not in_scope(repo, allowed, mode):
        raise GitHubError(f"{repo} is not in this task's repo list (add it to the task, or Settings → GitHub scope: all)")
    return repo


async def run(op, a, token, allowed=None, mode="task", client=None):
    """Execute one github action -> text for Claude. Raises GitHubError with a one-line reason."""
    if op not in OPS:
        raise GitHubError(f"unknown GitHub op {op}")
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))
    try:
        return await _run(client, op, a, token, allowed or [], mode)
    finally:
        if own:
            await client.aclose()


async def _run(client, op, a, token, allowed, mode):
    if op == "repos":
        if mode == "task":
            rows = []
            for repo in allowed[:10]:
                r = await call(client, token, "GET", f"/repos/{repo}")
                d = r.json()
                rows.append(f"{d['full_name']}  default:{d.get('default_branch')}  {'private' if d.get('private') else 'public'}  "
                            f"{d.get('language') or '-'}  updated {str(d.get('pushed_at') or '')[:10]}  {(d.get('description') or '')[:80]}")
            return ("\n".join(rows) or "no repos were given to this task") + (_footer(r) if allowed else "")
        r = await call(client, token, "GET", "/user/repos", params={"per_page": 50, "sort": "pushed", "affiliation": "owner,collaborator,organization_member"})
        rows = [f"{d['full_name']}  default:{d.get('default_branch')}  {'private' if d.get('private') else 'public'}  "
                f"{d.get('language') or '-'}  updated {str(d.get('pushed_at') or '')[:10]}" for d in r.json()]
        return ("\n".join(rows) or "the token sees no repos") + _footer(r)

    if op == "search":
        q = str(a.get("q") or "").strip()
        if not q:
            raise GitHubError("search needs q")
        repo = str(a.get("repo") or "").strip()
        if repo:
            repo = _repo(a, allowed, mode)
            q = f"{q} repo:{repo}"
        elif mode == "task":
            if not allowed:
                raise GitHubError("search needs a repo (this task has none)")
            q = q + " " + " ".join(f"repo:{x}" for x in allowed)
        r = await call(client, token, "GET", "/search/code", params={"q": q, "per_page": _n(a, 20, 30)})
        d = r.json()
        rows = [f"{it['repository']['full_name']}: {it['path']}" for it in d.get("items", [])]
        return (f"{d.get('total_count', 0)} hit(s) for {q!r} (default branches only)\n" + "\n".join(rows)) + _footer(r)

    repo = _repo(a, allowed, mode)
    ref = _ref(a)
    params = {"ref": ref} if ref else None

    if op in ("tree", "read"):
        path = str(a.get("path") or "").strip().strip("/")
        if ".." in path.split("/"):
            raise GitHubError("path may not contain ..")
        r = await call(client, token, "GET", f"/repos/{repo}/contents/{path}", params=params)
        d = r.json()
        if isinstance(d, list):
            if op == "read":
                return f"{path or '/'} is a folder; use tree, then read a file" + _footer(r)
            rows = [f"{'d' if it['type'] == 'dir' else '-'} {it.get('size') or 0:>10} {it['name']}" for it in d[:400]]
            return f"{repo}/{path or ''}{(' @ ' + ref) if ref else ''}\n" + ("\n".join(rows) or "(empty)") + _footer(r)
        if d.get("type") == "symlink" or d.get("type") == "submodule":
            return f"{path} is a {d['type']} -> {d.get('target') or d.get('submodule_git_url')}" + _footer(r)
        if op == "tree":
            return f"- {d.get('size') or 0:>10} {d['name']}   (a file; use read)" + _footer(r)
        size = int(d.get("size") or 0)
        if size > MAX_FILE:
            return f"{path} is {size} bytes (over 1 MB); search inside it or clone the repo and use shell head/grep" + _footer(r)
        if d.get("encoding") == "base64" and d.get("content"):
            raw = base64.b64decode(d["content"])
        else:
            r2 = await call(client, token, "GET", f"/repos/{repo}/contents/{path}", params=params, accept="application/vnd.github.raw+json")
            raw = r2.content
        if b"\0" in raw[:8000]:
            return f"{path} is binary ({size} bytes)" + _footer(r)
        return raw.decode("utf-8", "replace") + _footer(r)

    if op == "commits":
        p = {"per_page": _n(a)}
        if ref:
            p["sha"] = ref
        path = str(a.get("path") or "").strip().strip("/")
        if path:
            p["path"] = path
        r = await call(client, token, "GET", f"/repos/{repo}/commits", params=p)
        rows = []
        for c in r.json():
            cm = c.get("commit") or {}
            who = (cm.get("author") or {}).get("name") or (c.get("author") or {}).get("login") or "?"
            rows.append(f"{c['sha'][:7]}  {str((cm.get('author') or {}).get('date') or '')[:10]}  {who}: {(cm.get('message') or '').splitlines()[0][:100]}")
        return ("\n".join(rows) or "no commits") + _footer(r)

    if op in ("issues", "prs"):
        state = str(a.get("state") or "open")
        if state not in ("open", "closed", "all"):
            raise GitHubError("state must be open, closed or all")
        kind = "issues" if op == "issues" else "pulls"
        r = await call(client, token, "GET", f"/repos/{repo}/{kind}", params={"state": state, "per_page": _n(a, 20, 50), "sort": "updated"})
        rows = []
        for it in r.json():
            if op == "issues" and "pull_request" in it:
                continue
            who = (it.get("user") or {}).get("login") or "?"
            extra = (" [" + ", ".join(lb["name"] for lb in it.get("labels") or []) + "]") if it.get("labels") else ""
            if op == "prs":
                extra = f"  {(it.get('head') or {}).get('ref')} -> {(it.get('base') or {}).get('ref')}" + (" draft" if it.get("draft") else "")
            rows.append(f"#{it['number']} [{it['state']}] {it['title'][:100]}  ({who}){extra}  updated {str(it.get('updated_at') or '')[:10]}")
        return ("\n".join(rows) or f"no {state} {op}") + _footer(r)

    if op == "pr_diff":
        num = _n(a, 0, 10 ** 9)
        if not num:
            raise GitHubError("pr_diff needs n (the PR number)")
        r = await call(client, token, "GET", f"/repos/{repo}/pulls/{num}")
        pr = r.json()
        r2 = await call(client, token, "GET", f"/repos/{repo}/pulls/{num}", accept="application/vnd.github.diff")
        head = (f"#{num} {pr.get('title')} [{pr.get('state')}] {(pr.get('head') or {}).get('ref')} -> {(pr.get('base') or {}).get('ref')} "
                f"by {(pr.get('user') or {}).get('login')} · +{pr.get('additions', 0)} -{pr.get('deletions', 0)} in {pr.get('changed_files', 0)} file(s)\n\n")
        return head + r2.text + _footer(r2)

    if op == "clone":
        dest = Path(str(a.get("_dest") or "")).resolve()
        if dest.exists() and any(dest.iterdir()):
            raise GitHubError(f"{dest} exists and is not empty")
        tmp = io.BytesIO()
        r = await call(client, token, "GET", f"/repos/{repo}/tarball/{ref}", stream_to=tmp)
        tmp.seek(0)
        n = extract_tarball(tmp, dest)
        return f"cloned {repo}{(' @ ' + ref) if ref else ''}: {n} file(s) into {dest} (snapshot, no .git; no token inside)" + _footer(r)
    raise GitHubError(f"unknown GitHub op {op}")


def extract_tarball(fileobj, dest):
    """Extract GitHub's tarball (one top folder) into dest, dropping the top folder; refuses links and escapes."""
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    with tarfile.open(fileobj=fileobj, mode="r:gz") as tf:
        for m in tf:
            parts = Path(m.name).parts
            if len(parts) < 2:
                continue
            rel = Path(*parts[1:])
            if m.issym() or m.islnk() or m.isdev() or ".." in rel.parts or rel.is_absolute():
                continue
            target = (dest / rel)
            if not str(target.resolve()).startswith(str(dest.resolve()) + "/"):
                continue
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not m.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with open(target, "wb") as out:
                while True:
                    chunk = src.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
            os.chmod(target, 0o755 if m.mode & 0o100 else 0o644)
            n += 1
    return n
