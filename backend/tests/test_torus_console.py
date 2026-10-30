"""Phase 3b: Console agent. One persistent session inside operatord: send -> Claude proposes -> reads run, changes wait
for an inline Allow/Deny, output logged like a terminal; stop, clear, undo edits, hand-off to an Operator task; gateway
routes; CLI/web wiring."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-con-test")
WORK = HOME / "work"
os.environ["TORUS_HOME"] = str(HOME)
sys.path.insert(0, str(BIN))
import operatord as op  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    import shutil
    if HOME.exists():
        shutil.rmtree(HOME)
    for sub in ("etc", "logs", "var/operator/snapshots", "vault/answers", "airgap", "work"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(op, "CONF", HOME / "etc" / "operator.conf")
    monkeypatch.setattr(op, "DIR", HOME / "var" / "operator")
    monkeypatch.setattr(op, "SNAP", HOME / "var" / "operator" / "snapshots")
    monkeypatch.setattr(op, "ANSWERS", HOME / "vault" / "answers")
    monkeypatch.setattr(op, "TERMUX_HOME", HOME)
    monkeypatch.setattr(op, "PROTECTED", [HOME / "airgap", HOME / "etc" / "gateway.token", HOME / "var" / "operator"])
    monkeypatch.setattr(op, "SHARED", [HOME / "shared"])
    (HOME / "etc" / "operator.conf").write_text(f"ask_every_change=false\ndefault_budget_usd=0.5\nmodel=sonnet\nmax_steps=20\ncwd={WORK}\n")
    op.TASKS.clear(); op.EVENTS.clear(); op.RUN.clear()
    yield


class FakeNC:
    def __init__(self):
        self.out = []

    async def publish(self, subject, data):
        self.out.append(json.loads(data))


class Msg:
    reply = "r"


def scripted(replies):
    it = iter(replies)

    async def fake_llm(t, system, messages, max_tokens=4096):
        assert "TORUS Console" in system and str(WORK) in system
        t["calls"] = t.get("calls", 0) + 1
        t["cost"] = round(t.get("cost", 0) + 0.01, 6)
        r = next(it)
        return r(messages) if callable(r) else r
    return fake_llm


async def settle(t, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if t["status"] == "idle":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"console still {t['status']}")


async def wait_needs_you(t, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if t["status"] == "needs_you":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"console is {t['status']}, not needs_you")


def test_session_record_and_list_hides_it():
    c = op.console()
    assert c["id"] == "console" and c["status"] == "idle" and c["budget_usd"] == 0.5 and c["cwd"] == str(WORK)
    assert op.console() is c
    nc = FakeNC()
    asyncio.run(op.on_list(nc, Msg(), {}))
    assert nc.out[-1]["tasks"] == []
    asyncio.run(op.on_console(nc, Msg(), {"action": "get"}))
    assert nc.out[-1]["ok"] and nc.out[-1]["console"]["id"] == "console" and "messages" not in nc.out[-1]["console"]


def test_read_runs_change_waits_for_tap_and_turn_ends(monkeypatch):
    (WORK / "a.txt").write_text("alpha\n")
    monkeypatch.setattr(op, "llm", scripted([
        json.dumps({"say": "listing", "actions": [{"tool": "list", "path": "."}, {"tool": "write", "path": "b.txt", "content": "beta\n"}]}),
        lambda msgs: (lambda last: json.dumps({"say": "done" if "### list ." in last and "wrote 5 chars" in last else "unexpected: " + last[:200]}))(msgs[-1]["content"]),
    ]))
    nc = FakeNC()

    async def flow():
        await op.on_console(nc, Msg(), {"action": "send", "text": "list the folder and add b.txt"})
        assert nc.out[-1]["ok"]
        t = op.TASKS["console"]
        await wait_needs_you(t)
        assert t["pending"]["sentence"].startswith("Write ") or "writes" in t["pending"]["sentence"]
        await op.on_console(nc, Msg(), {"action": "send", "text": "another"})
        assert not nc.out[-1]["ok"] and "busy" in nc.out[-1]["error"]
        await op.on_action(nc, Msg(), {"id": "console", "action": "approve"})
        await settle(t)
        return t
    t = asyncio.run(flow())
    kinds = [e["kind"] for e in t["log"]]
    assert kinds[:3] == ["user", "say", "cmd"] and "ask" in kinds and "tap" in kinds and kinds[-1] == "say"
    assert [e["text"] for e in t["log"] if e["kind"] == "say"][-1] == "done"
    assert (WORK / "b.txt").read_text() == "beta\n" and t["phases"][0]["changed"] == [str(WORK / "b.txt")]
    assert t["calls"] == 2 and t["status"] == "idle" and t["pending"] is None
    assert len(t["messages"]) == 4 and t["messages"][0]["content"] == "list the folder and add b.txt"
    assert (HOME / "var" / "operator" / "console.json").exists()


def test_deny_blocked_and_raw_text_answer(monkeypatch):
    monkeypatch.setattr(op, "llm", scripted([
        json.dumps({"say": "trying", "actions": [{"tool": "delete", "path": "x.txt"}, {"tool": "read", "path": str(HOME / "airgap" / "k")}, {"tool": "shell", "cmd": "echo hi"}]}),
        lambda msgs: "Plain words, no JSON: " + ("ok" if "User DENIED" in msgs[-1]["content"] and "BLOCKED" in msgs[-1]["content"] and "hi" in msgs[-1]["content"] else "bad"),
    ]))
    nc = FakeNC()

    async def flow():
        await op.on_console(nc, Msg(), {"action": "send", "text": "delete x"})
        t = op.TASKS["console"]
        await wait_needs_you(t)
        await op.on_action(nc, Msg(), {"id": "console", "action": "deny"})
        await settle(t)
        return t
    t = asyncio.run(flow())
    texts = [(e["kind"], e["text"]) for e in t["log"]]
    assert ("tap", "denied") in texts and any(k == "blocked" for k, _ in texts)
    assert texts[-1] == ("say", "Plain words, no JSON: ok")
    assert any(k == "cmd" and x == "echo hi" for k, x in texts)


def test_stop_budget_clear_and_undo(monkeypatch):
    async def slow(t, system, messages, max_tokens=4096):
        await asyncio.sleep(10)
    monkeypatch.setattr(op, "llm", slow)
    nc = FakeNC()

    async def flow():
        await op.on_console(nc, Msg(), {"action": "send", "text": "hang"})
        t = op.TASKS["console"]
        await asyncio.sleep(0.05)
        assert t["status"] == "running" and "console" in op.RUN
        await op.on_action(nc, Msg(), {"id": "console", "action": "stop"})
        await settle(t)
        assert t["log"][-1]["kind"] == "stop"
        # budget: the session cap trips -> stop entry with the hint
        (HOME / "etc" / "operator.conf").write_text(f"default_budget_usd=0.05\nmodel=sonnet\ncwd={WORK}\n")

        async def pricey(t2, system, messages, max_tokens=4096):
            t2["cost"] = 0.06
            raise op.BudgetStop("task budget $0.05 reached (spent $0.0600)")
        monkeypatch.setattr(op, "llm", pricey)
        await op.on_console(nc, Msg(), {"action": "send", "text": "more"})
        await settle(t)
        assert "Clear the console" in t["log"][-1]["text"] and t["stop_reason"].startswith("task budget")
        # undo edits: write then undo, console keeps going and can undo again later
        (WORK / "c.txt").write_text("old\n")
        monkeypatch.setattr(op, "llm", scripted([json.dumps({"say": "w", "actions": [{"tool": "write", "path": "c.txt", "content": "new\n"}]}), json.dumps({"say": "ok"})]))
        await op.on_console(nc, Msg(), {"action": "send", "text": "edit c"})
        await wait_needs_you(t)
        await op.on_action(nc, Msg(), {"id": "console", "action": "approve"})
        await settle(t)
        assert (WORK / "c.txt").read_text() == "new\n"
        await op.on_action(nc, Msg(), {"id": "console", "action": "undo", "phase": 0})
        assert nc.out[-1]["ok"] and nc.out[-1]["restored"] == [str(WORK / "c.txt")]
        assert (WORK / "c.txt").read_text() == "old\n" and t["phases"][0]["changed"] == [] and t["phases"][0]["snaps"] == {}
        # clear wipes the session
        await op.on_console(nc, Msg(), {"action": "clear"})
        c = nc.out[-1]["console"]
        assert nc.out[-1]["ok"] and c["log"] == [] and c["cost"] == 0 and op.TASKS["console"] is not t
    asyncio.run(flow())


def test_handoff_to_operator_task(monkeypatch):
    seen = {}

    async def planner(t, system, messages, max_tokens=4096):
        seen["task"] = t
        return json.dumps({"phases": [{"title": "p", "reads": [], "changes": [], "runs": []}], "estimate_usd": 0.1})
    nc = FakeNC()

    async def flow():
        c = op.console()
        op.entry(c, "user", "find the big files under work")
        op.entry(c, "say", "there are three")
        monkeypatch.setattr(op, "llm", planner)
        await op.on_console(nc, Msg(), {"action": "task"})
        assert nc.out[-1]["ok"], nc.out[-1]
        tid = nc.out[-1]["task"]["id"]
        await op.RUN[tid]
        return tid
    tid = asyncio.run(flow())
    t = op.TASKS[tid]
    assert t["task"].startswith("find the big files under work") and "Context from the console:" in t["task"] and "say: there are three" in t["task"]
    assert t["status"] == "plan_ready" and t["cwd"] == str(WORK)
    assert op.TASKS["console"]["log"][-1]["text"].startswith("handed to Operator task")
    asyncio.run(op.on_console(nc, Msg(), {"action": "send", "text": "x", "cwd": str(HOME / "airgap")}))
    assert not nc.out[-1]["ok"] and "not a folder inside" in nc.out[-1]["error"]
    asyncio.run(op.on_console(nc, Msg(), {"action": "bogus"}))
    assert not nc.out[-1]["ok"]


def test_restart_resets_console_to_idle():
    c = op.console()
    c["status"], c["pending"] = "needs_you", {"x": 1}
    op.save(c)
    op.TASKS.clear()
    op.load()
    assert op.TASKS["console"]["status"] == "idle" and op.TASKS["console"]["pending"] is None


def test_wiring():
    gw = (BIN / "gatewayd.py").read_text()
    assert '"/api/op/console(?:/(send|clear|task))?"' in gw and '"torus.op.console"' in gw
    assert '"torus.op.console": on_console' in (BIN / "operatord.py").read_text()
    ctl = (BIN / "torusctl.py").read_text()
    assert 'add_parser("console")' in ctl and "/api/op/console/send" in ctl
    torus = (ROOT / "torus" / "bin" / "torus").read_text()
    assert "console) ctl console" in torus
    web = (ROOT / "torus" / "files" / "web" / "index.html").read_text()
    for tid in ("nav-console", "con-log", "con-input", "con-send-button", "con-allow-button", "con-deny-button", "con-stop-button", "con-task-button", "con-undo-button", "con-clear-button", "con-pending"):
        assert f'data-testid="{tid}"' in web, tid
    assert "TORUS Console" in (ROOT / "torus" / "tests" / "fake_llm.py").read_text()
