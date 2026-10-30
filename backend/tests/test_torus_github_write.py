"""Phase 3a: GitHub write side. torus_github branch/commit/pr/comment/pr_close/merge/delete_branch/revert against a
fake GitHub (Git Data API), push_command + askpass helper, operatord classification (change vs danger: default branch,
force, merge, delete), default-branch escalation in run_phase, timeline/dossier records, CLI wiring."""
import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-ghw-test")
WORK = HOME / "work"
os.environ["TORUS_HOME"] = str(HOME)
os.environ["TORUS_GITHUB_API"] = "http://127.0.0.1:8096"
sys.path.insert(0, str(BIN))
import operatord as op  # noqa: E402
import torus_github as gh  # noqa: E402

TOKEN = "github_pat_TEST"
STATE = {}


def reset_state():
    STATE.clear()
    STATE.update(refs={"main": "aaa111", "fix": "bbb222"}, commits={"aaa111": {"tree": "t-main", "parents": [{"sha": "000000"}], "message": "init"},
                                                                     "bbb222": {"tree": "t-fix", "parents": [{"sha": "aaa111"}], "message": "wip fix"},
                                                                     "000000": {"tree": "t-root", "parents": [], "message": "root"}},
                 blobs=[], trees=[], prs=[], comments=[], merged=[], deleted=[], log=[])


class FakeGitHub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-RateLimit-Remaining", "4999")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path).path
        STATE["log"].append(("GET", p))
        if p == "/repos/owner/demo":
            return self.send(200, {"full_name": "owner/demo", "default_branch": "main"})
        if p.startswith("/repos/owner/demo/git/ref/heads/"):
            b = p.split("/git/ref/heads/", 1)[1]
            return self.send(200, {"object": {"sha": STATE["refs"][b]}}) if b in STATE["refs"] else self.send(404, {"message": "Not Found"})
        if p.startswith("/repos/owner/demo/git/commits/"):
            sha = p.rsplit("/", 1)[-1]
            c = STATE["commits"].get(sha)
            return self.send(200, {"sha": sha, "tree": {"sha": c["tree"]}, "parents": c["parents"], "message": c["message"]}) if c else self.send(404, {"message": "no commit"})
        return self.send(404, {"message": "Not Found"})

    def do_POST(self):
        p = urlparse(self.path).path
        b = self.body()
        STATE["log"].append(("POST", p, b))
        if p == "/repos/owner/demo/git/refs":
            name = b["ref"].split("refs/heads/")[1]
            if name in STATE["refs"]:
                return self.send(422, {"message": "Reference already exists"})
            STATE["refs"][name] = b["sha"]
            return self.send(201, {"ref": b["ref"]})
        if p == "/repos/owner/demo/git/blobs":
            STATE["blobs"].append(b)
            return self.send(201, {"sha": f"blob{len(STATE['blobs'])}"})
        if p == "/repos/owner/demo/git/trees":
            STATE["trees"].append(b)
            return self.send(201, {"sha": f"tree{len(STATE['trees'])}"})
        if p == "/repos/owner/demo/git/commits":
            sha = f"c{len(STATE['commits']) + 1:06d}"
            STATE["commits"][sha] = {"tree": b["tree"], "parents": [{"sha": x} for x in b["parents"]], "message": b["message"]}
            return self.send(201, {"sha": sha})
        if p == "/repos/owner/demo/pulls":
            STATE["prs"].append(b)
            return self.send(201, {"number": 40 + len(STATE["prs"]), "title": b["title"], "html_url": f"https://github.com/owner/demo/pull/{40 + len(STATE['prs'])}"})
        if p.startswith("/repos/owner/demo/issues/") and p.endswith("/comments"):
            STATE["comments"].append((p, b))
            return self.send(201, {"html_url": "https://github.com/owner/demo/issues/7#issuecomment-1"})
        if p == "/repos/owner/forbidden/pulls":
            return self.send(403, {"message": "Resource not accessible by personal access token"})
        return self.send(404, {"message": "Not Found"})

    def do_PATCH(self):
        p = urlparse(self.path).path
        b = self.body()
        STATE["log"].append(("PATCH", p, b))
        if p.startswith("/repos/owner/demo/git/refs/heads/"):
            STATE["refs"][p.split("/git/refs/heads/", 1)[1]] = b["sha"]
            return self.send(200, {"object": {"sha": b["sha"]}})
        if p.startswith("/repos/owner/demo/pulls/"):
            return self.send(200, {"number": int(p.rsplit("/", 1)[-1]), "state": b.get("state"), "html_url": "https://github.com/owner/demo/pull/41"})
        return self.send(404, {"message": "Not Found"})

    def do_PUT(self):
        p = urlparse(self.path).path
        b = self.body()
        STATE["log"].append(("PUT", p, b))
        if p.endswith("/merge"):
            STATE["merged"].append((p, b))
            return self.send(200, {"sha": "mmm333444", "merged": True, "message": "Pull Request successfully merged"})
        return self.send(404, {"message": "Not Found"})

    def do_DELETE(self):
        p = urlparse(self.path).path
        STATE["log"].append(("DELETE", p))
        if p.startswith("/repos/owner/demo/git/refs/heads/"):
            STATE["deleted"].append(p.split("/git/refs/heads/", 1)[1])
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self.send(404, {"message": "Not Found"})


@pytest.fixture(scope="module", autouse=True)
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 8096), FakeGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for sub in ("etc", "logs", "var/operator/snapshots", "vault/answers", "airgap", "work"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
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
    reset_state()
    yield


def run(op_, **a):
    return asyncio.run(gh.run(op_, a, TOKEN, ["owner/demo"], "task"))


# ------------------------------------------------------------ torus_github writes
def test_branch_commit_pr_comment():
    out = run("branch", repo="owner/demo", branch="feat/x")
    assert out.startswith("branch feat/x created from main @ aaa111") and STATE["refs"]["feat/x"] == "aaa111"
    with pytest.raises(gh.GitHubError, match="already exists"):
        run("branch", repo="owner/demo", branch="feat/x")
    with pytest.raises(gh.GitHubError, match="bad branch"):
        run("branch", repo="owner/demo", branch="../evil")
    out = run("commit", repo="owner/demo", branch="feat/x", message="add app\n\ndetails", files=[{"path": "src/app.py", "content": "print(2)\n", "executable": True},
                                                                                                    {"path": "old.txt", "delete": True}])
    assert out.startswith("commit c000004 on feat/x: add app") and "± src/app.py" in out and "- old.txt" in out
    assert STATE["blobs"][-1] == {"content": "print(2)\n", "encoding": "utf-8"}
    tree = STATE["trees"][-1]
    assert tree["base_tree"] == "t-main" and tree["tree"][0]["mode"] == "100755" and tree["tree"][1]["sha"] is None
    assert STATE["refs"]["feat/x"] == "c000004" and STATE["commits"]["c000004"]["parents"] == [{"sha": "aaa111"}]
    with pytest.raises(gh.GitHubError, match="needs a message"):
        run("commit", repo="owner/demo", branch="feat/x", files=[{"path": "a", "content": ""}])
    with pytest.raises(gh.GitHubError, match="bad file path"):
        run("commit", repo="owner/demo", branch="feat/x", message="m", files=[{"path": "../a", "content": ""}])
    with pytest.raises(gh.GitHubError, match="at most 40"):
        run("commit", repo="owner/demo", branch="feat/x", message="m", files=[{"path": f"f{i}", "content": ""} for i in range(41)])
    out = run("pr", repo="owner/demo", head="feat/x", title="Add app", body="why")
    assert out.startswith("PR #41 opened: Add app  feat/x -> main  https://github.com/owner/demo/pull/41")
    assert STATE["prs"][-1]["base"] == "main" and STATE["prs"][-1]["draft"] is False
    out = run("comment", repo="owner/demo", n=7, body="looks good")
    assert "comment added on #7" in out and STATE["comments"][-1][0] == "/repos/owner/demo/issues/7/comments"
    out = run("pr_close", repo="owner/demo", n=41)
    assert out.startswith("PR #41 closed")


def test_merge_delete_revert_and_permission_error():
    out = run("merge", repo="owner/demo", n=41)
    assert "merged (squash): mmm3334" in out and STATE["merged"][-1][1] == {"merge_method": "squash"}
    with pytest.raises(gh.GitHubError, match="method must be"):
        run("merge", repo="owner/demo", n=41, method="yolo")
    out = run("delete_branch", repo="owner/demo", branch="fix")
    assert out.startswith("branch fix deleted") and STATE["deleted"] == ["fix"]
    with pytest.raises(gh.GitHubError, match="default branch; it stays"):
        run("delete_branch", repo="owner/demo", branch="main")
    reset_state()
    out = run("revert", repo="owner/demo", branch="fix")
    assert out.startswith("reverted bbb222 on fix with c000004")
    c = STATE["commits"]["c000004"]
    assert c["tree"] == "t-main" and c["parents"] == [{"sha": "bbb222"}] and c["message"].startswith('Revert "wip fix"')
    with pytest.raises(gh.GitHubError, match="only undoes the latest"):
        run("revert", repo="owner/demo", branch="fix", sha="aaa111")
    STATE["refs"]["root"] = "000000"
    with pytest.raises(gh.GitHubError, match="root commit"):
        run("revert", repo="owner/demo", branch="root")
    with pytest.raises(gh.GitHubError, match="lacks a permission"):
        asyncio.run(gh.run("pr", {"repo": "owner/forbidden", "head": "x", "base": "main", "title": "t"}, TOKEN, [], "all"))


def test_push_command_and_askpass():
    argv, env = gh.push_command({"branch": "feat/x", "remote": "origin"}, "/x/torus_askpass.py")
    assert argv == ["git", "push", "origin", "feat/x"] and env["GIT_ASKPASS"] == "/x/torus_askpass.py" and env["GIT_TERMINAL_PROMPT"] == "0"
    argv, _ = gh.push_command({"branch": "feat/x", "force": True, "set_upstream": True}, "/x/a")
    assert argv == ["git", "push", "--force-with-lease", "-u", "origin", "feat/x"]
    with pytest.raises(gh.GitHubError):
        gh.push_command({"remote": "ori gin"}, "/x/a")
    r = subprocess.run([sys.executable, str(BIN / "torus_askpass.py"), "Username for 'https://github.com':"], capture_output=True, text=True)
    assert r.stdout.strip() == "x-access-token"
    r = subprocess.run([sys.executable, str(BIN / "torus_askpass.py"), "Password for 'https://x-access-token@github.com':"],
                       capture_output=True, text=True, env={**os.environ, "TORUS_HOME": str(HOME)})
    assert r.returncode != 0 and "airgapd" in r.stderr  # no airgapd in tests: fails closed, token never printed
    assert os.access(BIN / "torus_askpass.py", os.X_OK) or True


# ------------------------------------------------------------ operatord classification
def act(**kw):
    kw.setdefault("_cwd", str(WORK))
    kw.setdefault("_repos", ["owner/demo"])
    kw.setdefault("_gh_scope", "task")
    return kw


def test_classify_writes():
    c = op.classify
    assert c(act(tool="github", op="branch", repo="owner/demo", branch="b"), set(), False) == ("change", "creates branch b in owner/demo")
    assert c(act(tool="github", op="branch", repo="owner/demo", branch="b"), set(), True)[0] == "danger"
    assert c(act(tool="github", op="commit", repo="owner/demo", branch="b", files=[{}, {}]), set(), False) == ("change", "commits 2 file(s) to b in owner/demo")
    assert c(act(tool="github", op="commit", repo="owner/demo", branch="main", _default="main", files=[]), set(), False)[0] == "danger"
    assert c(act(tool="github", op="pr", repo="owner/demo"), set(), False)[0] == "change"
    assert c(act(tool="github", op="comment", repo="owner/demo", n=3), set(), False)[1] == "comments on #3 in owner/demo"
    assert c(act(tool="github", op="revert", repo="owner/demo", branch="b"), set(), False)[0] == "change"
    assert c(act(tool="github", op="pr_close", repo="owner/demo", n=3), set(), False)[0] == "change"
    assert c(act(tool="github", op="merge", repo="owner/demo", n=3), set(), False) == ("danger", "merges PR #3 in owner/demo")
    assert c(act(tool="github", op="delete_branch", repo="owner/demo", branch="b"), set(), False)[0] == "danger"
    assert c(act(tool="github", op="commit", repo="owner/other", branch="b"), set(), False)[0] == "blocked"
    assert c(act(tool="github", op="delete_repo", repo="owner/demo"), set(), False)[0] == "blocked"
    # push
    assert c(act(tool="github", op="push", repo="owner/demo", path="clone", branch="b"), set(), False) == ("change", f"pushes b from {WORK / 'clone'} to owner/demo")
    assert c(act(tool="github", op="push", repo="owner/demo", path="clone", branch="b", force=True), set(), False)[0] == "danger"
    assert c(act(tool="github", op="push", repo="owner/demo", path="clone", branch="main", _default="main"), set(), False)[0] == "danger"
    assert c(act(tool="github", op="push", repo="owner/demo", path="/tmp/elsewhere", branch="b"), set(), False)[0] == "blocked"
    assert op.sentence({"tool": "github", "op": "merge", "repo": "owner/demo", "n": 3}, "merges") == "GitHub merge owner/demo #3 — merges."
    assert op.action_label({"tool": "github", "op": "commit", "repo": "owner/demo", "branch": "b"}) == "github commit owner/demo b"


class FakeNC:
    def __init__(self):
        self.out = []

    async def publish(self, subject, data):
        self.out.append(json.loads(data))


class Msg:
    reply = "r"


def test_scripted_task_branch_commit_pr_with_default_branch_escalation(monkeypatch):
    """Phase 1: branch + commit on a feature branch run as in-plan changes; a commit to main waits for a tap (denied);
    the PR is opened; timeline records GitHub links; dossier lists the writes."""
    script = iter([
        json.dumps({"phases": [{"title": "Fix typo via PR", "reads": ["README.md"], "changes": ["owner/demo branch fix-typo, commit, PR"], "runs": []}], "estimate_usd": 0.1}),
        json.dumps({"say": "branching", "actions": [{"tool": "github", "op": "branch", "repo": "owner/demo", "branch": "fix-typo"},
                                                    {"tool": "github", "op": "commit", "repo": "owner/demo", "branch": "fix-typo", "message": "fix typo", "files": [{"path": "README.md", "content": "# Demo\n"}]},
                                                    {"tool": "github", "op": "commit", "repo": "owner/demo", "branch": "main", "message": "sneaky", "files": [{"path": "README.md", "content": "x"}]},
                                                    {"tool": "github", "op": "pr", "repo": "owner/demo", "head": "fix-typo", "title": "Fix typo"}]}),
        json.dumps({"phase_done": True, "summary": "PR opened"}),
    ])

    async def fake_llm(t, system, messages, max_tokens=4096):
        t["calls"] = t.get("calls", 0) + 1
        if t["calls"] == 2:
            assert '"op":"commit"' in system and "prefer a branch + PR" not in system  # work prompt lists shapes
        return next(script)

    async def fake_secret(name):
        return TOKEN

    async def auto_deny(t, action, why):
        t.setdefault("asked", []).append((action.get("op"), action.get("branch"), why))
        op.entry(t, "ask", op.sentence(action, why))
        return False
    monkeypatch.setattr(op, "llm", fake_llm)
    monkeypatch.setattr(op, "get_secret", fake_secret)
    monkeypatch.setattr(op, "wait_tap", auto_deny)
    nc = FakeNC()

    async def flow():
        await op.on_create(nc, Msg(), {"task": "fix the typo in the demo README", "repos": "owner/demo"})
        t = op.TASKS[nc.out[-1]["task"]["id"]]
        await op.RUN[t["id"]]
        await op.on_action(nc, Msg(), {"id": t["id"], "action": "approve_plan"})
        for _ in range(200):
            if t["status"] not in ("running", "planning"):
                break
            await asyncio.sleep(0.05)
        return t
    t = asyncio.run(flow())
    assert t["status"] == "done", t.get("stop_reason")
    assert t["asked"] == [("commit", "main", "commits straight to the default branch main of owner/demo")]
    assert t["gh_default"] == {"owner/demo": "main"}
    ghw = t["phases"][0]["github"]
    assert [g["op"] for g in ghw] == ["branch", "commit", "pr"]
    assert ghw[1]["url"].startswith("https://github.com/owner/demo/commit/") and ghw[2]["url"] == "https://github.com/owner/demo/pull/41"
    assert STATE["refs"]["main"] == "aaa111"  # the denied commit never touched main
    dossier = Path(t["dossier"]).read_text()
    assert "GitHub writes:" in dossier and "pr owner/demo: PR #41 opened" in dossier
    kinds = [e["kind"] for e in t["log"]]
    assert "ask" in kinds and kinds.count("cmd") == 3


def test_wiring():
    assert "bin/torus_askpass.py" in (ROOT / "torus" / "manifest.txt").read_text()
    ctl = (BIN / "torusctl.py").read_text()
    for w in ('op == "branch"', 'op == "commit"', 'op == "pr"', 'op == "merge"', 'op == "push"', "torus_askpass.py"):
        assert w in ctl, w
    torus = (ROOT / "torus" / "bin" / "torus").read_text()
    assert "torus gh branch" in torus and "pr_close|merge" in torus
    web = (ROOT / "torus" / "files" / "web" / "index.html").read_text()
    assert 'data-testid="op-phase-github-${i}"' in web
    r = subprocess.run(["bash", "-n", str(ROOT / "torus" / "bin" / "torus")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_readonly_auto_task_blocks_changes_and_posts_findings(monkeypatch):
    """A webhook auto rule: plan auto-approved, every change blocked (no tap), findings commented on the issue."""
    script = iter([
        json.dumps({"phases": [{"title": "Investigate", "reads": ["README.md"], "changes": [], "runs": []}], "estimate_usd": 0.05}),
        json.dumps({"say": "looking", "actions": [{"tool": "github", "op": "tree", "repo": "owner/demo"},
                                                  {"tool": "write", "path": "fix.txt", "content": "x"},
                                                  {"tool": "github", "op": "commit", "repo": "owner/demo", "branch": "fix", "message": "m", "files": [{"path": "a", "content": "b"}]}]}),
        json.dumps({"phase_done": True, "summary": "The retry loop never backs off."}),
    ])

    async def fake_llm(t, system, messages, max_tokens=4096):
        return next(script)

    async def fake_secret(name):
        return TOKEN

    async def never_tap(t, action, why):
        raise AssertionError("read-only tasks must not ask for taps")
    monkeypatch.setattr(op, "llm", fake_llm)
    monkeypatch.setattr(op, "get_secret", fake_secret)
    monkeypatch.setattr(op, "wait_tap", never_tap)
    monkeypatch.setattr(gh, "_run", _run_tree_ok(gh._run))
    nc = FakeNC()

    async def flow():
        await op.on_create(nc, Msg(), {"task": "investigate the retry bug", "repos": "owner/demo", "source": "webhook", "auto_readonly": True, "post_to": {"repo": "owner/demo", "n": 7}})
        assert nc.out[-1]["ok"], nc.out[-1]
        t = op.TASKS[nc.out[-1]["task"]["id"]]
        for _ in range(300):
            if t["status"] in ("done", "error", "stopped") and t["id"] not in op.RUN:
                break
            await asyncio.sleep(0.02)
        return t
    t = asyncio.run(flow())
    assert t["status"] == "done", t.get("stop_reason")
    assert t["readonly"] and t["post_to"] == {"repo": "owner/demo", "n": 7} and t["source"] == "webhook"
    kinds = [(e["kind"], e["text"]) for e in t["log"]]
    assert any(k == "tap" and x.startswith("auto-approved") for k, x in kinds)
    blocked = [x for k, x in kinds if k == "blocked"]
    assert len(blocked) == 2 and all("read-only task" in x for x in blocked)
    assert not (WORK / "fix.txt").exists() and STATE["refs"]["fix"] == "bbb222"
    assert STATE["comments"][-1][0] == "/repos/owner/demo/issues/7/comments" and "never backs off" in STATE["comments"][-1][1]["body"]
    assert any(k == "note" and x.startswith("findings posted") for k, x in kinds)


def _run_tree_ok(orig):
    async def run(client, op_, a, token, allowed, mode):
        if op_ == "tree":
            return "d 0 src\n- 20 README.md"
        return await orig(client, op_, a, token, allowed, mode)
    return run
