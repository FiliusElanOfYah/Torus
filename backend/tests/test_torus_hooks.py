"""Phase 3c: webhooks. hookd signature/replay/body-cap checks, rule matching + task creation through a fake operatord,
tunnel.yml inspection, audit extra-7, install wiring (phase 11, services, manifest, CLI, settings card)."""
import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-hook-test")
os.environ["TORUS_HOME"] = str(HOME)
os.environ["TORUS_HOOKD_PORT"] = "7798"
sys.path.insert(0, str(BIN))
import hookd  # noqa: E402

SECRET = "s3cr3t"


class FakeMsg:
    def __init__(self, data):
        self.data = data


class FakeNC:
    def __init__(self, ok=True):
        self.created = []
        self.ok = ok

    async def request(self, subject, data, timeout=0):
        assert subject == "torus.op.create"
        body = json.loads(data)
        self.created.append(body)
        if not self.ok:
            return FakeMsg(json.dumps({"ok": False, "error": "two tasks are already active; stop one first"}).encode())
        return FakeMsg(json.dumps({"ok": True, "task": {"id": f"t-{len(self.created)}"}}).encode())


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    import shutil
    if HOME.exists():
        shutil.rmtree(HOME)
    for sub in ("etc", "logs", "var/hooks", "airgap", "bin"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "torus" / "files" / "etc" / "hooks.yaml", HOME / "etc" / "hooks.yaml")
    monkeypatch.setattr(hookd, "RULES", HOME / "etc" / "hooks.yaml")
    monkeypatch.setattr(hookd, "TUNNEL", HOME / "etc" / "tunnel.yml")
    monkeypatch.setattr(hookd, "HOOKS_DIR", HOME / "var" / "hooks")
    monkeypatch.setattr(hookd, "EVENTS", HOME / "var" / "hooks" / "events.jsonl")
    monkeypatch.setattr(hookd, "SEEN_FILE", HOME / "var" / "hooks" / "seen.json")
    (HOME / "airgap" / "webhook.key").write_text(SECRET + "\n")

    async def fake_secret(name):
        assert name == "webhook"
        return SECRET
    monkeypatch.setattr(hookd, "get_secret", fake_secret)
    hookd.SEEN.clear(); hookd.RECENT.clear()
    hookd.NC["nc"] = FakeNC()
    yield


def deliver(event, payload, delivery="d-1", secret=SECRET, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"x-github-event": event, "x-github-delivery": delivery, "x-hub-signature-256": sig}
    return asyncio.run(hookd.handle_github(headers, body))


def test_signature_replay_and_unknown_events():
    st, r = deliver("ping", {"zen": "x", "repository": {"full_name": "o/r"}})
    assert st == 200 and r["pong"]
    st, r = deliver("ping", {"zen": "x"}, delivery="d-1")
    assert st == 409
    st, r = deliver("ping", {"zen": "x"}, delivery="d-2", secret="wrong")
    assert st == 401
    st, r = deliver("ping", {"zen": "x"}, delivery="")
    assert st == 409
    st, r = deliver("star", {"a": 1}, delivery="d-3")
    assert st == 202 and r["ignored"] == "star"
    st, r = deliver("issues", {}, delivery="d-4", raw=b"not json")
    assert st == 400
    assert json.loads((HOME / "var" / "hooks" / "seen.json").read_text())[-1] == "d-4"
    assert [e["ok"] for e in hookd.RECENT] == [True, False, False, False, True]
    (HOME / "airgap" / "webhook.key").unlink()
    st, r = deliver("ping", {"zen": "x"}, delivery="d-5")
    assert st == 503 and "secret" in r["error"]


def test_rules_create_tasks_that_wait_for_approval():
    payload = {"action": "created", "repository": {"full_name": "owner/demo"}, "sender": {"login": "ann"},
               "issue": {"number": 7, "title": "Retry bug", "html_url": "https://github.com/owner/demo/issues/7"},
               "comment": {"body": "@torus please look at the retry loop", "html_url": "https://github.com/owner/demo/issues/7#c1"}}
    st, r = deliver("issue_comment", payload, delivery="e-1")
    assert st == 200 and r["created"] == [{"rule": "@torus in an issue or PR comment", "task": "t-1"}]
    body = hookd.NC["nc"].created[0]
    assert body["repos"] == ["owner/demo"] and body["source"] == "webhook"
    assert body["task"].startswith("GitHub owner/demo #7 (Retry bug) — ann wrote:\n\n@torus please look at the retry loop")
    # no @torus -> no task
    payload["comment"]["body"] = "just chatting"
    st, r = deliver("issue_comment", payload, delivery="e-2")
    assert st == 200 and r["created"] == []
    # failed workflow on main
    wf = {"action": "completed", "repository": {"full_name": "owner/demo"}, "sender": {"login": "ci"},
          "workflow_run": {"name": "tests", "conclusion": "failure", "head_branch": "main", "html_url": "https://github.com/owner/demo/actions/runs/1"}}
    st, r = deliver("workflow_run", wf, delivery="e-3")
    assert r["created"][0]["task"] == "t-2" and "The workflow 'tests' failed on main in owner/demo" in hookd.NC["nc"].created[1]["task"]
    wf["workflow_run"]["head_branch"] = "feature"
    st, r = deliver("workflow_run", wf, delivery="e-4")
    assert r["created"] == []
    # operatord refusing is reported, not raised
    hookd.NC["nc"] = FakeNC(ok=False)
    payload["comment"]["body"] = "@torus again"
    st, r = deliver("issue_comment", payload, delivery="e-5")
    assert st == 200 and "two tasks" in r["created"][0]["error"]
    lines = (HOME / "var" / "hooks" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 5 and json.loads(lines[0])["created"][0]["task"] == "t-1"


def test_rule_fields_and_matching():
    f = hookd.fields("push", {"ref": "refs/heads/dev", "repository": {"full_name": "o/r"}, "head_commit": {"message": "fix"}, "sender": {"login": "x"}})
    assert f["branch"] == "dev" and f["title"] == "fix"
    f = hookd.fields("pull_request", {"action": "opened", "repository": {"full_name": "o/r"}, "pull_request": {"number": 3, "title": "PR", "head": {"ref": "feat"}, "labels": [{"name": "urgent"}]}})
    assert f["branch"] == "feat" and f["number"] == "3" and f["labels"] == "urgent"
    assert hookd.matches({"event": "pull_request", "action": "opened", "label": "URGENT", "repo": "O/R"}, f)
    assert not hookd.matches({"event": "pull_request", "repo": "other/r"}, f)
    assert not hookd.matches({"event": "push"}, f)
    assert hookd.render("PR {number} on {branch} by {sender} {nope}", f) == "PR 3 on feat by  {nope}"
    (HOME / "etc" / "hooks.yaml").write_text("rules: [{event: push}, {task: x}, {event: push, task: 'Push to {branch}'}]\n")
    assert [r["task"] for r in hookd.load_rules()] == ["Push to {branch}"]
    (HOME / "etc" / "hooks.yaml").write_text(": bad: yaml: [")
    assert hookd.load_rules() == []


def test_tunnel_info_and_audit():
    import torus_audit as au
    au.HOME, au.ETC, au.AIRGAP = HOME, HOME / "etc", HOME / "airgap"
    assert au.check_hooks()[0] == "skip"
    (HOME / "bin" / "hookd.py").write_text("#")
    assert au.check_hooks()[0] == "pass" and hookd.tunnel_info() == {"configured": False, "hostname": "", "ok": True, "why": ""}
    tun = HOME / "etc" / "tunnel.yml"
    tun.write_text("tunnel: abc\ningress:\n  - hostname: hooks.guardiaios.app\n    service: http://127.0.0.1:7777\n  - service: http_status:404\n")
    os.chmod(tun, 0o600)
    st, detail, fix = au.check_hooks()
    assert st == "fail" and "7777" in detail
    assert not hookd.tunnel_info()["ok"]
    tun.write_text("tunnel: abc\ningress:\n  - hostname: hooks.guardiaios.app\n    service: http://127.0.0.1:7798\n  - service: http_status:404\n")
    assert hookd.tunnel_info() == {"configured": True, "hostname": "hooks.guardiaios.app", "ok": True, "why": ""}
    tun.write_text("tunnel: abc\ningress:\n  - hostname: hooks.guardiaios.app\n    service: http://127.0.0.1:7778\n  - service: http_status:404\n")
    (HOME / "airgap" / "webhook.key").unlink()
    assert au.check_hooks()[0] == "fail" and "secret" in au.check_hooks()[1]
    (HOME / "airgap" / "webhook.key").write_text("k\n")
    os.chmod(HOME / "airgap" / "webhook.key", 0o600)
    assert au.check_hooks()[0] == "pass"
    os.chmod(tun, 0o644)
    assert au.check_hooks()[0] == "warn"


def test_http_server_roundtrip():
    async def go():
        hookd.NC["nc"] = FakeNC()
        server = await asyncio.start_server(hookd.serve, "127.0.0.1", 7798)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("http://127.0.0.1:7798/hook/health")
                assert r.status_code == 200 and r.json()["ok"] and r.json()["rules"] == 2
                body = json.dumps({"zen": "x"}).encode()
                sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
                r = await c.post("http://127.0.0.1:7798/hook/github", content=body, headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "h-1", "X-Hub-Signature-256": sig})
                assert r.status_code == 200 and r.json()["pong"]
                r = await c.get("http://127.0.0.1:7798/nope")
                assert r.status_code == 404
            rd, wr = await asyncio.open_connection("127.0.0.1", 7798)
            wr.write(b"POST /hook/github HTTP/1.1\r\nHost: x\r\nContent-Length: 2000000\r\n\r\n")
            await wr.drain()
            head = await asyncio.wait_for(rd.readline(), 5)
            assert b"413" in head
            wr.close()
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(go())


def test_wiring():
    t = ROOT / "torus"
    assert "11 hookd" in (t / "services.txt").read_text() and "11 cloudflared  cloudflared.run" in (t / "services.txt").read_text()
    man = (t / "manifest.txt").read_text()
    assert "11 own  0755 bin/hookd.py" in man and "11 keep 0644 etc/hooks.yaml" in man
    assert (t / "phases" / "11-hooks.sh").exists() and (t / "files" / "service" / "cloudflared.run").exists()
    torus = (t / "bin" / "torus").read_text()
    assert 'PHASES="0 1 2 3 4 5 6 7 8 9 10 11"' in torus and "hookd cloudflared" in torus
    for w in ("cmd_hooks()", "cmd_tunnel()", "webhook.key", "cloudflared tunnel route dns", "service: http://127.0.0.1:7778", "hooks) cmd_hooks", "tunnel) cmd_tunnel", "anthropic|brave|cloudflare|github|webhook)"):
        assert w in torus, w
    assert '"webhook"' in (BIN / "airgapd.py").read_text()
    assert 'add_parser("hooks")' in (BIN / "torusctl.py").read_text() and "/repos/{args[1]}/hooks" in (BIN / "torusctl.py").read_text()
    assert '("extra-7", "Webhooks: tunnel exposes only hookd", check_hooks)' in (BIN / "torus_audit.py").read_text()
    gw = (BIN / "gatewayd.py").read_text()
    assert 'path == "/api/hooks"' in gw and '"torus.hooks.status"' in gw
    web = (t / "files" / "web" / "index.html").read_text()
    assert 'data-testid="hooks-body"' in web and "renderHooks()" in web
    r = subprocess.run(["bash", "-n", str(t / "bin" / "torus")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["bash", "-n", str(t / "phases" / "11-hooks.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
