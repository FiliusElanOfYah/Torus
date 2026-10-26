"""Auto-route to Haiku, dossier find, oracled sentence diff + weekly rollup.
Needs:  FAKE_LLM=1 torus/tests/run_local_mesh.sh start   (TORUS_HOME=/tmp/torus-test, venv /tmp/tv)
Serves a small changing page on 127.0.0.1:8099 for the diff test. Restores llm.toggle and pinned.yaml.
"""
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
HOME = Path("/tmp/torus-test")
ENV = {**os.environ, "TORUS_HOME": str(HOME)}
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "torus" / "files" / "bin"))
os.environ.setdefault("TORUS_HOME", str(HOME))

PAGE = {"text": ""}
V1 = ("The quick brown fox jumps over the lazy dog near the river bank every morning. "
      "Sentence two stays exactly the same in both versions of this page. "
      "Sentence three is also stable across the versions of this test page.")
V2 = V1.replace("The quick brown fox jumps over the lazy dog near the river bank every morning.",
                "A brand new opening sentence appears at the top of the page after the edit was made.")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        body = f"<html><head><title>Change Test</title></head><body><h1>Change Test</h1><p>{PAGE['text']}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module", autouse=True)
def server_and_conf():
    tog = HOME / "etc" / "llm.toggle"
    pinned = HOME / "etc" / "pinned.yaml"
    old_tog, old_pin = tog.read_text(), pinned.read_text()
    socketserver.TCPServer.allow_reuse_address = True
    PAGE["text"] = V1
    srv = socketserver.TCPServer(("127.0.0.1", 8098), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield
    srv.shutdown()
    tog.write_text(old_tog)
    pinned.write_text(old_pin)


@pytest.fixture(scope="session")
def s():
    x = requests.Session()
    x.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
    return x


def wait_job(s, jid, timeout=60):
    for _ in range(timeout):
        j = s.get(f"{BASE}/api/jobs/{jid}").json()
        if j["status"] in ("done", "error", "cancelled"):
            return j
        time.sleep(1)
    raise AssertionError("job did not finish")


def wait_oracle(s, timeout=90):
    for _ in range(timeout // 2):
        time.sleep(2)
        o = s.get(f"{BASE}/api/oracle").json()
        if not o["running"]:
            return o
    raise AssertionError("refill did not finish")


# ---------------------------------------------------------------- auto-route
def test_auto_route_setting_roundtrip(s):
    r = s.post(f"{BASE}/api/settings", json={"auto_route": True}).json()
    assert r["auto_route"] is True and "auto_route=true" in (HOME / "etc" / "llm.toggle").read_text()
    r = s.post(f"{BASE}/api/settings", json={"auto_route": False}).json()
    assert r["auto_route"] is False


def test_auto_route_short_followup_uses_haiku(s):
    s.post(f"{BASE}/api/settings", json={"llm_enabled": True, "auto_route": True, "model": "sonnet"})
    jid = s.post(f"{BASE}/api/ask", json={"question": "TEST autoroute base", "urls": ["https://www.iana.org/help/example-domains"], "mode": "urls"}).json()["id"]
    j = wait_job(s, jid)
    assert j["status"] == "done" and not any("auto-route" in ln for ln in j["log"]), "first answers must not be auto-routed"
    s.post(f"{BASE}/api/jobs/{jid}/followup", json={"question": "and why is that?"})
    j = wait_job(s, jid)
    assert any("auto-route" in ln and "Haiku" in ln for ln in j["log"]), j["log"]
    n = sum(1 for ln in j["log"] if "auto-route" in ln)
    # a model picked by hand wins
    s.post(f"{BASE}/api/jobs/{jid}/followup", json={"question": "and why is that?", "model": "opus"})
    j = wait_job(s, jid)
    assert sum(1 for ln in j["log"] if "auto-route" in ln) == n
    # new URLs in the follow-up -> full tier
    s.post(f"{BASE}/api/jobs/{jid}/followup", json={"question": "compare with this", "urls": [f"http://127.0.0.1:8098/new-{int(time.time())}.html"]})
    j = wait_job(s, jid)
    assert sum(1 for ln in j["log"] if "auto-route" in ln) == n
    # long follow-up -> full tier
    s.post(f"{BASE}/api/jobs/{jid}/followup", json={"question": "x" * 300})
    j = wait_job(s, jid)
    assert sum(1 for ln in j["log"] if "auto-route" in ln) == n
    # toggle off -> no routing
    s.post(f"{BASE}/api/settings", json={"auto_route": False})
    s.post(f"{BASE}/api/jobs/{jid}/followup", json={"question": "short again?"})
    j = wait_job(s, jid)
    assert sum(1 for ln in j["log"] if "auto-route" in ln) == n
    s.post(f"{BASE}/api/settings", json={"llm_enabled": False})


def test_cli_auto_toggle():
    py = "/tmp/tv/bin/python"
    r = subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "conf", "llm", "auto_route=true"], env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "auto_route=true" in (HOME / "etc" / "llm.toggle").read_text()
    subprocess.run([py, str(HOME / "bin" / "torusctl.py"), "conf", "llm", "auto_route=false"], env=ENV, capture_output=True)


# ---------------------------------------------------------------- find
def test_find_validation(s):
    assert s.get(f"{BASE}/api/vault/find", params={"q": "k"}).status_code == 400
    assert s.get(f"{BASE}/api/vault/find", params={"q": "klein", "dir": "secret"}).status_code == 400


def test_find_hits_point_at_paragraphs(s):
    r = s.get(f"{BASE}/api/vault/find", params={"q": "klein bottle"}).json()
    assert r["hits"], "test vault has no answers mentioning klein bottle"
    h = r["hits"][0]
    assert h["all"] and h["score"] > 0 and h["dir"] in ("answers", "inbox", "topics") and "klein" in h["para"].lower()
    doc = s.get(f"{BASE}/api/vault/doc", params={"path": h["rel"]}).json()
    import re
    paras = re.split(r"\n{2,}", doc["text"])
    assert "klein" in paras[h["idx"]].lower(), "idx must address the paragraph in the served text"
    assert not h["para"].startswith(("- ", "#")), h["para"][:40]
    only = s.get(f"{BASE}/api/vault/find", params={"q": "klein bottle", "dir": "answers"}).json()
    assert all(x["dir"] == "answers" for x in only["hits"])
    assert s.get(f"{BASE}/api/vault/find", params={"q": "zzqqxxnothinghere"}).json()["hits"] == []


# ---------------------------------------------------------------- oracled diff + rollup
def test_sentence_diff_and_weekly_rollup(s):
    import oracled
    import shutil
    topic = {"name": "difftest2", "seeds": ["http://127.0.0.1:8098/page.html"]}
    shutil.rmtree(oracled.SEED_DIR / "difftest2", ignore_errors=True)
    for p in (HOME / "vault" / "topics").glob("difftest2*.md"):
        p.unlink()
    PAGE["text"] = V1
    s.post(f"{BASE}/api/oracle/config", json={"yaml": "topics:\n  - name: difftest2\n    seeds:\n      - http://127.0.0.1:8098/page.html\n"})
    assert s.post(f"{BASE}/api/oracle/run", json={"topic": "difftest2"}).json()["ok"]
    o = wait_oracle(s)
    t = next(x for x in o["topics"] if x["name"] == "difftest2")
    r = t["results"][0]
    assert r["status"] == "ok", r
    assert r["changed"] and r["diff"]["first"] and r["diff"]["n_added"] == 3 and r["diff"]["n_removed"] == 0
    assert t["rollup"].endswith(f"-week-{oracled.week_of(time.time())}.md") and Path(t["rollup"]).exists()
    PAGE["text"] = V2
    assert s.post(f"{BASE}/api/oracle/run", json={"topic": "difftest2"}).json()["ok"]
    o = wait_oracle(s)
    t = next(x for x in o["topics"] if x["name"] == "difftest2")
    r = t["results"][0]
    assert r["changed"] and not r["diff"]["first"]
    assert r["diff"]["added"] == ["A brand new opening sentence appears at the top of the page after the edit was made."]
    assert r["diff"]["removed"] == ["The quick brown fox jumps over the lazy dog near the river bank every morning."]
    digest = Path(t["digest"]).read_text()
    assert "What changed" in digest and "+ A brand new opening sentence" in digest and "− The quick brown fox" in digest
    roll = Path(t["rollup"]).read_text()
    assert "refills: 2" in roll and "changes: 2" in roll and "first read" in roll and "+1 / -1 sentences" in roll
    assert "- + A brand new opening sentence" in roll and "- − The quick brown fox" in roll
    hist = [json.loads(ln) for ln in (oracled.SEED_DIR / "difftest2" / "history.jsonl").read_text().splitlines()]
    assert [h["kind"] for h in hist] == ["change", "refill", "change", "refill"]
    # unchanged third run: no new change entry, rollup counts 3 refills
    assert s.post(f"{BASE}/api/oracle/run", json={"topic": "difftest2"}).json()["ok"]
    o = wait_oracle(s)
    t = next(x for x in o["topics"] if x["name"] == "difftest2")
    assert t["results"][0]["changed"] is False and t["results"][0]["diff"]["added"] == ["A brand new opening sentence appears at the top of the page after the edit was made."]
    assert "refills: 3" in Path(t["rollup"]).read_text() and "changes: 2" in Path(t["rollup"]).read_text()
    # the rollup is readable through the dossier API and listed under topics
    rel = "topics/" + Path(t["rollup"]).name
    d = s.get(f"{BASE}/api/vault/doc", params={"path": rel}).json()
    assert d["meta"]["week"] == oracled.week_of(time.time()) and d["text"].startswith("# difftest2 - week")
    assert any(x["rel"] == rel for x in s.get(f"{BASE}/api/vault/docs", params={"dir": "topics"}).json()["docs"])
    found = s.get(f"{BASE}/api/vault/find", params={"q": "brand new opening", "dir": "topics"}).json()["hits"]
    assert any(h["rel"] == rel for h in found)
