"""Model-id verify (torus_models), dossier reader (/api/vault/*), audit badge, oracled change tracking.
Needs the local mesh with the fake Claude:  FAKE_LLM=1 torus/tests/run_local_mesh.sh start
(TORUS_HOME=/tmp/torus-test, venv /tmp/tv). Puts llm.toggle and pinned.yaml back the way it found them.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
HOME = Path("/tmp/torus-test")
ENV = {**os.environ, "TORUS_HOME": str(HOME)}
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "torus" / "files" / "bin"))
os.environ.setdefault("TORUS_HOME", str(HOME))
import torus_models  # noqa: E402

FAKE_MODELS = [{"id": "claude-sonnet-5-5", "created_at": "2026-03-01"}, {"id": "claude-sonnet-5-0", "created_at": "2025-11-01"},
               {"id": "claude-opus-5-5", "created_at": "2026-03-01"}, {"id": "claude-haiku-5-5", "created_at": "2026-03-01"},
               {"id": "claude-haiku-4-5-20251001", "created_at": "2025-10-01"}]


@pytest.fixture(scope="session")
def s():
    x = requests.Session()
    x.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
    return x


@pytest.fixture(scope="module", autouse=True)
def keep_conf():
    tog = HOME / "etc" / "llm.toggle"
    old = tog.read_text() if tog.exists() else None
    key = HOME / "airgap" / "anthropic.key"
    made = not key.exists()
    if made:
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text("sk-ant-test\n")
        os.chmod(key, 0o600)
    yield
    if old is not None:
        tog.write_text(old)
    if made:
        key.unlink()


# ---------------------------------------------------------------- pure logic
def test_family_and_versions():
    assert torus_models.family("claude-sonnet-4-5-20250929") == "sonnet"
    assert torus_models.family("claude-3-5-haiku-20241022") == "haiku"
    assert torus_models.family("gpt-4") is None
    assert torus_models.version_key({"id": "claude-haiku-5-5"}) > torus_models.version_key({"id": "claude-haiku-4-5-20251001"})


def test_check_statuses():
    conf = {"sonnet_id": "claude-sonnet-5-5", "opus_id": "claude-opus-4-1", "haiku_id": "claude-haiku-4-5-20251001"}
    rep = torus_models.check(FAKE_MODELS, conf)
    t = rep["tiers"]
    assert t["sonnet"]["status"] == "ok"
    assert t["opus"]["status"] == "missing" and t["opus"]["newest"] == "claude-opus-5-5"
    assert t["haiku"]["status"] == "outdated" and t["haiku"]["newest"] == "claude-haiku-5-5"
    assert t["haiku"]["available"] == ["claude-haiku-5-5", "claude-haiku-4-5-20251001"]
    assert rep["problems"] == 2
    rep = torus_models.check([m for m in FAKE_MODELS if "opus" not in m["id"]], conf)
    assert rep["tiers"]["opus"]["status"] == "unknown"


def test_set_tier_id_validation():
    with pytest.raises(ValueError):
        torus_models.set_tier_id("sonnet", "gpt-4")
    with pytest.raises(ValueError):
        torus_models.set_tier_id("turbo", "claude-x-1")


# ---------------------------------------------------------------- gateway: models
def test_settings_has_tiers_and_haiku(s):
    st = s.get(f"{BASE}/api/settings").json()
    assert set(st["tiers"]) == {"sonnet", "opus", "haiku"} and st["haiku_id"].startswith("claude-haiku")
    r = s.post(f"{BASE}/api/settings", json={"model": "haiku"}).json()
    assert r["model"] == "haiku" and r["model_id"] == r["haiku_id"]
    r = s.post(f"{BASE}/api/settings", json={"model": "gpt"})
    assert r.status_code == 400 and "haiku" in r.json()["error"]
    s.post(f"{BASE}/api/settings", json={"model": "sonnet"})


def test_ask_accepts_haiku_rejects_other(s):
    r = s.post(f"{BASE}/api/ask", json={"question": "x", "urls": [], "mode": "urls", "model": "gpt"})
    assert r.status_code == 400
    # haiku passes validation (the job itself needs sources, so it ends quickly)
    r = s.post(f"{BASE}/api/ask", json={"question": "TEST_haiku", "urls": ["https://example.com/"], "mode": "urls", "model": "haiku"})
    assert r.status_code == 200 and r.json()["id"]


def test_verify_fixes_ids(s):
    tog = HOME / "etc" / "llm.toggle"
    tog.write_text("enabled=false\nmodel=sonnet\nmax_tokens=4096\nopus_id=claude-opus-4-1\nhaiku_id=claude-haiku-4-5-20251001\n")
    r = s.post(f"{BASE}/api/llm/verify", json={"fix": False})
    if r.status_code == 503:
        pytest.skip("fake Claude not running: FAKE_LLM=1 run_local_mesh.sh start")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["tiers"]["opus"]["status"] == "missing" and rep["tiers"]["haiku"]["status"] == "outdated" and rep["changes"] == []
    assert "opus_id=claude-opus-4-1" in tog.read_text()
    rep = s.post(f"{BASE}/api/llm/verify", json={"fix": True}).json()
    assert {(c["tier"], c["to"]) for c in rep["changes"]} == {("opus", "claude-opus-5-5"), ("haiku", "claude-haiku-5-5")}
    assert rep["problems"] == 0 and rep["settings"]["tiers"]["opus"]["status"] == "ok"
    text = tog.read_text()
    assert "opus_id=claude-opus-5-5" in text and "haiku_id=claude-haiku-5-5" in text
    last = s.get(f"{BASE}/api/llm/verify").json()
    assert last["when"] > 0 and last["tiers"]["haiku"]["current"] == "claude-haiku-5-5"
    assert json.loads((HOME / "var" / "models.json").read_text())["problems"] == 0


def test_set_tier_via_settings_rechecks(s):
    r = s.post(f"{BASE}/api/settings", json={"haiku_id": "claude-haiku-4-5-20251001"}).json()
    assert r["haiku_id"] == "claude-haiku-4-5-20251001"
    if s.get(f"{BASE}/api/llm/verify").json().get("when"):
        assert r["tiers"]["haiku"]["status"] == "outdated"
    r = s.post(f"{BASE}/api/settings", json={"haiku_id": "gpt-4"})
    assert r.status_code == 400
    s.post(f"{BASE}/api/settings", json={"haiku_id": "claude-haiku-5-5"})


def test_models_endpoint_and_cli(s):
    r = s.post(f"{BASE}/api/llm/models", json={})
    if r.status_code == 503:
        pytest.skip("fake Claude not running")
    assert r.json()["models"][0]["id"].startswith("claude-")
    py = "/tmp/tv/bin/python"
    out = subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "verify", "--check-only"], env=ENV, capture_output=True, text=True)
    assert out.returncode == 0 and "sonnet" in out.stdout and "haiku" in out.stdout, out.stdout + out.stderr
    out = subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "models"], env=ENV, capture_output=True, text=True)
    assert out.returncode == 0 and "claude-sonnet-5-5" in out.stdout


# ---------------------------------------------------------------- gateway: dossiers
def test_docs_list_and_counts(s):
    r = s.get(f"{BASE}/api/vault/docs", params={"dir": "answers"}).json()
    assert r["dir"] == "answers" and set(r["counts"]) == {"inbox", "answers", "topics"}
    assert r["docs"], "no answers in the test vault"
    d = r["docs"][0]
    assert d["rel"].startswith("answers/") and d["title"] and d["mtime"] > 0 and d["bytes"] > 0
    assert r["docs"] == sorted(r["docs"], key=lambda x: x["mtime"], reverse=True)
    r = s.get(f"{BASE}/api/vault/docs", params={"dir": "inbox"}).json()
    assert all(not d["name"].endswith(".summary.md") for d in r["docs"])
    assert any(d["has_summary"] for d in r["docs"])
    assert s.get(f"{BASE}/api/vault/docs", params={"dir": "secret"}).status_code == 400


def test_doc_read_answer_and_inbox(s):
    a = s.get(f"{BASE}/api/vault/docs", params={"dir": "answers"}).json()["docs"][0]
    d = s.get(f"{BASE}/api/vault/doc", params={"path": a["rel"]}).json()
    assert d["title"] == a["title"] and d["text"].startswith("# ") and d["meta"].get("job") and not d["text"].startswith("---")
    i = next(x for x in s.get(f"{BASE}/api/vault/docs", params={"dir": "inbox"}).json()["docs"] if x["has_summary"])
    d = s.get(f"{BASE}/api/vault/doc", params={"path": i["rel"]}).json()
    assert d["meta"]["url"].startswith("http") and "summary" in d and d["summary"].startswith("# Summary")


@pytest.mark.parametrize("bad", ["inbox/../../etc/llm.toggle", "../etc/x.md", "etc/llm.toggle", "inbox/.hidden.md",
                                 "answers/nope.md", "inbox/a/b.md", ""])
def test_doc_path_safety(s, bad):
    r = s.get(f"{BASE}/api/vault/doc", params={"path": bad})
    assert r.status_code == 404, (bad, r.text)


# ---------------------------------------------------------------- audit badge + oracle change tracking
def test_health_reports_audit_failures(s):
    h = s.get(f"{BASE}/api/health").json()
    assert "audit_fail" in h and isinstance(h["audit_fail"], int)
    try:
        saved = json.loads((HOME / "var" / "audit.json").read_text())
        assert h["audit_fail"] == saved["counts"].get("fail", 0)
    except FileNotFoundError:
        assert h["audit_fail"] == 0


def test_oracle_tracks_changes(s):
    pinned = HOME / "etc" / "pinned.yaml"
    old = pinned.read_text()
    state = HOME / "var" / "oracle.json"
    st = json.loads(state.read_text()) if state.exists() else {}
    st.setdefault("topics", {}).pop("changetest", None)
    try:
        s.post(f"{BASE}/api/oracle/config", json={"yaml": "topics:\n  - name: changetest\n    seeds:\n      - https://www.iana.org/help/example-domains\n"})
        for attempt in range(2):
            r = s.post(f"{BASE}/api/oracle/run", json={"topic": "changetest"}).json()
            assert r["ok"], r
            for _ in range(60):
                time.sleep(2)
                o = s.get(f"{BASE}/api/oracle").json()
                if not o["running"]:
                    break
            t = next(x for x in o["topics"] if x["name"] == "changetest")
            res = t["results"][0]
            assert res["status"] == "ok" and len(res["hash"]) == 16
            if attempt == 0:
                assert res["changed"] is True and t["changed"] == 1 and t["changed_at"] >= t["when"] - 1
                first_changed_at = t["changed_at"]
            else:
                assert res["changed"] is False and t["changed"] == 0 and t["changed_at"] == first_changed_at
        md = (HOME / "vault" / "topics" / "changetest.md").read_text()
        assert "changed: 0" in md and "0 changed since the last refill" in md
    finally:
        pinned.write_text(old)
