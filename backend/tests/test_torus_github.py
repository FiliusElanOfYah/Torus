"""Phase 2a: GitHub read for the Operator. torus_github against a local fake GitHub API (repos, contents, search, commits,
issues, PRs, diff, tarball clone), repo scope (task vs all), operatord classification of the github tool, a scripted task
that reads a repo through the tool, gatewayd settings github_scope, CLI wiring, audit extra-6, manifest."""
import asyncio
import base64
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-gh-test")
WORK = HOME / "work"
os.environ["TORUS_HOME"] = str(HOME)
os.environ["TORUS_GITHUB_API"] = "http://127.0.0.1:8097"
sys.path.insert(0, str(BIN))
import operatord as op  # noqa: E402
import torus_github as gh  # noqa: E402

TOKEN = "github_pat_TEST"
FILES = {"README.md": b"# demo\nhello torus\n", "src/app.py": b"print('hi')\n", "bin/big.bin": b"\0" * 100}


def tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in FILES.items():
            ti = tarfile.TarInfo("owner-demo-abc123/" + name)
            ti.size = len(data)
            ti.mode = 0o755 if name.startswith("bin/") else 0o644
            tf.addfile(ti, io.BytesIO(data))
        bad = tarfile.TarInfo("owner-demo-abc123/../escape.txt")
        bad.size = 1
        tf.addfile(bad, io.BytesIO(b"x"))
        ln = tarfile.TarInfo("owner-demo-abc123/link")
        ln.type = tarfile.SYMTYPE
        ln.linkname = "/etc/passwd"
        tf.addfile(ln)
    return buf.getvalue()


class FakeGitHub(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-RateLimit-Remaining", "42")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        FakeGitHub.calls.append((u.path, dict(q), self.headers.get("Accept", "")))
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.send(401, {"message": "Bad credentials"})
        p = u.path
        if p == "/user/repos":
            return self.send(200, [{"full_name": "owner/demo", "default_branch": "main", "private": True, "language": "Python", "pushed_at": "2026-06-01T00:00:00Z"},
                                   {"full_name": "owner/other", "default_branch": "main", "private": False, "language": None, "pushed_at": "2026-05-01T00:00:00Z"}])
        if p == "/repos/owner/demo":
            return self.send(200, {"full_name": "owner/demo", "default_branch": "main", "private": True, "language": "Python", "pushed_at": "2026-06-01T00:00:00Z", "description": "demo repo"})
        if p == "/repos/owner/secret":
            return self.send(404, {"message": "Not Found"})
        if p.startswith("/repos/owner/demo/contents"):
            rel = p[len("/repos/owner/demo/contents"):].strip("/")
            if rel == "":
                return self.send(200, [{"type": "file", "name": "README.md", "size": 20}, {"type": "dir", "name": "src", "size": 0}])
            if rel in FILES:
                data = FILES[rel]
                if "raw" in self.headers.get("Accept", ""):
                    return self.send(200, data, "application/vnd.github.raw")
                return self.send(200, {"type": "file", "name": rel.split("/")[-1], "size": len(data), "encoding": "base64",
                                       "content": base64.b64encode(data).decode()})
            if rel == "huge.txt":
                return self.send(200, {"type": "file", "name": "huge.txt", "size": 5_000_000, "encoding": "none", "content": ""})
            return self.send(404, {"message": "Not Found"})
        if p == "/search/code":
            return self.send(200, {"total_count": 1, "items": [{"path": "src/app.py", "repository": {"full_name": "owner/demo"}}]})
        if p == "/repos/owner/demo/commits":
            return self.send(200, [{"sha": "abcdef1234567", "commit": {"author": {"name": "Ann", "date": "2026-06-02T10:00:00Z"}, "message": "fix retry\n\nmore"}}])
        if p == "/repos/owner/demo/issues":
            return self.send(200, [{"number": 7, "state": "open", "title": "bug", "user": {"login": "bob"}, "labels": [{"name": "p1"}], "updated_at": "2026-06-02"},
                                   {"number": 8, "state": "open", "title": "a PR", "user": {"login": "bob"}, "pull_request": {}, "updated_at": "2026-06-02"}])
        if p == "/repos/owner/demo/pulls":
            return self.send(200, [{"number": 8, "state": "open", "title": "a PR", "user": {"login": "bob"}, "head": {"ref": "fix"}, "base": {"ref": "main"}, "updated_at": "2026-06-02"}])
        if p == "/repos/owner/demo/pulls/8":
            if "diff" in self.headers.get("Accept", ""):
                return self.send(200, b"diff --git a/x b/x\n+line\n", "application/vnd.github.diff")
            return self.send(200, {"title": "a PR", "state": "open", "head": {"ref": "fix"}, "base": {"ref": "main"}, "user": {"login": "bob"}, "additions": 1, "deletions": 0, "changed_files": 1})
        if p.startswith("/repos/owner/demo/tarball"):
            return self.send(200, tarball(), "application/x-gzip")
        if p == "/repos/owner/limited":
            return self.send(403, {"message": "API rate limit exceeded"}, extra={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"})
        return self.send(404, {"message": "Not Found"})


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 8097), FakeGitHub)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for sub in ("etc", "logs", "var/operator/snapshots", "vault/answers", "airgap", "work"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    for f in WORK.glob("*"):
        if f.is_file():
            f.unlink()
    for f in (HOME / "var" / "operator").glob("*.json"):
        f.unlink()
    monkeypatch.setattr(op, "CONF", HOME / "etc" / "operator.conf")
    monkeypatch.setattr(op, "DIR", HOME / "var" / "operator")
    monkeypatch.setattr(op, "SNAP", HOME / "var" / "operator" / "snapshots")
    monkeypatch.setattr(op, "ANSWERS", HOME / "vault" / "answers")
    monkeypatch.setattr(op, "TERMUX_HOME", HOME)
    monkeypatch.setattr(op, "PROTECTED", [HOME / "airgap", HOME / "etc" / "gateway.token", HOME / "var" / "operator"])
    monkeypatch.setattr(op, "SHARED", [HOME / "shared"])
    (HOME / "etc" / "operator.conf").write_text(f"ask_every_change=false\ndefault_budget_usd=1\nmodel=sonnet\nmax_steps=20\ncwd={WORK}\n")
    (HOME / "airgap" / "github.key").write_text(TOKEN + "\n")
    op.TASKS.clear(); op.EVENTS.clear(); op.RUN.clear()
    FakeGitHub.calls.clear()
    yield


def run(op_, allowed=("owner/demo",), mode="task", **a):
    return asyncio.run(gh.run(op_, a, TOKEN, list(allowed), mode))


# ------------------------------------------------------------ torus_github
def test_parse_repos_and_scope():
    assert gh.parse_repos("owner/demo, https://github.com/owner/other.git owner/demo") == ["owner/demo", "owner/other"]
    assert gh.parse_repos([]) == []
    with pytest.raises(ValueError):
        gh.parse_repos("not a repo")
    with pytest.raises(ValueError):
        gh.parse_repos(",".join(f"o/r{i}" for i in range(11)))
    assert gh.in_scope("Owner/Demo", ["owner/demo"], "task")
    assert not gh.in_scope("owner/other", ["owner/demo"], "task")
    assert gh.in_scope("anyone/anything", [], "all")
    assert gh.scope() == "task"
    (HOME / "etc" / "operator.conf").write_text("github_scope=all\n")
    assert gh.scope() == "all"


def test_repos_tree_read():
    out = run("repos")
    assert "owner/demo" in out and "private" in out and "demo repo" in out
    out = run("repos", mode="all")
    assert "owner/other" in out
    out = run("tree", repo="owner/demo")
    assert "d          0 src" in out and "README.md" in out
    out = run("read", repo="owner/demo", path="src/app.py")
    assert out.startswith("print('hi')")
    assert "[GitHub: 42 API calls left" in out
    assert "binary" in run("read", repo="owner/demo", path="bin/big.bin")
    assert "over 1 MB" in run("read", repo="owner/demo", path="huge.txt")
    assert "is a folder" in run("read", repo="owner/demo", path="")
    with pytest.raises(gh.GitHubError, match="not found, or the token"):
        run("read", repo="owner/demo", path="nope.txt")
    with pytest.raises(gh.GitHubError, match="\\.\\."):
        run("read", repo="owner/demo", path="../x")


def test_scope_blocks_before_network_and_errors_are_friendly():
    FakeGitHub.calls.clear()
    with pytest.raises(gh.GitHubError, match="not in this task's repo list"):
        run("tree", repo="owner/other")
    assert FakeGitHub.calls == []
    with pytest.raises(gh.GitHubError, match="owner/name"):
        run("tree", repo="junk")
    with pytest.raises(gh.GitHubError, match="rejected the token"):
        asyncio.run(gh.run("repos", {}, "wrong", [], "all"))
    with pytest.raises(gh.GitHubError, match="rate limit"):
        run("repos", allowed=["owner/limited"])
    with pytest.raises(gh.GitHubError, match="no access"):
        run("repos", allowed=["owner/secret"])


def test_search_commits_issues_prs_diff():
    out = run("search", q="retry")
    assert "1 hit(s)" in out and "owner/demo: src/app.py" in out
    assert FakeGitHub.calls[-1][1]["q"] == ["retry repo:owner/demo"]
    with pytest.raises(gh.GitHubError, match="needs a repo"):
        run("search", allowed=[], q="x")
    run("search", mode="all", allowed=[], q="x")
    assert FakeGitHub.calls[-1][1]["q"] == ["x"]
    out = run("commits", repo="owner/demo", n=3)
    assert "abcdef1  2026-06-02  Ann: fix retry" in out
    out = run("issues", repo="owner/demo")
    assert "#7 [open] bug  (bob) [p1]" in out and "#8" not in out
    out = run("prs", repo="owner/demo")
    assert "#8 [open] a PR  (bob)  fix -> main" in out
    out = run("pr_diff", repo="owner/demo", n=8)
    assert out.startswith("#8 a PR [open] fix -> main by bob · +1 -0 in 1 file(s)") and "+line" in out
    with pytest.raises(gh.GitHubError, match="state must be"):
        run("issues", repo="owner/demo", state="weird")


def test_clone_tarball_is_safe():
    dest = WORK / "clone"
    if dest.exists():
        import shutil
        shutil.rmtree(dest)
    out = run("clone", repo="owner/demo", _dest=str(dest))
    assert "cloned owner/demo: 3 file(s)" in out
    assert (dest / "README.md").read_bytes() == FILES["README.md"]
    assert (dest / "src" / "app.py").exists() and (dest / "bin" / "big.bin").stat().st_mode & 0o100
    assert not (dest / "link").exists() and not (WORK / "escape.txt").exists() and not (dest / ".git").exists()
    with pytest.raises(gh.GitHubError, match="not empty"):
        run("clone", repo="owner/demo", _dest=str(dest))


# ------------------------------------------------------------ operatord classification
def act(**kw):
    kw.setdefault("_cwd", str(WORK))
    kw.setdefault("_repos", ["owner/demo"])
    kw.setdefault("_gh_scope", "task")
    return kw


def test_classify_github():
    for o in ("repos", "tree", "read", "search", "commits", "issues", "prs", "pr_diff"):
        assert op.classify(act(tool="github", op=o, repo="owner/demo", n=1, q="x"), set(), False) == ("read", ""), o
    assert op.classify(act(tool="github", op="tree", repo="owner/other"), set(), False)[0] == "blocked"
    assert op.classify(act(tool="github", op="tree", repo="owner/other", _gh_scope="all"), set(), False)[0] == "read"
    assert op.classify(act(tool="github", op="tree", repo="bad"), set(), False)[0] == "blocked"
    assert op.classify(act(tool="github", op="delete_repo", repo="owner/demo"), set(), False)[0] == "blocked"
    assert op.classify(act(tool="github", op="tree", repo="owner/demo", _github_key=False), set(), False)[1].startswith("no GitHub token")
    assert op.classify(act(tool="github", op="search", q="x"), set(), False)[0] == "read"
    assert op.classify(act(tool="github", op="clone", repo="owner/demo", dest="demo"), set(), False) == ("change", f"clones owner/demo into {WORK / 'demo'}")
    assert op.classify(act(tool="github", op="clone", repo="owner/demo", dest="demo"), set(), True)[0] == "danger"
    assert op.classify(act(tool="github", op="clone", repo="owner/demo", dest="/tmp/elsewhere"), set(), False)[0] == "danger"
    assert op.classify(act(tool="github", op="clone", repo="owner/demo", dest=str(HOME / "airgap" / "x")), set(), False)[0] == "blocked"
    assert "GitHub read owner/demo src/app.py" in op.sentence({"tool": "github", "op": "read", "repo": "owner/demo", "path": "src/app.py"}, "why")
    assert op.action_label({"tool": "github", "op": "search", "repo": "owner/demo", "q": "retry"}) == "github search owner/demo retry"


class FakeNC:
    def __init__(self):
        self.out = []

    async def publish(self, subject, data):
        self.out.append(json.loads(data))


class Msg:
    reply = "r"


def test_create_validates_repos_and_token():
    nc = FakeNC()
    asyncio.run(op.on_create(nc, Msg(), {"task": "look at the repo", "repos": "owner/demo, nope"}))
    assert not nc.out[-1]["ok"] and "owner/name" in nc.out[-1]["error"]
    (HOME / "airgap" / "github.key").unlink()
    asyncio.run(op.on_create(nc, Msg(), {"task": "look at the repo", "repos": ["owner/demo"]}))
    assert not nc.out[-1]["ok"] and "torus key set github" in nc.out[-1]["error"]
    assert op.op_settings()["github_key"] is False and op.op_settings()["github_scope"] == "task"


def test_scripted_task_reads_repo_via_github_tool(monkeypatch):
    """Claude (scripted) plans one phase, reads README via the github tool, tries an out-of-scope repo (blocked),
    and finishes; the dossier mentions the repo."""
    script = iter([
        json.dumps({"phases": [{"title": "Read the repo", "reads": ["owner/demo README.md"], "changes": [], "runs": []}], "estimate_usd": 0.1, "notes": ""}),
        json.dumps({"say": "reading", "actions": [{"tool": "github", "op": "read", "repo": "owner/demo", "path": "README.md"},
                                                  {"tool": "github", "op": "tree", "repo": "owner/other"}]}),
        json.dumps({"phase_done": True, "summary": "README says hello torus"}),
    ])

    async def fake_llm(t, system, messages, max_tokens=4096):
        t["calls"] = t.get("calls", 0) + 1
        if t["calls"] == 2:
            assert "github" in system and "owner/demo" in system
        return next(script)

    async def fake_secret(name):
        assert name == "github"
        return TOKEN
    monkeypatch.setattr(op, "llm", fake_llm)
    monkeypatch.setattr(op, "get_secret", fake_secret)
    nc = FakeNC()

    async def flow():
        await op.on_create(nc, Msg(), {"task": "summarise the demo repo", "repos": "owner/demo"})
        tid = nc.out[-1]["task"]["id"]
        t = op.TASKS[tid]
        assert t["repos"] == ["owner/demo"] and t["github_scope"] == "task" and t["github_key"] is True
        await op.RUN[tid]
        assert t["status"] == "plan_ready"
        await op.on_action(nc, Msg(), {"id": tid, "action": "approve_plan"})
        for _ in range(200):
            if t["status"] not in ("running", "planning"):
                break
            await asyncio.sleep(0.05)
        return t
    t = asyncio.run(flow())
    assert t["status"] == "done"
    kinds = [(e["kind"], e["text"]) for e in t["log"]]
    assert ("cmd", "github read owner/demo README.md") in kinds
    assert any(k == "out" and "hello torus" in x for k, x in kinds)
    assert any(k == "blocked" and "owner/other" in x and "not in this task's repo list" in x for k, x in kinds)
    dossier = Path(t["dossier"]).read_text()
    assert "GitHub: owner/demo" in dossier and "README says hello torus" in dossier


# ------------------------------------------------------------ wiring
def test_gateway_settings_cli_audit_manifest():
    gw = (BIN / "gatewayd.py").read_text()
    assert '"github_scope" in body' in gw and 'body["github_scope"] not in ("task", "all")' in gw
    assert '"github": key_present("github")' in gw
    assert '"github"' in (BIN / "airgapd.py").read_text()
    torus = (ROOT / "torus" / "bin" / "torus").read_text()
    assert "anthropic|brave|cloudflare|github|webhook)" in torus and "gh) cmd_gh" in torus and "github_scope=$2" in torus
    assert 'ctl op create "$@"' in torus
    ctl = (BIN / "torusctl.py").read_text()
    assert 'add_parser("gh")' in ctl and '"--repos"' in ctl
    audit = (BIN / "torus_audit.py").read_text()
    assert '("extra-6", "GitHub token and Operator repo scope", check_github)' in audit
    assert "bin/torus_github.py" in (ROOT / "torus" / "manifest.txt").read_text()
    web = (ROOT / "torus" / "files" / "web" / "index.html").read_text()
    for tid in ("op-repos-input", "settings-gh-scope-toggle", "settings-gh-scope-all", "op-repos"):
        assert f'data-testid="{tid}"' in web, tid
    assert "repos: $(\"#op-repos\").value" in web
    r = subprocess.run(["bash", "-n", str(ROOT / "torus" / "bin" / "torus")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_audit_check_github():
    import torus_audit as au
    monkeypatch_paths(au)
    (HOME / "airgap" / "github.key").unlink()
    assert au.check_github()[0] == "skip"
    (HOME / "airgap" / "github.key").write_text("x\n")
    os.chmod(HOME / "airgap" / "github.key", 0o644)
    assert au.check_github()[0] == "fail"
    os.chmod(HOME / "airgap" / "github.key", 0o600)
    assert au.check_github()[0] == "pass"
    (HOME / "etc" / "operator.conf").write_text("github_scope=all\n")
    st, detail, fix = au.check_github()
    assert st == "warn" and "torus gh scope task" in fix


def monkeypatch_paths(au):
    au.AIRGAP = HOME / "airgap"
    au.ETC = HOME / "etc"
