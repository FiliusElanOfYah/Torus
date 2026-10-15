"""TORUS Gateway API tests - exercise HTTP API at 127.0.0.1:7777."""
import os
import time
import json
import pytest
import requests

BASE = "http://127.0.0.1:7777"
H_OK = {"Content-Type": "application/json", "X-Torus": "1"}
H_NO = {"Content-Type": "application/json"}
H_BAD = {"Content-Type": "application/json", "X-Torus": "1", "Host": "evil.com"}


# ---------- security ----------
class TestSecurity:
    def test_health_ok(self):
        r = requests.get(f"{BASE}/api/health", timeout=5)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_post_missing_xtorus_rejected(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_NO,
                          data=json.dumps({"question": "x"}))
        assert r.status_code == 403

    def test_post_bad_host_rejected(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_BAD,
                          data=json.dumps({"question": "x"}))
        assert r.status_code == 403

    def test_get_bad_host_rejected(self):
        r = requests.get(f"{BASE}/api/health", headers={"Host": "evil.com"})
        assert r.status_code == 403


# ---------- validation ----------
class TestAskValidation:
    def test_empty_question_400(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": ""}))
        assert r.status_code == 400

    def test_whitespace_question_400(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "   "}))
        assert r.status_code == 400

    def test_bad_mode_400(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "test", "mode": "garbage"}))
        assert r.status_code == 400

    def test_bad_model_400(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "test", "model": "gpt4"}))
        assert r.status_code == 400

    def test_valid_ask_returns_id(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "What is TORUS?",
                                           "urls": [],
                                           "mode": "urls"}))
        assert r.status_code == 200
        data = r.json()
        assert "id" in data and len(data["id"]) >= 4


# ---------- end-to-end flows ----------
def _poll(job_id, want_statuses, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = requests.get(f"{BASE}/api/jobs/{job_id}", headers={"X-Torus": "1"})
        if r.status_code == 200:
            last = r.json()
            if last.get("status") in want_statuses:
                return last
        time.sleep(1)
    return last


class TestJobsFlow:
    def test_wikipedia_reaches_done_local(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "What is a torus?",
                                           "urls": ["https://en.wikipedia.org/wiki/Torus"],
                                           "mode": "urls"}))
        assert r.status_code == 200
        jid = r.json()["id"]
        final = _poll(jid, {"done", "needs_you", "error"}, timeout=90)
        assert final is not None, "no job state received"
        assert final["status"] == "done", f"got {final.get('status')}: {final.get('error')}"
        assert final.get("engine") == "local", f"engine={final.get('engine')}"
        assert len(final.get("answer", "")) > 20

    def test_example_fresh_needs_you(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "test near-empty",
                                           "urls": ["https://example.com/"],
                                           "mode": "urls",
                                           "fresh": True}))
        assert r.status_code == 200
        jid = r.json()["id"]
        final = _poll(jid, {"needs_you", "done", "error"}, timeout=45)
        assert final is not None
        # expect at least one needs_you source for near-empty page
        srcs = final.get("sources", [])
        statuses = [s.get("status") for s in srcs]
        assert "needs_you" in statuses or final["status"] == "needs_you", \
            f"got statuses={statuses}, job={final.get('status')}"


class TestPasteSkipRerunCancel:
    def _mk_fresh_example_job(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "paste test",
                                           "urls": ["https://example.com/"],
                                           "mode": "urls",
                                           "fresh": True}))
        assert r.status_code == 200
        jid = r.json()["id"]
        final = _poll(jid, {"needs_you", "done"}, timeout=45)
        return jid, final

    def test_paste_marks_ok_and_continues(self):
        jid, final = self._mk_fresh_example_job()
        if final["status"] != "needs_you":
            pytest.skip("example.com unexpectedly reached done without needs_you")
        # find a needs_you source number (1-indexed per gatewayd)
        n = None
        for s in final["sources"]:
            if s.get("status") == "needs_you":
                n = s.get("n")
                break
        assert n is not None
        content = ("<html><body>" + ("TORUS test content paragraph. " * 30) +
                   "</body></html>")
        r = requests.post(f"{BASE}/api/jobs/{jid}/paste", headers=H_OK,
                          data=json.dumps({"n": n, "content": content}))
        assert r.status_code == 200
        final2 = _poll(jid, {"done", "error"}, timeout=60)
        assert final2 is not None and final2["status"] == "done"

    def test_paste_too_short_400(self):
        jid, final = self._mk_fresh_example_job()
        if final["status"] != "needs_you":
            pytest.skip("no needs_you to test short paste")
        r = requests.post(f"{BASE}/api/jobs/{jid}/paste", headers=H_OK,
                          data=json.dumps({"n": final["sources"][0].get("n", 1),
                                           "content": "short"}))
        assert r.status_code == 400

    def test_skip_all(self):
        jid, final = self._mk_fresh_example_job()
        if final["status"] != "needs_you":
            pytest.skip("no needs_you")
        r = requests.post(f"{BASE}/api/jobs/{jid}/skip", headers=H_OK,
                          data=json.dumps({"all": True}))
        assert r.status_code == 200
        final2 = _poll(jid, {"done", "error"}, timeout=60)
        assert final2 is not None and final2["status"] in {"done", "error"}

    def test_cancel(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "cancel me",
                                           "urls": ["https://example.com/"],
                                           "mode": "urls",
                                           "fresh": True}))
        jid = r.json()["id"]
        time.sleep(0.5)
        rc = requests.post(f"{BASE}/api/jobs/{jid}/cancel", headers=H_OK,
                           data=json.dumps({}))
        assert rc.status_code == 200
        time.sleep(1)
        g = requests.get(f"{BASE}/api/jobs/{jid}", headers={"X-Torus": "1"}).json()
        assert g["status"] in {"cancelled", "canceled", "done", "error"}


# ---------- settings ----------
class TestSettings:
    def test_get_settings(self):
        r = requests.get(f"{BASE}/api/settings", headers={"X-Torus": "1"})
        assert r.status_code == 200
        s = r.json()
        for k in ("llm_enabled", "model", "search_mode"):
            assert k in s

    def test_set_search_mode_persists(self):
        r = requests.post(f"{BASE}/api/settings", headers=H_OK,
                          data=json.dumps({"search_mode": "both"}))
        assert r.status_code == 200
        g = requests.get(f"{BASE}/api/settings", headers={"X-Torus": "1"}).json()
        assert g["search_mode"] == "both"
        # file persistence check
        p = "/tmp/torus-test/etc/search.conf"
        if os.path.exists(p):
            assert "both" in open(p).read()
        # reset
        requests.post(f"{BASE}/api/settings", headers=H_OK,
                      data=json.dumps({"search_mode": "urls"}))

    def test_bad_mode_rejected(self):
        r = requests.post(f"{BASE}/api/settings", headers=H_OK,
                          data=json.dumps({"search_mode": "nope"}))
        assert r.status_code == 400

    def test_bad_model_rejected(self):
        r = requests.post(f"{BASE}/api/settings", headers=H_OK,
                          data=json.dumps({"model": "gpt5"}))
        assert r.status_code == 400

    def test_llm_on_without_key_still_finishes(self):
        requests.post(f"{BASE}/api/settings", headers=H_OK,
                      data=json.dumps({"llm_enabled": True}))
        try:
            r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                              data=json.dumps({"question": "llm on no key",
                                               "urls": ["https://en.wikipedia.org/wiki/Torus"],
                                               "mode": "urls"}))
            jid = r.json()["id"]
            final = _poll(jid, {"done", "error"}, timeout=90)
            assert final is not None and final["status"] == "done"
        finally:
            requests.post(f"{BASE}/api/settings", headers=H_OK,
                          data=json.dumps({"llm_enabled": False}))

    def test_brave_mode_no_key_warning_not_crash(self):
        r = requests.post(f"{BASE}/api/ask", headers=H_OK,
                          data=json.dumps({"question": "brave mode test",
                                           "urls": ["https://en.wikipedia.org/wiki/Torus"],
                                           "mode": "both"}))
        assert r.status_code == 200
        jid = r.json()["id"]
        final = _poll(jid, {"done", "needs_you", "error"}, timeout=90)
        assert final is not None
        assert final["status"] in {"done", "needs_you"}


# ---------- listing / search / status ----------
class TestListings:
    def test_jobs_list(self):
        r = requests.get(f"{BASE}/api/jobs", headers={"X-Torus": "1"})
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, (list, dict))

    def test_search_endpoint(self):
        r = requests.get(f"{BASE}/api/search?q=torus", headers={"X-Torus": "1"})
        assert r.status_code == 200

    def test_status_endpoint(self):
        r = requests.get(f"{BASE}/api/status", headers={"X-Torus": "1"})
        assert r.status_code == 200
        data = r.json()
        # Expect list of services
        assert isinstance(data, (list, dict))


# ---------- rerun ----------
class TestRerun:
    def test_rerun_bad_model_400(self):
        # pick most recent done job
        jobs = requests.get(f"{BASE}/api/jobs", headers={"X-Torus": "1"}).json()
        items = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
        done = [j for j in items if j.get("status") == "done"]
        if not done:
            pytest.skip("no done job")
        r = requests.post(f"{BASE}/api/jobs/{done[0]['id']}/rerun", headers=H_OK,
                          data=json.dumps({"model": "garbage"}))
        assert r.status_code in {400, 422}


# ---------- vault / dossiers ----------
class TestVault:
    def test_answers_dir_populated(self):
        p = "/tmp/torus-test/vault/answers"
        assert os.path.isdir(p)
        files = os.listdir(p)
        # the wikipedia test above should have produced at least one
        assert len(files) >= 1, f"no dossiers in {p}"
