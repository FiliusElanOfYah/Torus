# Iteration 9 (T1): model ids verify/fix, tier settings, dossier docs API, audit badge, oracle hashes.
import json
import time
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
HOME = Path("/tmp/torus-test")
TOGGLE = HOME / "etc" / "llm.toggle"
PINNED = HOME / "etc" / "pinned.yaml"
H = {"X-Torus": "1", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def s():
    x = requests.Session()
    x.headers.update(H)
    return x


@pytest.fixture(scope="module", autouse=True)
def restore_toggle():
    orig = TOGGLE.read_text()
    yield
    TOGGLE.write_text(orig)


def conf():
    return dict(l.split("=", 1) for l in TOGGLE.read_text().splitlines() if "=" in l)


# --- settings: tiers + model validation
def test_settings_shape(s):
    r = s.get(f"{BASE}/api/settings")
    assert r.status_code == 200
    d = r.json()
    assert set(d["tiers"]) == {"sonnet", "opus", "haiku"}
    assert d["haiku_id"].startswith("claude-haiku")
    for t in d["tiers"].values():
        assert t["id"].startswith("claude-") and t["role"] and "status" in t


def test_settings_model_haiku_ok_gpt_400(s):
    r = s.post(f"{BASE}/api/settings", json={"model": "haiku"})
    assert r.status_code == 200 and r.json()["model"] == "haiku"
    r = s.post(f"{BASE}/api/settings", json={"model": "gpt"})
    assert r.status_code == 400
    assert s.post(f"{BASE}/api/settings", json={"model": "sonnet"}).status_code == 200


def test_settings_haiku_id(s):
    s.post(f"{BASE}/api/llm/verify", json={"fix": True})  # make sure a report exists
    r = s.post(f"{BASE}/api/settings", json={"haiku_id": "gpt-4"})
    assert r.status_code == 400
    assert conf()["haiku_id"] != "gpt-4"
    r = s.post(f"{BASE}/api/settings", json={"haiku_id": "claude-haiku-4-5-20251001"})
    assert r.status_code == 200
    d = r.json()
    assert d["haiku_id"] == "claude-haiku-4-5-20251001"
    assert d["tiers"]["haiku"]["status"] == "outdated"
    r = s.post(f"{BASE}/api/settings", json={"haiku_id": "claude-haiku-5-5"})
    assert r.json()["tiers"]["haiku"]["status"] == "ok"


# --- verify: dry run then fix
def test_verify_dry_then_fix(s):
    txt = TOGGLE.read_text()
    lines = [l for l in txt.splitlines() if not l.startswith(("opus_id=", "haiku_id="))]
    lines += ["opus_id=claude-opus-4-1", "haiku_id=claude-haiku-4-5-20251001"]
    TOGGLE.write_text("\n".join(lines) + "\n")
    r = s.post(f"{BASE}/api/llm/verify", json={"fix": False}, timeout=70)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tiers"]["opus"]["status"] == "missing"
    assert d["tiers"]["haiku"]["status"] == "outdated"
    assert d["tiers"]["sonnet"]["status"] == "ok"
    assert d["changes"] == []
    assert conf()["opus_id"] == "claude-opus-4-1" and conf()["haiku_id"] == "claude-haiku-4-5-20251001"
    r = s.post(f"{BASE}/api/llm/verify", json={"fix": True}, timeout=70)
    d = r.json()
    ch = {c["tier"]: c for c in d["changes"]}
    assert ch["opus"]["to"] == "claude-opus-5-5" and ch["opus"]["from"] == "claude-opus-4-1"
    assert ch["haiku"]["to"] == "claude-haiku-5-5"
    assert "sonnet" not in ch
    assert conf()["opus_id"] == "claude-opus-5-5" and conf()["haiku_id"] == "claude-haiku-5-5"
    assert d["settings"]["tiers"]["opus"]["status"] == "ok"
    g = s.get(f"{BASE}/api/llm/verify").json()
    assert g["when"] > 0 and {c["tier"] for c in g["changes"]} == {"opus", "haiku"}


def test_models_list(s):
    r = s.post(f"{BASE}/api/llm/models", json={}, timeout=70)
    assert r.status_code == 200
    ms = r.json()["models"]
    ids = [m["id"] for m in ms]
    assert "claude-haiku-5-5" in ids and "claude-opus-5-5" in ids and len(ids) == 6
    for m in ms:
        assert set(m) >= {"id", "display_name", "created_at"}


# --- dossier docs
@pytest.mark.parametrize("folder", ["answers", "inbox", "topics"])
def test_docs_list(s, folder):
    r = s.get(f"{BASE}/api/vault/docs", params={"dir": folder})
    assert r.status_code == 200
    d = r.json()
    assert d["dir"] == folder and set(d["counts"]) == {"answers", "inbox", "topics"}
    assert d["counts"][folder] == len(d["docs"])
    mt = [x["mtime"] for x in d["docs"]]
    assert mt == sorted(mt, reverse=True)
    if folder == "inbox":
        assert not any(x["name"].endswith(".summary.md") for x in d["docs"])
        assert any(x["has_summary"] for x in d["docs"])


def test_docs_bad_dir(s):
    assert s.get(f"{BASE}/api/vault/docs", params={"dir": "other"}).status_code == 400


def test_doc_read(s):
    for folder in ("answers", "inbox", "topics"):
        docs = s.get(f"{BASE}/api/vault/docs", params={"dir": folder}).json()["docs"]
        if not docs:
            continue
        doc = next((x for x in docs if x.get("has_summary")), docs[0])
        r = s.get(f"{BASE}/api/vault/doc", params={"path": doc["rel"]})
        assert r.status_code == 200
        d = r.json()
        assert d["title"] and isinstance(d["meta"], dict)
        assert not d["text"].startswith("---")
        assert d["text"].startswith("# "), d["text"][:80]
        if folder == "inbox" and doc["has_summary"]:
            assert d.get("summary")


@pytest.mark.parametrize("bad", ["inbox/../../etc/llm.toggle", "../x.md", "etc/llm.toggle", "inbox/.h.md", "inbox/a/b.md", "", "answers/nope.md"])
def test_doc_path_safety(s, bad):
    assert s.get(f"{BASE}/api/vault/doc", params={"path": bad}).status_code == 404


# --- health / audit badge
def test_health_audit_fail(s):
    d = s.get(f"{BASE}/api/health").json()
    fail = json.loads((HOME / "var" / "audit.json").read_text())["counts"]["fail"]
    assert d["audit_fail"] == fail


# --- oracle hashes / changed counter
def wait_oracle(s, t=60):
    end = time.time() + t
    while time.time() < end:
        o = s.get(f"{BASE}/api/oracle").json()
        if not o.get("running"):
            return o
        time.sleep(1)
    raise AssertionError("oracle still running")


def test_oracle_changed_zero_on_second_run(s):
    wait_oracle(s)
    for _ in range(2):
        r = s.post(f"{BASE}/api/oracle/run", json={"topic": "example"})
        assert r.json().get("ok"), r.text
        time.sleep(1)
        o = wait_oracle(s)
    top = next(t for t in o["topics"] if t["name"] == "example")
    assert top["changed"] == 0
    hashes = [x.get("hash") for x in top["results"]]
    assert hashes and all(isinstance(h, str) and len(h) == 16 for h in hashes), top["results"]


def test_phases_regression():
    r = requests.get("http://localhost:8001/api/phases")
    assert r.status_code == 200
