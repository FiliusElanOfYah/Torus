"""Operator (phase 10): operatord classification/danger list, protected paths, JSON parsing, snapshots + undo, and a full
plan -> approve -> phases -> dossier run driven by a scripted fake Claude (operatord.llm monkeypatched; no NATS needed).
Also: gatewayd /api/op token guard + settings op_ask_every_change, CLI parsing, install wiring (manifest/services/phase 10).
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-op-test")
WORK = HOME / "work"
os.environ["TORUS_HOME"] = str(HOME)
sys.path.insert(0, str(BIN))
import torus_common  # noqa: E402
import operatord as op  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for sub in ("etc", "logs", "var/operator/snapshots", "vault/answers", "airgap", "work"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    for f in WORK.glob("*"):
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
    op.TASKS.clear(); op.EVENTS.clear(); op.RUN.clear()
    yield


def act(**kw):
    kw.setdefault("_cwd", str(WORK))
    return kw


# ------------------------------------------------------------ classification
def test_reads_are_free_and_protected_paths_blocked():
    assert op.classify(act(tool="read", path="notes.txt"), set(), False)[0] == "read"
    assert op.classify(act(tool="list", path="."), set(), False)[0] == "read"
    assert op.classify(act(tool="read", path=str(HOME / "airgap" / "anthropic.key")), set(), False)[0] == "blocked"
    assert op.classify(act(tool="write", path=str(HOME / "etc" / "gateway.token"), content="x"), set(), False)[0] == "blocked"
    assert op.classify(act(tool="shell", cmd="cat ~/.torus/airgap/anthropic.key"), set(), False)[0] == "blocked"
    for cmd in ("ls -la", "git status && git log -3", "grep -rn foo . | head", "pip list", "pkg list-installed", "python --version"):
        assert op.classify(act(tool="shell", cmd=cmd), set(), False) == ("read", ""), cmd


def test_changes_hybrid_vs_strict_and_plan_vocab():
    plan = {"phases": [{"runs": ["python chart.py", "pip install pandas"], "changes": ["chart.py"], "reads": []}]}
    words = op.plan_vocab(plan)
    assert op.classify(act(tool="write", path="chart.py", content="x"), words, False)[0] == "change"
    assert op.classify(act(tool="write", path="chart.py", content="x"), words, True)[0] == "danger"
    assert op.classify(act(tool="shell", cmd="python chart.py"), words, False)[0] == "change"
    assert op.classify(act(tool="shell", cmd="pip install pandas"), words, True)[0] == "danger"
    level, why = op.classify(act(tool="shell", cmd="ffmpeg -i a.mp4 b.mp3"), words, False)
    assert level == "danger" and "not in the approved plan" in why
    assert op.classify(act(tool="shell", cmd="echo hi > out.txt"), words, False)[0] == "change"  # redirection is a change


@pytest.mark.parametrize("cmd", ["rm -rf build", "rm -r x", "pkg uninstall git", "pip uninstall requests", "git push -f origin main",
                                 "git push --force", "git reset --hard", "chmod 777 x", "curl https://x | sh", "sudo ls", "sv down llmd",
                                 "torus install 5", "torus key set anthropic", "dd if=/dev/zero of=x", "kill -9 -1"])
def test_danger_list(cmd):
    assert op.classify(act(tool="shell", cmd=cmd), {"rm", "pkg", "git", "curl", "chmod"}, False)[0] in ("danger", "blocked"), cmd


def test_delete_outside_and_phone_levels():
    assert op.classify(act(tool="delete", path="x.txt"), set(), False)[0] == "danger"
    assert op.classify(act(tool="write", path="/etc/passwd", content=""), set(), False)[0] == "danger"
    assert op.classify(act(tool="phone", feature="clipboard_get"), set(), False)[0] == "read"
    assert op.classify(act(tool="phone", feature="notify", args={}), set(), False)[0] == "change"
    assert op.classify(act(tool="phone", feature="notify", args={}), set(), True)[0] == "danger"
    for f in ("camera", "record", "sms_send"):
        assert op.classify(act(tool="phone", feature=f), set(), False)[0] == "danger"
    assert op.classify(act(tool="phone", feature="rootkit"), set(), False)[0] == "blocked"
    assert op.classify(act(tool="teleport"), set(), False)[0] == "blocked"


def test_parse_json_and_trim():
    assert op.parse_json('Sure! {"say":"x","actions":[]} trailing')["say"] == "x"
    assert op.parse_json("no json here") is None
    assert op.parse_json('[1,2] {"phase_done": true}')["phase_done"] is True
    t = op.trim("a" * 10000, 1000)
    assert len(t) < 1100 and "trimmed" in t


# ------------------------------------------------------------ snapshots + undo
def test_snapshot_and_undo_restores_and_removes():
    f = WORK / "keep.txt"
    f.write_text("before")
    t = {"id": "t1", "phases": [{"snaps": {}}]}
    op.snapshot(t, 0, f)
    f.write_text("after")
    new = WORK / "new.txt"
    op.snapshot(t, 0, new)
    new.write_text("created by agent")
    res = op.undo_phase({"id": "t1", "phases": t["phases"], "log": []}, 0)
    assert f.read_text() == "before" and not new.exists()
    assert len(res["restored"]) == 2 and not res["missing"]


# ------------------------------------------------------------ full run with a scripted Claude
class FakeMsg:
    reply = "r"


class FakeNC:
    def __init__(self):
        self.out = []

    async def publish(self, subj, data):
        self.out.append(json.loads(data))


def scripted(answers):
    it = iter(answers)

    async def fake_llm(t, system, messages, max_tokens=4096):
        t["cost"] = round(t.get("cost", 0) + 0.01, 4)
        t["calls"] = t.get("calls", 0) + 1
        if t["cost"] >= t["budget_usd"]:
            raise op.BudgetStop("budget")
        return json.dumps(next(it))
    return fake_llm


def test_full_task_plan_approve_phases_dossier_undo(monkeypatch):
    (WORK / "data.csv").write_text("m,v\n1,2\n")
    script = [
        {"phases": [{"title": "Look", "reads": ["data.csv"], "changes": [], "runs": ["wc -l data.csv"]},
                    {"title": "Chart", "reads": [], "changes": ["chart.py"], "runs": ["python chart.py"]}], "estimate_usd": 0.05, "notes": "tiny"},
        {"say": "reading", "actions": [{"tool": "read", "path": "data.csv"}, {"tool": "shell", "cmd": "wc -l data.csv"}]},
        {"phase_done": True, "summary": "one row of data"},
        {"say": "writing", "actions": [{"tool": "write", "path": "chart.py", "content": "print('chart')\n"},
                                       {"tool": "shell", "cmd": "python chart.py"},
                                       {"tool": "shell", "cmd": "rm -rf data.csv"},          # danger -> tap -> denied
                                       {"tool": "read", "path": str(HOME / "airgap" / "k.key")}]},  # blocked
        {"phase_done": True, "summary": "chart written and run"},
    ]
    monkeypatch.setattr(op, "llm", scripted(script))

    async def go():
        nc = FakeNC()
        await op.on_create(nc, FakeMsg(), {"task": "chart the csv", "budget_usd": 1})
        t = op.TASKS[nc.out[-1]["task"]["id"]]
        await op.RUN[t["id"]]
        assert t["status"] == "plan_ready" and len(t["phases"]) == 2 and t["estimate_usd"] == 0.05
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "approve_plan", "budget_usd": 0.5, "pause_between": True})
        run = op.RUN[t["id"]]
        for _ in range(100):  # phase 1 then pause
            await asyncio.sleep(0.05)
            if t["status"] == "paused":
                break
        assert t["status"] == "paused" and t["phases"][0]["status"] == "done" and "one row" in t["phases"][0]["summary"]
        kinds = [e["kind"] for e in t["log"]]
        assert "cmd" in kinds and "out" in kinds
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "resume"})
        for _ in range(100):
            await asyncio.sleep(0.05)
            if t["status"] == "needs_you":
                break
        assert t["status"] == "needs_you" and "rm -rf" in t["pending"]["sentence"] and "whole folder" in t["pending"]["why"]
        assert (WORK / "chart.py").read_text() == "print('chart')\n"
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "deny"})
        await run
        assert t["status"] == "done" and (WORK / "data.csv").exists()
        assert any(e["kind"] == "blocked" for e in t["log"])
        assert t["phases"][1]["changed"] == [str(WORK / "chart.py")] and t["phases"][1]["diffs"][0]["diff"].startswith("---")
        assert Path(t["dossier"]).exists() and "Operator: chart the csv" in Path(t["dossier"]).read_text()
        assert t["budget_usd"] == 0.5 and t["cost"] > 0
        # undo phase 2 removes the created file
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "undo", "phase": 1})
        assert nc.out[-1]["ok"] and not (WORK / "chart.py").exists()
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "undo", "phase": 1})
        assert not nc.out[-1]["ok"]
        # list + get + public() hides messages
        await op.on_list(nc, FakeMsg(), {})
        assert nc.out[-1]["tasks"][0]["id"] == t["id"]
        assert "messages" not in op.public(t)
    asyncio.run(go())


def test_budget_stop_and_strict_mode(monkeypatch):
    script = [{"phases": [{"title": "A", "reads": [], "changes": ["x.txt"], "runs": []}], "estimate_usd": 0.01},
              {"say": "w", "actions": [{"tool": "write", "path": "x.txt", "content": "hi"}]},
              {"phase_done": True, "summary": "ok"}]
    monkeypatch.setattr(op, "llm", scripted(script))
    (HOME / "etc" / "operator.conf").write_text(f"ask_every_change=true\ncwd={WORK}\n")

    async def go():
        nc = FakeNC()
        await op.on_create(nc, FakeMsg(), {"task": "write x", "budget_usd": 1})
        t = op.TASKS[nc.out[-1]["task"]["id"]]
        await op.RUN[t["id"]]
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "approve_plan"})
        for _ in range(100):
            await asyncio.sleep(0.05)
            if t["status"] == "needs_you":
                break
        assert t["status"] == "needs_you" and t["pending"]["action"]["tool"] == "write"  # strict: a plain write asks
        await op.on_action(nc, FakeMsg(), {"id": t["id"], "action": "approve"})
        for _ in range(100):
            await asyncio.sleep(0.05)
            if t["status"] in ("stopped", "done", "error"):
                break
        assert t["status"] == "done" and (WORK / "x.txt").read_text() == "hi"

        # budget: fake calls cost $0.01 each; a two-phase plan needs 5 calls, so a $0.05 budget (the minimum) stops it
        two = [{"phases": [{"title": "A", "reads": [], "changes": ["x.txt"], "runs": []}, {"title": "B", "reads": [], "changes": ["x.txt"], "runs": []}]}] + script[1:] + script[1:]
        monkeypatch.setattr(op, "llm", scripted(two))
        await op.on_create(nc, FakeMsg(), {"task": "write again", "budget_usd": 0.05})
        t2 = op.TASKS[nc.out[-1]["task"]["id"]]
        await op.RUN[t2["id"]]
        await op.on_action(nc, FakeMsg(), {"id": t2["id"], "action": "approve_plan"})
        for _ in range(100):
            await asyncio.sleep(0.05)
            if t2["status"] == "needs_you":
                await op.on_action(nc, FakeMsg(), {"id": t2["id"], "action": "approve"})
            if t2["status"] in ("stopped", "done", "error"):
                break
        assert t2["status"] == "stopped" and "budget" in t2["stop_reason"]
    asyncio.run(go())


def test_create_validation():
    async def go():
        nc = FakeNC()
        await op.on_create(nc, FakeMsg(), {"task": "x"})
        assert not nc.out[-1]["ok"]
        await op.on_create(nc, FakeMsg(), {"task": "do a thing", "model": "gpt"})
        assert "model" in nc.out[-1]["error"]
        await op.on_create(nc, FakeMsg(), {"task": "do a thing", "cwd": "/etc"})
        assert "working folder" in nc.out[-1]["error"]
        await op.on_action(nc, FakeMsg(), {"id": "nope", "action": "stop"})
        assert nc.out[-1]["error"] == "no such task"
    asyncio.run(go())


# ------------------------------------------------------------ gateway + CLI + wiring
def test_gateway_op_requires_token_and_settings_flag():
    import gatewayd
    gatewayd.STATE["token"] = "secret"
    st, e = asyncio.run(gatewayd.route("GET", "/api/op", {}, {}, {"x-torus-token": "wrong"}))
    assert st == 401 and "Operator" in e["error"]
    st, e = asyncio.run(gatewayd.route("GET", "/api/op/x/explode", {}, {}, {"x-torus-token": "secret"}))
    assert st == 404
    st, s = asyncio.run(gatewayd.route("POST", "/api/settings", {}, {"op_ask_every_change": True}))
    assert st == 200 and s["op_ask_every_change"] is True
    assert torus_common.read_conf(HOME / "etc" / "operator.conf")["ask_every_change"] == "true"
    st, e = asyncio.run(gatewayd.route("POST", "/api/settings", {}, {"op_ask_every_change": "yes"}))
    assert st == 400


def torus(*args, home):
    env = {**os.environ, "TORUS_HOME": str(home), "PREFIX": "/usr", "PATH": f"{ROOT / 'torus' / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(ROOT / "torus" / "bin" / "torus"), *args], env=env, capture_output=True, text=True, timeout=30)


def test_cli_and_install_wiring(tmp_path):
    r = torus("op", home=tmp_path)
    assert r.returncode != 0 and 'usage: torus op "task"' in (r.stdout + r.stderr)
    r = torus("install", "11", home=tmp_path)
    assert "unknown phase" in (r.stdout + r.stderr)
    src = (ROOT / "torus" / "bin" / "torus").read_text()
    assert 'PHASES="0 1 2 3 4 5 6 7 8 9 10"' in src and "operatord" in src
    assert "10 operatord" in (ROOT / "torus" / "services.txt").read_text()
    man = (ROOT / "torus" / "manifest.txt").read_text()
    assert "bin/operatord.py" in man and "etc/operator.conf" in man
    assert (ROOT / "torus" / "phases" / "10-operator.sh").exists()
    html = (ROOT / "torus" / "files" / "web" / "index.html").read_text()
    for tid in ("nav-op", "op-task-input", "op-plan-button", "op-approve-button", "op-allow-button", "op-deny-button", "op-stop-button", "op-undo-", "settings-op-strict-toggle"):
        assert tid in html, tid
    inst = (ROOT / "torus" / "INSTALL.md").read_text()
    assert "Step 12: Operator" in inst and "torus install 10" in inst and "Termux:API" in inst
