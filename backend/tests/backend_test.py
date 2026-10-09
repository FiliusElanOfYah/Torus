"""TORUS backend API tests — all endpoints under /api."""
import io
import os
import zipfile

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get("REACT_APP_BACKEND_URL") else None
if not BASE_URL:
    # fallback: read frontend/.env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def session_id(api):
    r = api.post(f"{BASE_URL}/api/sessions")
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    return sid


# ─── phases ───
class TestPhases:
    def test_list_phases(self, api):
        r = api.get(f"{BASE_URL}/api/phases")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 10
        assert len(data["phases"]) == 10
        p0 = data["phases"][0]
        assert p0["number"] == 0
        assert "name" in p0 and "step_count" in p0
        # phases numbered 0..9
        nums = [p["number"] for p in data["phases"]]
        assert nums == list(range(10))

    def test_get_phase_zero_detail(self, api):
        r = api.get(f"{BASE_URL}/api/phases/0")
        assert r.status_code == 200
        p = r.json()
        assert p["number"] == 0
        assert len(p["steps"]) == 8
        for s in p["steps"]:
            for k in ("id", "title", "command", "expected", "verify", "troubleshooting"):
                assert k in s, f"missing {k} in step {s}"

    def test_get_phase_not_found(self, api):
        r = api.get(f"{BASE_URL}/api/phases/999")
        assert r.status_code == 404

    def test_phase_8_has_opsec(self, api):
        r = api.get(f"{BASE_URL}/api/phases/8")
        assert r.status_code == 200
        p = r.json()
        assert len(p.get("opsec_checklist", [])) == 10


# ─── daemons ───
class TestDaemons:
    def test_list_daemons(self, api):
        r = api.get(f"{BASE_URL}/api/daemons")
        assert r.status_code == 200
        ds = r.json()["daemons"]
        assert len(ds) == 12
        for d in ds:
            for k in ("name", "role", "bus", "ring"):
                assert k in d


# ─── sessions ───
class TestSessions:
    def test_create_session(self, api):
        r = api.post(f"{BASE_URL}/api/sessions")
        assert r.status_code == 200
        s = r.json()
        assert "id" in s and len(s["id"]) >= 32
        assert "profile" in s
        assert s["profile"]["device_name"]

    def test_get_session(self, api, session_id):
        r = api.get(f"{BASE_URL}/api/sessions/{session_id}")
        assert r.status_code == 200
        assert r.json()["id"] == session_id

    def test_get_session_404(self, api):
        r = api.get(f"{BASE_URL}/api/sessions/nonexistent-xyz")
        assert r.status_code == 404

    def test_update_profile(self, api, session_id):
        payload = {
            "profile": {
                "device_name": "TEST_Device",
                "termux_source": "f-droid",
                "android_api": "35",
                "free_disk_gb": "20",
                "acode_installed": True,
                "termux_boot_installed": True,
                "vault_location": "internal",
                "default_llm": "sonnet-4.5",
                "opus_for_code": True,
                "jev_gating": True,
            }
        }
        r = api.patch(f"{BASE_URL}/api/sessions/{session_id}/profile", json=payload)
        assert r.status_code == 200
        assert r.json()["profile"]["device_name"] == "TEST_Device"
        # verify persistence
        g = api.get(f"{BASE_URL}/api/sessions/{session_id}")
        assert g.json()["profile"]["device_name"] == "TEST_Device"

    def test_profile_404(self, api):
        r = api.patch(f"{BASE_URL}/api/sessions/badid/profile", json={"profile": {}})
        assert r.status_code == 404

    def test_toggle_step(self, api, session_id):
        # verify true
        r = api.post(f"{BASE_URL}/api/sessions/{session_id}/steps", json={"step_id": "0.1", "verified": True})
        assert r.status_code == 200
        assert "0.1" in r.json()["verified_steps"]
        # idempotent
        r2 = api.post(f"{BASE_URL}/api/sessions/{session_id}/steps", json={"step_id": "0.1", "verified": True})
        assert r2.json()["verified_steps"].count("0.1") == 1
        # verify false removes
        r3 = api.post(f"{BASE_URL}/api/sessions/{session_id}/steps", json={"step_id": "0.1", "verified": False})
        assert "0.1" not in r3.json()["verified_steps"]

    def test_toggle_opsec(self, api, session_id):
        r = api.post(f"{BASE_URL}/api/sessions/{session_id}/opsec", json={"item_id": "opsec-1", "checked": True})
        assert r.status_code == 200
        assert "opsec-1" in r.json()["opsec_checked"]
        r2 = api.post(f"{BASE_URL}/api/sessions/{session_id}/opsec", json={"item_id": "opsec-1", "checked": False})
        assert "opsec-1" not in r2.json()["opsec_checked"]

    def test_progress(self, api, session_id):
        # mark step 0.1 verified
        api.post(f"{BASE_URL}/api/sessions/{session_id}/steps", json={"step_id": "0.1", "verified": True})
        r = api.get(f"{BASE_URL}/api/sessions/{session_id}/progress")
        assert r.status_code == 200
        data = r.json()
        assert "overall" in data and "phases" in data
        assert data["overall"]["total_steps"] > 0
        assert len(data["phases"]) == 10
        assert data["overall"]["verified_steps"] >= 1

    def test_progress_404(self, api):
        r = api.get(f"{BASE_URL}/api/sessions/badid/progress")
        assert r.status_code == 404


# ─── bundles ───
class TestBundles:
    def test_phase_bundle(self, api):
        r = api.get(f"{BASE_URL}/api/bundle/phase/0")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert "attachment" in r.headers.get("content-disposition", "")
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        assert any(n.endswith("README.md") for n in names)
        assert any(n.endswith("install.sh") for n in names)
        assert any(n.endswith("env.sample") for n in names)

    def test_phase_bundle_404(self, api):
        r = api.get(f"{BASE_URL}/api/bundle/phase/999")
        assert r.status_code == 404

    def test_mesh_bundle(self, api):
        r = api.get(f"{BASE_URL}/api/bundle/mesh")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert len(r.content) > 10 * 1024
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        assert any("bootstrap-all.sh" in n for n in names)
        # all 10 phase folders present
        for i in range(10):
            assert any(f"phase-{i}-" in n for n in names), f"missing phase-{i}"
