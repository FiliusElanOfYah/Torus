"""Phase 7 oracled: pinned-topic refill.
Needs the local mesh: torus/tests/run_local_mesh.sh start   (TORUS_HOME=/tmp/torus-test, venv /tmp/tv)
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
import oracled  # noqa: E402

GOOD = "https://www.iana.org/help/example-domains"


@pytest.fixture(scope="session")
def s():
    x = requests.Session()
    x.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
    return x


def wait_idle(s, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        o = s.get(f"{BASE}/api/oracle").json()
        if not o["running"] and o["last_run"]:
            return o
        time.sleep(2)
    raise AssertionError("refill did not finish")


# ---------------------------------------------------------------- parser (no daemon needed)
def test_parse_web_app_yaml_dump():
    text = "topics:\n- name: post-quantum-crypto\n  seeds:\n  - https://openquantumsafe.org/\n  - https://pq-crystals.org/\n- name: empty\n  seeds: []\n"
    t = oracled.parse_pinned(text)
    assert t == [{"name": "post-quantum-crypto", "seeds": ["https://openquantumsafe.org/", "https://pq-crystals.org/"]},
                 {"name": "empty", "seeds": []}]


def test_parse_indented_quoted_and_flow():
    text = '# c\ntopics:\n  - name: "a b"\n    seeds:\n      - https://a.example/  # trailing\n  - name: c\n    seeds: [https://c.example/, https://d.example/]\n'
    t = oracled.parse_pinned(text)
    assert t[0] == {"name": "a b", "seeds": ["https://a.example/"]}
    assert t[1]["seeds"] == ["https://c.example/", "https://d.example/"]


@pytest.mark.parametrize("bad", ["topics:\n  - name: x\n    seeds:\n      - ftp://x\n",
                                 "topics:\n  - name: x\n  - name: x\n", "topics:\n  - seeds:\n      - https://x/\n"])
def test_parse_rejects(bad):
    with pytest.raises(Exception):
        oracled.parse_pinned(bad)


def test_dump_roundtrip():
    topics = [{"name": "q \"uoted\"", "seeds": ["https://x.example/a"]}, {"name": "none", "seeds": []}]
    assert oracled.parse_pinned(oracled.dump_pinned(topics)) == topics


# ---------------------------------------------------------------- gateway + daemon
def test_config_get_and_save(s):
    r = s.post(f"{BASE}/api/oracle/config", json={"yaml": "topics:\n  - name: bad\n    seeds:\n      - ftp://x\n"})
    assert r.status_code == 400 and "ftp://x" in r.json()["error"]
    text = f"topics:\n  - name: example\n    seeds:\n      - {GOOD}\n      - https://example.com/\n"
    r = s.post(f"{BASE}/api/oracle/config", json={"yaml": text})
    assert r.status_code == 200 and r.json()["topics"][0]["seeds"] == [GOOD, "https://example.com/"]
    c = s.get(f"{BASE}/api/oracle/config").json()
    assert c["error"] == "" and c["topics"][0]["name"] == "example" and "- name:" in c["yaml"]
    assert (HOME / "etc" / "pinned.yaml").read_text().startswith("# TORUS pinned topics")


def test_status_shape(s):
    o = s.get(f"{BASE}/api/oracle").json()
    assert o["ok"] and o["every_minutes"] >= 5 and isinstance(o["topics"], list)
    assert o.get("daemon") != "down", "oracled is not running"
    assert o["topics"][0]["name"] == "example" and len(o["topics"][0]["seeds"]) == 2


def test_run_unknown_topic(s):
    r = s.post(f"{BASE}/api/oracle/run", json={"topic": "nope"}).json()
    assert r["ok"] is False and "nope" in r["error"]


def test_run_refills_and_writes_digest(s):
    r = s.post(f"{BASE}/api/oracle/run", json={}).json()
    assert r["ok"] and r["started"]
    o = wait_idle(s)
    t = o["topics"][0]
    assert t["when"] > 0 and t["ok"] >= 1, t
    good = next(x for x in t["results"] if x["url"] == GOOD)
    assert good["status"] == "ok" and good["title"] and good["chars"] > 100
    digest = Path(t["digest"])
    assert digest == HOME / "vault" / "topics" / "example.md" and digest.exists()
    md = digest.read_text()
    assert "# example" in md and GOOD in md and "- " in md
    assert o["next_run"] > o["last_run"] and o["runs"] >= 1
    state = json.loads((HOME / "var" / "oracle.json").read_text())
    assert state["topics"]["example"]["ok"] >= 1
    assert (HOME / "logs" / "oracle.jsonl").exists()


def test_refill_lands_in_index(s):
    r = s.get(f"{BASE}/api/search", params={"q": "example domains documentation"}).json()
    assert any(GOOD in x["url"] for x in r["results"])


def test_second_run_while_running_is_refused(s):
    a = s.post(f"{BASE}/api/oracle/run", json={"topic": "example"}).json()
    assert a["ok"]
    b = s.post(f"{BASE}/api/oracle/run", json={}).json()
    assert b["ok"] is False and "already running" in b["error"]
    wait_idle(s)


def test_ctl_status_and_check():
    py = "/tmp/tv/bin/python"
    r = subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "oracle", "status"], env=ENV, capture_output=True, text=True)
    assert r.returncode == 0 and "example" in r.stdout and "schedule:" in r.stdout, r.stdout + r.stderr
    r = subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "oracle", "check"], env=ENV, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("OK")


def test_conf_interval_via_ctl(s):
    py = "/tmp/tv/bin/python"
    r = subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "conf", "oracle", "every_minutes=120"], env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "every_minutes=120" in (HOME / "etc" / "oracle.conf").read_text()
    time.sleep(1)
    assert s.get(f"{BASE}/api/oracle").json()["every_minutes"] == 120
    subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "conf", "oracle", "every_minutes=60"], env=ENV, capture_output=True)


def test_catalog_phase_7_8_9_point_at_installer():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import phases

    for n in (7, 8, 9):
        p = phases.get_phase(n)
        assert p["steps"][0]["command"] == f"torus install {n}", p["steps"][0]
        assert all("liboqs" not in st["command"] for st in p["steps"])
    assert len(phases.get_phase(8)["opsec_checklist"]) == 10
    assert any("torus oracle" in st["command"] for st in phases.get_phase(7)["steps"])


# ---------------------------------------------------------------- pin from an answer
def test_pin_source_creates_topic_and_dedupes(s):
    pinned = HOME / "etc" / "pinned.yaml"
    old = pinned.read_text()
    try:
        r = s.post(f"{BASE}/api/oracle/pin", json={"url": "ftp://x", "topic": "a"})
        assert r.status_code == 400
        r = s.post(f"{BASE}/api/oracle/pin", json={"url": "https://www.iana.org/domains/reserved", "topic": ""})
        assert r.status_code == 400
        r = s.post(f"{BASE}/api/oracle/pin", json={"url": "https://www.iana.org/domains/reserved", "topic": "pin test"}).json()
        assert r["ok"] and r["created"] and not r["already"] and r["seeds"] == ["https://www.iana.org/domains/reserved"]
        assert "pin test" in pinned.read_text()
        r = s.post(f"{BASE}/api/oracle/pin", json={"url": "https://www.iana.org/domains/reserved", "topic": "Pin Test"}).json()
        assert r["already"] and not r["created"] and r["topic"] == "pin test"
        r = s.post(f"{BASE}/api/oracle/pin", json={"url": "https://www.iana.org/help/example-domains", "topic": "pin test"}).json()
        assert not r["created"] and len(r["seeds"]) == 2
        cfg = s.get(f"{BASE}/api/oracle/config").json()
        assert next(t for t in cfg["topics"] if t["name"] == "pin test")["seeds"] == r["seeds"]
        wait_idle(s)
    finally:
        pinned.write_text(old)


def test_unpin_seed_and_topic(s):
    pinned = HOME / "etc" / "pinned.yaml"
    old = pinned.read_text()
    try:
        s.post(f"{BASE}/api/oracle/pin", json={"url": "https://www.iana.org/domains/reserved", "topic": "unpin test"})
        s.post(f"{BASE}/api/oracle/pin", json={"url": "https://www.iana.org/help/example-domains", "topic": "unpin test"})
        wait_idle(s)
        assert s.post(f"{BASE}/api/oracle/unpin", json={"url": "https://x.y/", "topic": "unpin test"}).status_code == 404
        assert s.post(f"{BASE}/api/oracle/unpin", json={"url": "https://x.y/", "topic": "nope"}).status_code == 404
        r = s.post(f"{BASE}/api/oracle/unpin", json={"url": "https://www.iana.org/domains/reserved", "topic": "Unpin Test"}).json()
        assert r["ok"] and r["seeds"] == ["https://www.iana.org/help/example-domains"] and not r["removed_topic"]
        assert "domains/reserved" not in pinned.read_text()
        r = s.post(f"{BASE}/api/oracle/unpin", json={"topic": "unpin test"}).json()
        assert r["removed_topic"]
        assert "unpin test" not in pinned.read_text()
        assert all(t["name"] != "unpin test" for t in s.get(f"{BASE}/api/oracle").json()["topics"])
    finally:
        pinned.write_text(old)
