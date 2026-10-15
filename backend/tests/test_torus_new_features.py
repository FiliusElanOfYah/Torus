"""Pytest regression suite for TORUS phase 7 features:
 - follow-ups on an existing job
 - citation map
 - PDF report
 - code API (projects, run, tasks review/apply/rollback/cancel)
 - path safety on torus.code.write
 - web UI testIDs (checked via plain HTML grep)
"""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
HOME = Path("/tmp/torus-test")
TOKEN = (HOME / "etc" / "gateway.token").read_text().strip()


@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
    return sess


# ---------- helpers ----------
def wait_job(s, jid, want=("done", "needs_you", "failed", "error"), timeout=60):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        j = s.get(f"{BASE}/api/jobs/{jid}").json()
        last = j
        if j.get("status") in want:
            return j
        time.sleep(0.5)
    raise AssertionError(f"job {jid} stuck at {last.get('status')!r}: {last.get('log', [])[-3:]}")


def wait_task(s, tid, want, timeout=60):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        t = s.get(f"{BASE}/api/code/tasks/{tid}", headers={"X-Torus-Token": TOKEN}).json()
        last = t
        if t.get("status") in want:
            return t
        time.sleep(0.4)
    raise AssertionError(f"task {tid} stuck at {last.get('status')!r}: log={last.get('log', [])[-5:]}")


# ---------- 1. Follow-ups ----------
class TestFollowups:
    @pytest.fixture(scope="class")
    def job_id(self, s):
        r = s.post(f"{BASE}/api/ask", json={"question": "TEST_followups base",
                                            "urls": ["https://en.wikipedia.org/wiki/Torus"],
                                            "model": "sonnet"})
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        return wait_job(s, jid)["id"]

    def test_1_base_messages_count(self, s, job_id):
        j = s.get(f"{BASE}/api/jobs/{job_id}").json()
        assert j["status"] == "done"
        # fake_llm answer is "Stub answer citing [1]. Messages seen: 1"
        assert "Messages seen: 1" in j.get("answer", ""), f"got: {j.get('answer')}"

    def test_2_followup_bad_model(self, s, job_id):
        r = s.post(f"{BASE}/api/jobs/{job_id}/followup",
                   json={"question": "what else?", "model": "banana"})
        assert r.status_code == 400

    def test_3_followup_empty_question(self, s, job_id):
        r = s.post(f"{BASE}/api/jobs/{job_id}/followup", json={"question": "   "})
        assert r.status_code == 400

    def test_4_followup_1(self, s, job_id):
        r = s.post(f"{BASE}/api/jobs/{job_id}/followup",
                   json={"question": "TEST follow1 - different angle?"})
        assert r.status_code == 200, r.text
        wait_job(s, job_id)
        j = s.get(f"{BASE}/api/jobs/{job_id}").json()
        fus = j.get("followups") or []
        assert len(fus) == 1
        assert "Messages seen: 3" in (fus[0].get("answer") or ""), fus[0]

    def test_5_followup_2(self, s, job_id):
        r = s.post(f"{BASE}/api/jobs/{job_id}/followup",
                   json={"question": "TEST follow2 - a second one"})
        assert r.status_code == 200
        wait_job(s, job_id)
        j = s.get(f"{BASE}/api/jobs/{job_id}").json()
        fus = j["followups"]
        assert len(fus) == 2
        assert "Messages seen: 5" in fus[1]["answer"], fus[1]

    def test_6_dossier_has_followups(self, s, job_id):
        j = s.get(f"{BASE}/api/jobs/{job_id}").json()
        path = Path(j.get("answer_path") or "")
        assert path.exists(), j
        txt = path.read_text()
        assert "Follow-up: TEST follow1" in txt
        assert "Follow-up: TEST follow2" in txt


# ---------- 2. Citation map ----------
class TestCitationMap:
    @pytest.fixture(scope="class")
    def job_id(self, s):
        urls = ["https://en.wikipedia.org/wiki/Torus",
                "https://en.wikipedia.org/wiki/Klein_bottle",
                "https://en.wikipedia.org/wiki/M%C3%B6bius_strip"]
        r = s.post(f"{BASE}/api/ask", json={"question": "TEST_citations wiki trio",
                                            "urls": urls, "model": "sonnet"})
        assert r.status_code == 200
        return wait_job(s, r.json()["id"], timeout=120)["id"]

    def test_1_citations_shape(self, s, job_id):
        r = s.get(f"{BASE}/api/jobs/{job_id}/citations")
        assert r.status_code == 200, r.text
        c = r.json()
        for k in ("ranking", "edges", "shared", "sources_with_links"):
            assert k in c, f"missing {k}"
        assert isinstance(c["ranking"], list) and len(c["ranking"]) >= 3
        for row in c["ranking"]:
            for k in ("n", "score", "linked_by", "site_linked_by", "answer_cites", "links_out"):
                assert k in row

    def test_2_wiki_pages_cross_link(self, s, job_id):
        c = s.get(f"{BASE}/api/jobs/{job_id}/citations").json()
        page_edges = [e for e in c["edges"] if e.get("kind") == "page"]
        assert page_edges, f"expected page edges between wiki pages, got: {c['edges']}"

    def test_3_shared_excludes_wiki_boilerplate(self, s, job_id):
        c = s.get(f"{BASE}/api/jobs/{job_id}/citations").json()
        bad = ["foundation.wikimedia.org", "wikimediafoundation.org",
               "wikipedia.org/wiki/Help:", "wikipedia.org/wiki/Main_Page",
               "wikipedia.org/wiki/Special:", "donate.wikimedia.org"]
        for sh in c["shared"]:
            u = sh["url"].lower()
            for b in bad:
                assert b.lower() not in u, f"wikimedia boilerplate leaked into shared: {u}"


# ---------- 3. PDF report ----------
class TestReport:
    @pytest.fixture(scope="class")
    def job_id(self, s):
        r = s.post(f"{BASE}/api/ask", json={"question": "TEST_report simple q with emoji \U0001f680 and CJK \u4e2d\u6587",
                                            "urls": ["https://en.wikipedia.org/wiki/Torus"],
                                            "model": "sonnet"})
        assert r.status_code == 200
        jid = wait_job(s, r.json()["id"])["id"]
        # add one follow-up so PDF contains the section
        s.post(f"{BASE}/api/jobs/{jid}/followup", json={"question": "TEST_report follow one"})
        wait_job(s, jid)
        return jid

    def test_1_post_report_saves_to_vault(self, s, job_id):
        r = s.post(f"{BASE}/api/jobs/{job_id}/report", json={})
        assert r.status_code == 200, r.text
        p = r.json().get("path")
        assert p and p.endswith(".pdf")
        pth = Path(p)
        assert pth.exists() and pth.is_file()
        assert str(pth).startswith(str(HOME / "vault" / "reports"))
        assert pth.stat().st_size > 500

    def test_2_get_pdf_bytes(self, s, job_id):
        r = s.get(f"{BASE}/api/jobs/{job_id}/report.pdf")
        assert r.status_code == 200
        assert r.headers.get("Content-Type", "").startswith("application/pdf")
        cd = r.headers.get("Content-Disposition", "")
        assert "attachment" in cd.lower()
        assert r.content.startswith(b"%PDF-")

    def test_3_pdf_contents(self, s, job_id):
        from pypdf import PdfReader
        import io
        r = s.get(f"{BASE}/api/jobs/{job_id}/report.pdf")
        reader = PdfReader(io.BytesIO(r.content))
        assert len(reader.pages) >= 1
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        assert "TEST_report" in text
        assert "Follow-up" in text
        assert "Sources" in text
        # unicode -> '?'
        assert "\U0001f680" not in text and "\u4e2d\u6587" not in text


# ---------- 4. Code API ----------
class TestCode:
    @pytest.fixture(scope="class")
    def headers(self):
        return {"X-Torus": "1", "X-Torus-Token": TOKEN, "Content-Type": "application/json"}

    def test_1_requires_token(self):
        r = requests.get(f"{BASE}/api/code/projects", headers={"X-Torus": "1"})
        assert r.status_code == 401

    def test_2_create_project(self, s, headers):
        r = s.post(f"{BASE}/api/code/projects", json={"name": "demo"}, headers=headers)
        assert r.status_code in (200, 201), r.text
        # idempotent-ish
        r2 = s.get(f"{BASE}/api/code/projects", headers=headers)
        assert r2.status_code == 200
        names = [p["name"] for p in r2.json().get("projects", [])]
        assert "demo" in names, names

    def test_3_bad_project_name(self, s, headers):
        for bad in ("../x", "/abs", ".", ".."):
            r = s.post(f"{BASE}/api/code/projects", json={"name": bad}, headers=headers)
            assert r.status_code >= 400, f"accepted bad name {bad!r}: {r.text}"

    def test_4_quick_run(self, s, headers):
        r = s.post(f"{BASE}/api/code/run",
                   json={"project": "demo", "cmd": "echo hi; false", "timeout": 10},
                   headers=headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "hi" in d.get("output", "")
        assert d.get("exit") == 1
        assert d.get("timed_out") is False

    def test_5_quick_run_timeout(self, s, headers):
        r = s.post(f"{BASE}/api/code/run",
                   json={"project": "demo", "cmd": "sleep 10", "timeout": 5},
                   headers=headers)
        assert r.status_code == 200, r.text
        assert r.json().get("timed_out") is True

    def test_6_auto_fix_loop(self, s, headers):
        r = s.post(f"{BASE}/api/code/tasks",
                   json={"project": "demo", "instruction": "TEST_create a hello.py that prints a greeting",
                         "run_cmd": "python3 hello.py", "max_attempts": 3,
                         "auto_fix": True, "model": "sonnet"},
                   headers=headers)
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        t = wait_task(s, tid, {"review", "failed", "error", "done"}, timeout=60)
        assert t["status"] == "review", f"expected review, got {t['status']}: log={t.get('log')}"
        # proposals[-1].changes[] should have diffs
        p = t["proposals"][-1]
        assert p["changes"] and all("diff" in c for c in p["changes"])
        # apply -> runs exit 1 then after fix exit 0
        r = s.post(f"{BASE}/api/code/tasks/{tid}/review",
                   json={"action": "apply"}, headers=headers)
        assert r.status_code == 200
        t = wait_task(s, tid, {"done", "failed", "error"}, timeout=120)
        assert t["status"] == "done", f"log={t.get('log')[-6:]}"
        exits = [run["exit"] for run in t["runs"]]
        assert exits[0] == 1 and exits[-1] == 0, exits
        return tid

    def test_7_rollback(self, s, headers):
        # make a fresh task, apply, then rollback
        r = s.post(f"{BASE}/api/code/tasks",
                   json={"project": "demo", "instruction": "TEST_rb hello.py",
                         "run_cmd": "", "max_attempts": 1, "auto_fix": False,
                         "model": "sonnet"},
                   headers=headers)
        tid = r.json()["id"]
        t = wait_task(s, tid, {"review", "error"}, timeout=60)
        assert t["status"] == "review"
        s.post(f"{BASE}/api/code/tasks/{tid}/review", json={"action": "apply"}, headers=headers)
        t = wait_task(s, tid, {"done", "failed", "error"}, timeout=60)
        assert t["status"] == "done"
        # rollback
        r = s.post(f"{BASE}/api/code/tasks/{tid}/rollback", json={}, headers=headers)
        assert r.status_code == 200, r.text
        t = s.get(f"{BASE}/api/code/tasks/{tid}", headers=headers).json()
        assert t["status"] == "rolled_back"

    def test_8_cancel_and_bad_review(self, s, headers):
        r = s.post(f"{BASE}/api/code/tasks",
                   json={"project": "demo", "instruction": "TEST_cancel task",
                         "run_cmd": "", "max_attempts": 1, "auto_fix": False},
                   headers=headers)
        tid = r.json()["id"]
        t = wait_task(s, tid, {"review"}, timeout=60)
        # bad action
        r = s.post(f"{BASE}/api/code/tasks/{tid}/review",
                   json={"action": "boom"}, headers=headers)
        assert r.status_code == 400
        # reject
        r = s.post(f"{BASE}/api/code/tasks/{tid}/review",
                   json={"action": "reject"}, headers=headers)
        assert r.status_code == 200
        t = wait_task(s, tid, {"rejected"}, timeout=10)
        assert t["status"] == "rejected"


# ---------- 5. Path safety via nats ----------
class TestPathSafety:
    @pytest.mark.asyncio
    async def test_bad_paths_refused(self):
        pytest.importorskip("nats")
        import nats
        nc = await nats.connect("nats://127.0.0.1:4222")
        try:
            for bad in ("../escape.txt", "/etc/passwd", ".git/HEAD", ".git/config"):
                reply = await nc.request("torus.code.write",
                                         json.dumps({"project": "demo", "task_id": "safetytest",
                                                     "files": [{"path": bad, "content": "x"}]}).encode(),
                                         timeout=5)
                d = json.loads(reply.data)
                assert d.get("ok") is False, f"coderd accepted bad path {bad!r}: {d}"
        finally:
            await nc.close()


# ---------- 6. Web UI testIDs ----------
class TestUITestIds:
    REQUIRED_TESTIDS = [
        "code-locked", "code-project-select", "code-new-project-input",
        "code-new-project-button", "code-instruction-input", "code-run-command-input",
        "code-focus-input", "code-attempts-select", "code-model-select",
        "code-autofix-checkbox", "code-task-submit-button",
        "code-quick-run-input", "code-quick-run-button", "code-quick-run-output",
        "code-task-list",
        "code-task-status", "code-proposal", "code-review-note",
        "code-review-apply", "code-review-revise", "code-review-reject",
        "code-task-rollback", "code-task-cancel", "code-task-log",
        "followup-card", "followup-question-input", "followup-urls-input",
        "followup-model-select", "followup-submit-button",
        "citation-map-button", "citation-map", "citation-shared", "citation-add-source",
        "report-save-button", "report-download-link", "report-path",
    ]

    def test_all_testids_present(self):
        html = Path("/tmp/torus-test/web/index.html").read_text()
        missing = [tid for tid in self.REQUIRED_TESTIDS if f'data-testid="{tid}"' not in html]
        assert not missing, f"missing testIDs in index.html: {missing}"
