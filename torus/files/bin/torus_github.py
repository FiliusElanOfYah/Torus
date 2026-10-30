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
CHANGE_OPS = ("clone", "branch", "commit", "pr", "comment", "push", "revert", "pr_close")
DANGER_OPS = ("merge", "delete_branch")
BRANCH_OPS = ("commit", "push", "delete_branch")  # ops whose target branch decides read/change/danger
OPS = READ_OPS + CHANGE_OPS + DANGER_OPS
BRANCH_RE = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9_./-]{1,200}(?<!\.lock)(?<!/)$")
MAX_COMMIT_FILES = 40
MAX_COMMIT_BYTES = 5 * 1024 * 1024
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


async def call(client, token, method, path, params=None, accept=None, stream_to=None, json_body=None):
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
        r = await client.request(method, url, params=params, headers=headers, follow_redirects=True, json=json_body)
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
    if r.status_code == 403 and "not accessible" in msg.lower():
        raise GitHubError("the GitHub token lacks a permission for this (edit the fine-grained PAT on github.com: "
                          "Contents: write for commits/branches, Pull requests: write for PRs, Issues: write for comments)")
    if r.status_code in (403, 404):
        raise GitHubError(f"GitHub says {r.status_code} for {path}: not found, or the token has no access to it ({msg})")
    if r.status_code == 409:
        raise GitHubError(f"GitHub conflict (409): {msg}")
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

    # ------------------------------------------------------------ write side (phase 3)
    if op == "branch":
        new, src = _branch(a.get("branch")), str(a.get("from") or "").strip()
        if not src:
            src = await default_branch(client, token, repo)
        sha = (await call(client, token, "GET", f"/repos/{repo}/git/ref/heads/{_branch(src)}")).json()["object"]["sha"]
        r = await call(client, token, "POST", f"/repos/{repo}/git/refs", json_body={"ref": f"refs/heads/{new}", "sha": sha})
        return f"branch {new} created from {src} @ {sha[:7]}  https://github.com/{repo}/tree/{new}" + _footer(r)

    if op == "commit":
        branch = _branch(a.get("branch"))
        message = str(a.get("message") or "").strip()
        files = a.get("files") or []
        if not message:
            raise GitHubError("commit needs a message")
        if not isinstance(files, list) or not files:
            raise GitHubError("commit needs files: [{path, content} | {path, delete: true}]")
        if len(files) > MAX_COMMIT_FILES:
            raise GitHubError(f"at most {MAX_COMMIT_FILES} files per commit")
        head = (await call(client, token, "GET", f"/repos/{repo}/git/ref/heads/{branch}")).json()["object"]["sha"]
        base_tree = (await call(client, token, "GET", f"/repos/{repo}/git/commits/{head}")).json()["tree"]["sha"]
        tree, total, names = [], 0, []
        for f in files:
            path = str(f.get("path") or "").strip().strip("/")
            if not path or ".." in path.split("/"):
                raise GitHubError(f"bad file path in commit: {path!r}")
            if f.get("delete"):
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                names.append(f"- {path}")
                continue
            content = str(f.get("content") if f.get("content") is not None else "")
            total += len(content.encode())
            if total > MAX_COMMIT_BYTES:
                raise GitHubError("commit is over 5 MB of text; split it")
            blob = (await call(client, token, "POST", f"/repos/{repo}/git/blobs", json_body={"content": content, "encoding": "utf-8"})).json()["sha"]
            tree.append({"path": path, "mode": "100755" if f.get("executable") else "100644", "type": "blob", "sha": blob})
            names.append(f"± {path}")
        tree_sha = (await call(client, token, "POST", f"/repos/{repo}/git/trees", json_body={"base_tree": base_tree, "tree": tree})).json()["sha"]
        commit = (await call(client, token, "POST", f"/repos/{repo}/git/commits",
                             json_body={"message": message, "tree": tree_sha, "parents": [head]})).json()
        r = await call(client, token, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}", json_body={"sha": commit["sha"], "force": False})
        return (f"commit {commit['sha'][:7]} on {branch}: {message.splitlines()[0][:80]}\n" + "\n".join(names)
                + f"\nhttps://github.com/{repo}/commit/{commit['sha']}" + _footer(r))

    if op == "pr":
        title = str(a.get("title") or "").strip()
        head_b, base = _branch(a.get("head") or a.get("branch")), str(a.get("base") or "").strip()
        if not title:
            raise GitHubError("pr needs a title")
        if not base:
            base = await default_branch(client, token, repo)
        r = await call(client, token, "POST", f"/repos/{repo}/pulls",
                       json_body={"title": title[:250], "head": head_b, "base": _branch(base), "body": str(a.get("body") or "")[:60000], "draft": bool(a.get("draft"))})
        d = r.json()
        return f"PR #{d['number']} opened: {d['title']}  {head_b} -> {base}  {d['html_url']}" + _footer(r)

    if op == "comment":
        num, body_ = _n(a, 0, 10 ** 9), str(a.get("body") or "").strip()
        if not num or not body_:
            raise GitHubError("comment needs n (issue/PR number) and body")
        r = await call(client, token, "POST", f"/repos/{repo}/issues/{num}/comments", json_body={"body": body_[:60000]})
        return f"comment added on #{num}  {r.json().get('html_url', '')}" + _footer(r)

    if op == "pr_close":
        num = _n(a, 0, 10 ** 9)
        if not num:
            raise GitHubError("pr_close needs n")
        r = await call(client, token, "PATCH", f"/repos/{repo}/pulls/{num}", json_body={"state": "closed"})
        return f"PR #{num} closed  {r.json().get('html_url', '')}" + _footer(r)

    if op == "merge":
        num = _n(a, 0, 10 ** 9)
        if not num:
            raise GitHubError("merge needs n")
        method = str(a.get("method") or "squash")
        if method not in ("merge", "squash", "rebase"):
            raise GitHubError("method must be merge, squash or rebase")
        r = await call(client, token, "PUT", f"/repos/{repo}/pulls/{num}/merge", json_body={"merge_method": method})
        d = r.json()
        return f"PR #{num} merged ({method}): {d.get('sha', '')[:7]} {d.get('message', '')}" + _footer(r)

    if op == "delete_branch":
        branch = _branch(a.get("branch"))
        if branch == await default_branch(client, token, repo):
            raise GitHubError(f"{branch} is the default branch; it stays")
        r = await call(client, token, "DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
        return f"branch {branch} deleted" + _footer(r)

    if op == "revert":
        branch = _branch(a.get("branch"))
        head = (await call(client, token, "GET", f"/repos/{repo}/git/ref/heads/{branch}")).json()["object"]["sha"]
        c = (await call(client, token, "GET", f"/repos/{repo}/git/commits/{head}")).json()
        want = str(a.get("sha") or "").strip()
        if want and not head.startswith(want):
            raise GitHubError(f"revert only undoes the latest commit on {branch} ({head[:7]}); {want[:7]} is older — ask for a new commit instead")
        if len(c.get("parents") or []) != 1:
            raise GitHubError("cannot revert a merge or root commit here")
        parent = (await call(client, token, "GET", f"/repos/{repo}/git/commits/{c['parents'][0]['sha']}")).json()
        commit = (await call(client, token, "POST", f"/repos/{repo}/git/commits",
                             json_body={"message": f"Revert \"{c['message'].splitlines()[0][:70]}\"\n\nThis reverts commit {head}.",
                                        "tree": parent["tree"]["sha"], "parents": [head]})).json()
        r = await call(client, token, "PATCH", f"/repos/{repo}/git/refs/heads/{branch}", json_body={"sha": commit["sha"], "force": False})
        return f"reverted {head[:7]} on {branch} with {commit['sha'][:7]}  https://github.com/{repo}/commit/{commit['sha']}" + _footer(r)

    if op == "push":
        raise GitHubError("push runs locally (operatord/torusctl handle it)")
    raise GitHubError(f"unknown GitHub op {op}")


def _branch(name):
    name = str(name or "").strip()
    if not BRANCH_RE.match(name):
        raise GitHubError(f"bad branch name {name!r}")
    return name


async def default_branch(client, token, repo):
    return (await call(client, token, "GET", f"/repos/{repo}")).json().get("default_branch") or "main"


def push_command(a, askpass):
    """argv + env for `git push` from a local clone, token supplied by the askpass helper (never on disk)."""
    remote, branch = str(a.get("remote") or "origin").strip(), str(a.get("branch") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", remote) or (branch and not BRANCH_RE.match(branch)):
        raise GitHubError("push: bad remote or branch name")
    argv = ["git", "push"]
    if a.get("force"):
        argv.append("--force-with-lease")
    if a.get("set_upstream") or a.get("u"):
        argv.append("-u")
    argv.append(remote)
    if branch:
        argv.append(f"HEAD:{branch}" if a.get("head") else branch)
    env = {"GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0", "TORUS_ASKPASS_KEY": "github"}
    return argv, env


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
