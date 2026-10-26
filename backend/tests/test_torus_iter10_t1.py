"""T1 iter10: auto-route to Haiku, dossier find, oracled sentence diff + weekly rollup.
Needs the local mesh (FAKE_LLM=1 torus/tests/run_local_mesh.sh start). Serves a page on 127.0.0.1:8111.
Set KEEP_TDIFF=1 to leave the 'tdiff' topic in pinned.yaml (for UI checks); otherwise pinned.yaml is restored.
"""
import http.server
import os
import re
import socketserver
import threading
import time
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
HOME = Path("/tmp/torus-test")
PORT = 8111
URL1 = "https://www.iana.org/help/example-domains"
URL2 = f"http://127.0.0.1:{PORT}/newsource-{int(time.time())}.html"  # never seen before -> real new source

PAGE = {"text": ""}
S1 = "Alpha sentence of the tdiff page talks about orbital mechanics and comets today."
S2 = "Beta sentence of the tdiff page stays exactly the same between every refill run."
S3 = "Gamma sentence of the tdiff page is also stable across all versions of this page."
S1B = "Delta sentence replaced the alpha one and now describes volcanic islands instead."


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        b = f"<html><head><title>TDiff Page</title></head><body><h1>TDiff Page</h1><p>{PAGE['text']}</p></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module", autouse=True)
def page_server():
    socketserver.TCPServer.allow_reuse_address = True
    sv = socketserver.TCPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=sv.serve_forever, daemon=True).start()
    PAGE["text"] = " ".join([S1, S2, S3])
    yield
    sv.shutdown()
    sv.server_close()


@pytest.fixture(scope="session")
def s():
    x = requests.Session()
    x.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
    return x


def wait_job(s, jid, t=60):
    end = time.time() + t
    while time.time() < end:
        j = s.get(f"{BASE}/api/jobs/{jid}").json()
        if j.get("status") in ("done", "failed", "error", "needs_you"):
            return j
        time.sleep(1)
    raise AssertionError(f"job {jid} not done: {j.get('status')}")


def log_text(j):
    return "\n".join(str(x) if not isinstance(x, dict) else str(x.get("msg") or x.get("text") or x) for x in j.get("log", []))


# ---------- Auto-route ----------
class TestAutoRoute:
    @pytest.fixture(scope="class", autouse=True)
    def toggle(self, s):
        tog = HOME / "etc" / "llm.toggle"
        old = tog.read_text()
        r = s.post(f"{BASE}/api/settings", json={"llm_enabled": True, "auto_route": True, "model": "sonnet"})
        assert r.status_code == 200, r.text
        yield
        s.post(f"{BASE}/api/settings", json={"llm_enabled": False, "auto_route": False})
        tog.write_text(old)

    def test_settings_reflect(self, s):
        st = s.get(f"{BASE}/api/settings").json()
        assert st["auto_route"] is True and st["llm_enabled"] is True

    def test_flow(self, s):
        r = s.post(f"{BASE}/api/ask", json={"question": "What are example domains for?", "urls": [URL1], "mode": "urls"})
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        j = wait_job(s, jid)
        assert j["status"] == "done", j["status"]
        assert "auto-route" not in log_text(j), "first answer must not be auto-routed"

        def fu(body, expect_new):
            before = log_text(s.get(f"{BASE}/api/jobs/{jid}").json()).count("auto-route: short follow-up, using Haiku")
            r = s.post(f"{BASE}/api/jobs/{jid}/followup", json=body)
            assert r.status_code == 200, r.text
            jj = wait_job(s, jid)
            assert jj["status"] == "done"
            after = log_text(jj).count("auto-route: short follow-up, using Haiku")
            assert after == before + (1 if expect_new else 0), (body, before, after)
            return jj

        fu({"question": "and why?"}, True)
        fu({"question": "and why with opus?", "model": "opus"}, False)
        fu({"question": "what about this?", "urls": [URL2]}, False)
        fu({"question": "x" * 300}, False)
        fu({"question": "a" * 240}, True)  # boundary: exactly 240 chars is still short
        fu({"question": "re-cite the old one", "urls": [URL1]}, True)  # existing URL is not new
        s.post(f"{BASE}/api/settings", json={"auto_route": False})
        fu({"question": "and why again?"}, False)

    def test_followup_validation(self, s):
        r = s.post(f"{BASE}/api/settings", json={"model": "gpt"})
        assert r.status_code == 400


# ---------- Dossier find ----------
class TestFind:
    def test_bad_inputs(self, s):
        assert s.get(f"{BASE}/api/vault/find", params={"q": "k"}).status_code == 400
        assert s.get(f"{BASE}/api/vault/find", params={"q": "klein", "dir": "secret"}).status_code == 400
        assert s.get(f"{BASE}/api/vault/find", params={"q": "../etc", "dir": "../etc"}).status_code == 400

    def test_klein_hits_and_idx(self, s):
        r = s.get(f"{BASE}/api/vault/find", params={"q": "klein bottle"})
        assert r.status_code == 200
        hits = r.json()["hits"]
        assert hits, "expected hits for klein bottle"
        alls = [h["all"] for h in hits]
        assert alls == sorted(alls, reverse=True), "all=true hits must come first"
        assert hits[0]["all"] is True
        for h in hits[:8]:
            assert not h["para"].startswith("- ") and not h["para"].startswith("#"), h["para"][:40]
            d = s.get(f"{BASE}/api/vault/doc", params={"path": h["rel"]}).json()
            paras = re.split(r"\n{2,}", d["text"])
            assert h["idx"] < len(paras), (h["rel"], h["idx"], len(paras))
            low = paras[h["idx"]].lower()
            assert "klein" in low or "bottle" in low, (h["rel"], h["idx"], paras[h["idx"]][:80])

    def test_dir_restrict_and_nonsense(self, s):
        hits = s.get(f"{BASE}/api/vault/find", params={"q": "klein bottle", "dir": "answers"}).json()["hits"]
        assert all(h["dir"] == "answers" and h["rel"].startswith("answers/") for h in hits)
        r = s.get(f"{BASE}/api/vault/find", params={"q": "zqxjv wqplmk"}).json()
        assert r["hits"] == []


# ---------- Diff + rollup ----------
class TestDiffRollup:
    @pytest.fixture(scope="class", autouse=True)
    def srv(self, s):
        pinned = HOME / "etc" / "pinned.yaml"
        old = pinned.read_text()
        import shutil
        shutil.rmtree(HOME / "var" / "oracle" / "tdiff", ignore_errors=True)  # fresh sentence memory + history
        for p in (HOME / "vault" / "topics").glob("tdiff-week-*.md"):
            p.unlink()
        yield
        if not os.environ.get("KEEP_TDIFF"):
            pinned.write_text(old)

    def run(self, s):
        r = s.post(f"{BASE}/api/oracle/run", json={"topic": "tdiff"})
        assert r.status_code in (200, 202), r.text
        time.sleep(1.5)
        end = time.time() + 60
        while time.time() < end:
            o = s.get(f"{BASE}/api/oracle").json()
            if not o["running"]:
                return next(t for t in o["topics"] if t["name"] == "tdiff")
            time.sleep(1)
        raise AssertionError("oracle still running")

    def test_scenario(self, s):
        PAGE["text"] = " ".join([S1, S2, S3])
        y = f"topics:\n  - name: tdiff\n    seeds:\n      - http://127.0.0.1:{PORT}/tdiff.html\n"
        r = s.post(f"{BASE}/api/oracle/config", json={"yaml": y})
        assert r.status_code == 200, r.text
        t = self.run(s)
        r0 = t["results"][0]
        assert r0["status"] == "ok", r0
        assert r0["changed"] is True and r0["diff"]["first"] is True and r0["diff"]["n_added"] == 3, r0
        assert re.search(r"-week-\d{4}-W\d{2}\.md$", t["rollup"]) and Path(t["rollup"]).exists()

        PAGE["text"] = " ".join([S1B, S2, S3])
        t = self.run(s)
        d = t["results"][0]["diff"]
        assert d["first"] is False and d["added"] == [S1B] and d["removed"] == [S1], d
        dig = Path(t["digest"]).read_text()
        assert "What changed" in dig and S1B in dig and S1 in dig
        ru = Path(t["rollup"]).read_text()
        for needle in ("refills: 2", "changes: 2", "first read", "+1 / -1 sentences"):
            assert needle in ru, needle
        # newest first: the +1/-1 heading precedes 'first read'
        assert ru.index("+1 / -1 sentences") < ru.index("first read")

        t = self.run(s)
        r0 = t["results"][0]
        assert r0["changed"] is False and r0["diff"]["added"] == [S1B]  # the last change stays visible until the next one
        ru = Path(t["rollup"]).read_text()
        assert "refills: 3" in ru and "changes: 2" in ru

        name = Path(t["rollup"]).name
        doc = s.get(f"{BASE}/api/vault/doc", params={"path": f"topics/{name}"})
        assert doc.status_code == 200 and doc.json().get("meta", {}).get("week")
        lst = s.get(f"{BASE}/api/vault/docs", params={"dir": "topics"}).json()
        assert any(name in str(x) for x in lst.get("docs", lst.get("rows", [])))
