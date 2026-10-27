# Iteration 13 (T1): live E2E against the running dev mesh (FAKE_LLM=1) on 127.0.0.1:7777 — Operator, spend, gateway, settings
import os
import time
from pathlib import Path

import pytest
import requests

BASE = os.environ.get("TORUS_GATEWAY_URL", "http://127.0.0.1:7777").rstrip("/")
HOME = Path(os.environ.get("TORUS_HOME", "/tmp/torus-test"))
NOTE = Path("/root/op-work/op-note.txt")


def _token():
    try:
        return (HOME / "etc" / "gateway.token").read_text().strip()
    except OSError:
        pytest.skip("no gateway token: mesh not running")


@pytest.fixture(scope="module")
def s():
    try:
        requests.get(BASE + "/", timeout=3)
    except requests.RequestException:
        pytest.skip("gatewayd not answering")
    ss = requests.Session()
    ss.headers.update({"X-Torus-Token": _token(), "X-Torus": "1", "Content-Type": "application/json"})
    return ss


def wait_status(s, tid, want, timeout=20):
    end = time.time() + timeout
    t = None
    while time.time() < end:
        t = s.get(f"{BASE}/api/op/{tid}", timeout=10).json()
        t = t.get("task", t)
        if t.get("status") in want:
            return t
        time.sleep(0.4)
    raise AssertionError(f"status never reached {want}: last={t and t.get('status')}")


def new_task(s, text="look around and write a note"):
    r = s.post(f"{BASE}/api/op", json={"task": text, "budget_usd": 1}, timeout=25)
    assert r.status_code == 200, r.text
    d = r.json()
    t = d.get("task", d)
    assert t.get("id")
    return t


# Operator: auth
def test_op_requires_token():
    r = requests.get(f"{BASE}/api/op", headers={"X-Torus": "1"}, timeout=5)
    assert r.status_code == 401


# Operator: full flow plan -> approve(pause) -> resume -> needs_you -> deny -> undo
def test_op_full_flow(s):
    if NOTE.exists():
        NOTE.unlink()
    t = new_task(s)
    tid = t["id"]
    t = wait_status(s, tid, {"plan_ready"}, 10)
    assert len(t["plan"]["phases"] if "phases" in t.get("plan", {}) else t["phases"]) == 2 or len(t.get("phases", [])) == 2
    est = t.get("estimate_usd", t.get("plan", {}).get("estimate_usd"))
    assert est == pytest.approx(0.02), est
    r = s.post(f"{BASE}/api/op/{tid}/approve_plan", json={"budget_usd": 0.5, "pause_between": True}, timeout=30)
    assert r.status_code == 200, r.text
    t = wait_status(s, tid, {"paused"}, 20)
    r = s.post(f"{BASE}/api/op/{tid}/resume", json={}, timeout=30)
    assert r.status_code == 200, r.text
    t = wait_status(s, tid, {"needs_you"}, 20)
    assert "rm -rf nothing" in t["pending"]["sentence"], t["pending"]
    assert NOTE.exists()
    r = s.post(f"{BASE}/api/op/{tid}/deny", json={}, timeout=30)
    assert r.status_code == 200, r.text
    t = wait_status(s, tid, {"done"}, 20)
    dossier = t.get("dossier") or ""
    assert str(HOME / "vault" / "answers") in dossier and Path(dossier).exists(), dossier
    ch = t["phases"][1].get("changed") or []
    assert any("op-note.txt" in str(c) for c in ch), ch
    assert t["phases"][1].get("diffs") or t["phases"][1].get("diff"), t["phases"][1].keys()
    r = s.post(f"{BASE}/api/op/{tid}/undo", json={"phase": 1}, timeout=30)
    assert r.status_code == 200, r.text
    assert not NOTE.exists()
    r = s.post(f"{BASE}/api/op/{tid}/undo", json={"phase": 1}, timeout=30)
    assert r.status_code == 400, r.text
    lst = s.get(f"{BASE}/api/op", timeout=10).json()
    tasks = lst.get("tasks", lst)
    assert any(x.get("id") == tid for x in tasks)


# Operator: decline a fresh plan -> stopped
def test_op_decline(s):
    tid = new_task(s, "decline me")["id"]
    wait_status(s, tid, {"plan_ready"}, 10)
    r = s.post(f"{BASE}/api/op/{tid}/stop", json={}, timeout=30)
    assert r.status_code == 200, r.text
    assert wait_status(s, tid, {"stopped"}, 10)["status"] == "stopped"


# Operator: bad inputs
def test_op_bad_inputs(s):
    assert s.post(f"{BASE}/api/op", json={"task": ""}, timeout=25).status_code == 400
    assert s.get(f"{BASE}/api/op/nope-nope", timeout=10).status_code in (400, 404)
    assert s.get(f"{BASE}/api/op/abc/approve", timeout=10).status_code == 405


# Settings: strict toggle -> operator.conf and write waits for a tap
def test_op_strict_mode(s):
    conf = HOME / "etc" / "operator.conf"
    try:
        r = s.post(f"{BASE}/api/settings", json={"op_ask_every_change": True}, timeout=10)
        assert r.status_code == 200 and r.json()["op_ask_every_change"] is True
        assert "ask_every_change=true" in conf.read_text()
        tid = new_task(s)["id"]
        wait_status(s, tid, {"plan_ready"}, 10)
        s.post(f"{BASE}/api/op/{tid}/approve_plan", json={"budget_usd": 0.5, "pause_between": False}, timeout=30)
        seen = []
        for _ in range(4):
            t = wait_status(s, tid, {"needs_you", "done", "failed", "stopped"}, 20)
            if t["status"] != "needs_you":
                break
            seen.append(t["pending"].get("tool") or t["pending"].get("action", {}).get("tool"))
            if "write" in seen:
                break
            s.post(f"{BASE}/api/op/{tid}/approve", json={}, timeout=30)
            time.sleep(0.5)
        assert "write" in seen, seen
        s.post(f"{BASE}/api/op/{tid}/stop", json={}, timeout=30)
    finally:
        s.post(f"{BASE}/api/settings", json={"op_ask_every_change": False}, timeout=10)
    assert "ask_every_change=false" in conf.read_text()


# Settings: bad strict value must not half-apply other fields
def test_settings_strict_bad_value_no_half_apply(s):
    before = s.get(f"{BASE}/api/settings", timeout=10).json()["daily_cap_usd"]
    r = s.post(f"{BASE}/api/settings", json={"daily_cap_usd": 7.5, "op_ask_every_change": "yes"}, timeout=10)
    assert r.status_code == 400
    after = s.get(f"{BASE}/api/settings", timeout=10).json()["daily_cap_usd"]
    s.post(f"{BASE}/api/settings", json={"daily_cap_usd": before or 0}, timeout=10)
    assert after == before, f"half-applied: cap {before} -> {after}"


# Spend meter + cap
def test_spend_and_cap(s):
    r = s.get(f"{BASE}/api/spend", timeout=10)
    assert r.status_code == 200, r.text
    d = r.json()
    print(d)
    assert s.post(f"{BASE}/api/settings", json={"daily_cap_usd": "lots"}, timeout=10).status_code == 400
    before = s.get(f"{BASE}/api/settings", timeout=10).json()["daily_cap_usd"]
    r = s.post(f"{BASE}/api/settings", json={"daily_cap_usd": 3}, timeout=10)
    assert r.status_code == 200 and float(r.json()["daily_cap_usd"]) == 3.0
    s.post(f"{BASE}/api/settings", json={"daily_cap_usd": before or 0}, timeout=10)


# Cloudflare gateway toggle
def test_cf_gateway(s):
    assert s.post(f"{BASE}/api/settings", json={"cf_enabled": True, "cf_account": "bad id!", "cf_gateway": "g"}, timeout=10).status_code == 400
    r = s.post(f"{BASE}/api/settings", json={"cf_enabled": True, "cf_account": "acct123", "cf_gateway": "torus-gw"}, timeout=10)
    assert r.status_code == 200 and r.json()["cf_enabled"] is True
    r = s.post(f"{BASE}/api/settings", json={"cf_enabled": False}, timeout=10)
    d = r.json()
    assert d["cf_enabled"] is False and d["cf_saved_account"] == "acct123" and d["cf_saved_gateway"] == "torus-gw"
    r = s.post(f"{BASE}/api/settings", json={"cf_enabled": True}, timeout=10)
    assert r.status_code == 200 and r.json()["cf_account"] == "acct123"
    assert s.post(f"{BASE}/api/settings", json={"cf_enabled": False}, timeout=10).json()["cf_enabled"] is False
