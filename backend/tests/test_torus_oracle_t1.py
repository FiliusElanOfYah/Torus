# T1 regression suite: Phase 7 oracled via gatewayd (127.0.0.1:7777), torusctl CLI, phases catalog (8001)
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

GW = "http://127.0.0.1:7777"
WEB = "http://localhost:8001"
HOME = Path("/tmp/torus-test")
PINNED = HOME / "etc" / "pinned.yaml"
CONF = HOME / "etc" / "oracle.conf"
PY = "/tmp/tv/bin/python"
CTL = str(HOME / "bin" / "torusctl.py")
H = {"X-Torus": "1"}
IANA = "https://www.iana.org/help/example-domains"
VALID = f"topics:\n  - name: example\n    seeds:\n      - {IANA}\n"


def ctl(*args):
    env = dict(os.environ, TORUS_HOME=str(HOME))
    return subprocess.run([PY, CTL, *args], capture_output=True, text=True, env=env, timeout=60)


def wait_idle(s, limit=150):
    end = time.time() + limit
    while time.time() < end:
        o = s.get(f"{GW}/api/oracle", headers=H, timeout=10).json()
        if not o["running"]:
            return o
        time.sleep(2)
    raise AssertionError("oracle still running")


@pytest.fixture(scope="module")
def s():
    original = PINNED.read_text()
    sess = requests.Session()
    yield sess
    wait_idle(sess)
    PINNED.write_text(original)


# --- status
def test_status_shape(s):
    r = s.get(f"{GW}/api/oracle", headers=H, timeout=10)
    assert r.status_code == 200
    o = r.json()
    for k in ("ok", "enabled", "every_minutes", "last_run", "next_run", "running", "topics"):
        assert k in o, k
    assert o.get("daemon") != "down"
    assert o["ok"] is True and isinstance(o["topics"], list)
    for t in o["topics"]:
        for k in ("name", "seeds", "when", "ok", "fail", "results", "digest"):
            assert k in t


def test_post_without_header_rejected():
    # gateway only enforces X-Torus on POST (CSRF guard); GET is open by design
    r = requests.post(f"{GW}/api/oracle/run", json={"topic": "nope"}, timeout=10)
    assert r.status_code == 403


# --- config
def test_config_invalid_ftp_rejected_and_file_untouched(s):
    before = PINNED.read_text()
    r = s.post(f"{GW}/api/oracle/config", headers=H, json={"yaml": "topics:\n  - name: bad\n    seeds:\n      - ftp://x\n"}, timeout=10)
    assert r.status_code == 400
    assert "ftp://x" in r.json()["error"]
    assert PINNED.read_text() == before


def test_config_duplicate_names_rejected(s):
    y = f"topics:\n  - name: a\n    seeds: [{IANA}]\n  - name: A\n    seeds: [{IANA}]\n"
    r = s.post(f"{GW}/api/oracle/config", headers=H, json={"yaml": y}, timeout=10)
    assert r.status_code == 400


def test_config_garbage_yaml_rejected(s):
    r = s.post(f"{GW}/api/oracle/config", headers=H, json={"yaml": "topics: [\n  - : :"}, timeout=10)
    assert r.status_code == 400


def test_config_valid_saved(s):
    r = s.post(f"{GW}/api/oracle/config", headers=H, json={"yaml": VALID}, timeout=10)
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["topics"] == [{"name": "example", "seeds": [IANA]}]
    text = PINNED.read_text()
    assert '"example"' in text and IANA in text and "example.com/" not in text
    g = s.get(f"{GW}/api/oracle/config", headers=H, timeout=10).json()
    assert g["yaml"] == text and g["error"] == ""


# --- run
def test_run_unknown_topic(s):
    j = s.post(f"{GW}/api/oracle/run", headers=H, json={"topic": "nope"}, timeout=15).json()
    assert j["ok"] is False and "nope" in j["error"]


def test_run_all_then_refused_then_digest(s):
    wait_idle(s)
    j = s.post(f"{GW}/api/oracle/run", headers=H, json={}, timeout=15).json()
    assert j == {"ok": True, "started": True, "topics": 1}
    j2 = s.post(f"{GW}/api/oracle/run", headers=H, json={}, timeout=15).json()
    assert j2["ok"] is False and "already running" in j2["error"]
    o = wait_idle(s)
    t = next(t for t in o["topics"] if t["name"] == "example")
    assert t["results"][0]["url"] == IANA and t["results"][0]["status"] == "ok"
    assert t["ok"] == 1 and t["fail"] == 0
    assert t["digest"] == str(HOME / "vault/topics/example.md")
    md = Path(t["digest"]).read_text()
    assert "# example" in md and IANA in md
    assert o["last_run"] > time.time() - 300


def test_run_single_topic_case_insensitive(s):
    wait_idle(s)
    j = s.post(f"{GW}/api/oracle/run", headers=H, json={"topic": "EXAMPLE"}, timeout=15).json()
    assert j["ok"] is True and j["topics"] == 1
    wait_idle(s)


# --- CLI
def test_ctl_status():
    r = ctl("oracle", "status")
    assert r.returncode == 0, r.stderr
    assert "schedule:" in r.stdout and "example" in r.stdout


def test_ctl_check():
    r = ctl("oracle", "check")
    assert r.returncode == 0 and r.stdout.startswith("OK")


def test_ctl_conf_every_minutes(s):
    try:
        r = ctl("conf", "oracle", "every_minutes=120")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "every_minutes=120" in CONF.read_text()
        assert s.get(f"{GW}/api/oracle", headers=H, timeout=10).json()["every_minutes"] == 120
    finally:
        ctl("conf", "oracle", "every_minutes=60")
    assert s.get(f"{GW}/api/oracle", headers=H, timeout=10).json()["every_minutes"] == 60


# --- regression: vault search
def test_search_regression(s):
    j = s.get(f"{GW}/api/search", params={"q": "example"}, headers=H, timeout=15).json()
    assert len(j.get("results", [])) > 0


# --- web-app backend phase catalog
@pytest.mark.parametrize("n", [7, 8, 9])
def test_phase_points_to_installer(n):
    p = requests.get(f"{WEB}/api/phases/{n}", timeout=10).json()
    assert p["steps"][0]["command"].strip() == f"torus install {n}"
    # phase 8 troubleshooting says "no liboqs ... needed" (negative mention only); never tell user to install it
    assert "install liboqs" not in str(p["steps"]).lower() and "liboqs-python" not in str(p["steps"]).lower()
    if n == 8:
        assert len(p["opsec_checklist"]) == 10


def test_phases_count():
    r = requests.get(f"{WEB}/api/phases", timeout=10).json()
    phases = r if isinstance(r, list) else r.get("phases", r)
    assert len(phases) == 10
