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
                "default_llm": "sonnet",
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



# ─── notes ───
class TestNotes:
    def test_set_note_and_persist(self, api, session_id):
        r = api.post(
            f"{BASE_URL}/api/sessions/{session_id}/notes",
            json={"step_id": "0.1", "note": "TEST_note contents\nline2"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["notes"]["0.1"] == "TEST_note contents\nline2"
        g = api.get(f"{BASE_URL}/api/sessions/{session_id}")
        assert g.json()["notes"]["0.1"] == "TEST_note contents\nline2"

    def test_note_404(self, api):
        r = api.post(
            f"{BASE_URL}/api/sessions/badid/notes",
            json={"step_id": "0.1", "note": "x"},
        )
        assert r.status_code == 404


# ─── pinned ───
class TestPinned:
    def test_get_pinned_defaults(self, api, session_id):
        r = api.get(f"{BASE_URL}/api/sessions/{session_id}/pinned")
        assert r.status_code == 200
        topics = r.json()["topics"]
        assert isinstance(topics, list) and len(topics) >= 1
        names = [t["name"] for t in topics]
        assert "post-quantum-crypto" in names
        pqc = next(t for t in topics if t["name"] == "post-quantum-crypto")
        assert len(pqc["seeds"]) == 2

    def test_put_pinned_and_persist(self, api, session_id):
        payload = {
            "topics": [
                {"name": "TEST_topic-1", "seeds": ["https://a.example", "https://b.example"]},
                {"name": "TEST_topic-2", "seeds": []},
            ]
        }
        r = api.put(f"{BASE_URL}/api/sessions/{session_id}/pinned", json=payload)
        assert r.status_code == 200
        assert r.json()["topics"] == payload["topics"]
        g = api.get(f"{BASE_URL}/api/sessions/{session_id}/pinned")
        assert g.json()["topics"] == payload["topics"]

    def test_pinned_yaml_download(self, api, session_id):
        r = api.get(f"{BASE_URL}/api/sessions/{session_id}/pinned.yaml")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-yaml")
        assert "attachment" in r.headers.get("content-disposition", "")
        assert "pinned.yaml" in r.headers.get("content-disposition", "")
        assert "topics:" in r.text

    def test_pinned_404(self, api):
        r = api.get(f"{BASE_URL}/api/sessions/badid/pinned")
        assert r.status_code == 404


# ─── post-quantum pubkey + manifest (Dilithium3 / ML-DSA-65) ───
import base64

PQ_ALGO = "ML-DSA-65 (Dilithium3)"
PQ_PK_BYTES = 1952
PQ_SIG_BYTES = 3309


class TestPubkey:
    def test_pubkey_shape(self, api):
        r = api.get(f"{BASE_URL}/api/pubkey")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["algo"] == PQ_ALGO
        fp = d["fingerprint"]
        assert isinstance(fp, str) and len(fp) == 12
        assert all(c in "0123456789abcdef" for c in fp)
        pk_b64 = d["public_key_b64"]
        assert isinstance(pk_b64, str)
        raw = base64.b64decode(pk_b64, validate=True)
        assert len(raw) == PQ_PK_BYTES, f"pk size {len(raw)} != {PQ_PK_BYTES}"


class TestManifest:
    def test_export_manifest(self, api, session_id):
        api.post(f"{BASE_URL}/api/sessions/{session_id}/steps", json={"step_id": "0.2", "verified": True})
        api.post(f"{BASE_URL}/api/sessions/{session_id}/opsec", json={"item_id": "opsec-A", "checked": True})
        api.post(f"{BASE_URL}/api/sessions/{session_id}/notes", json={"step_id": "0.2", "note": "TEST_manifest-note"})
        r = api.get(f"{BASE_URL}/api/sessions/{session_id}/manifest")
        assert r.status_code == 200
        assert "attachment" in r.headers.get("content-disposition", "")
        m = r.json()
        assert m["schema"] == "torus.session.manifest/v2"
        sig = m["signature"]
        assert sig["algo"] == PQ_ALGO
        # pk_fp must match /api/pubkey
        pk = api.get(f"{BASE_URL}/api/pubkey").json()
        assert sig["pk_fp"] == pk["fingerprint"]
        raw_sig = base64.b64decode(sig["value"], validate=True)
        assert len(raw_sig) == PQ_SIG_BYTES, f"sig size {len(raw_sig)} != {PQ_SIG_BYTES}"
        sess = m["session"]
        assert "0.2" in sess["verified_steps"]
        assert "opsec-A" in sess["opsec_checked"]
        assert sess["notes"]["0.2"] == "TEST_manifest-note"
        assert "profile" in sess
        assert "pinned_topics" in sess

    def test_import_roundtrip(self, api, session_id):
        # set pinned topics too, so round-trip covers all persisted fields
        api.put(
            f"{BASE_URL}/api/sessions/{session_id}/pinned",
            json={"topics": [{"name": "TEST_roundtrip", "seeds": ["https://x.example"]}]},
        )
        m = api.get(f"{BASE_URL}/api/sessions/{session_id}/manifest").json()
        r = api.post(f"{BASE_URL}/api/sessions/import", json={"manifest": m})
        assert r.status_code == 200, r.text
        new = r.json()
        assert new["id"] != session_id
        assert set(new["verified_steps"]) == set(m["session"]["verified_steps"])
        assert set(new["opsec_checked"]) == set(m["session"]["opsec_checked"])
        assert new["notes"] == m["session"]["notes"]
        assert new["pinned_topics"] == m["session"]["pinned_topics"]

    def test_import_tampered_signature(self, api, session_id):
        m = api.get(f"{BASE_URL}/api/sessions/{session_id}/manifest").json()
        m["session"]["verified_steps"] = m["session"]["verified_steps"] + ["0.9-tampered"]
        r = api.post(f"{BASE_URL}/api/sessions/import", json={"manifest": m})
        assert r.status_code == 400
        assert "signature verification failed" in r.json()["detail"].lower()

    def test_import_foreign_pk_fp(self, api, session_id):
        m = api.get(f"{BASE_URL}/api/sessions/{session_id}/manifest").json()
        m["signature"]["pk_fp"] = "deadbeef1234"
        r = api.post(f"{BASE_URL}/api/sessions/import", json={"manifest": m})
        assert r.status_code == 400
        assert "foreign key" in r.json()["detail"].lower()

    def test_import_missing_signature(self, api):
        r = api.post(
            f"{BASE_URL}/api/sessions/import",
            json={"manifest": {"schema": "torus.session.manifest/v2", "session": {}}},
        )
        assert r.status_code == 400
        assert "missing signature" in r.json()["detail"].lower()

    def test_import_unknown_algo(self, api, session_id):
        m = api.get(f"{BASE_URL}/api/sessions/{session_id}/manifest").json()
        m["signature"]["algo"] = "RSA-9000"
        r = api.post(f"{BASE_URL}/api/sessions/import", json={"manifest": m})
        assert r.status_code == 400
        assert "unsupported signature algo" in r.json()["detail"].lower()
        assert "rsa-9000" in r.json()["detail"].lower()

    def test_import_bad_schema(self, api, session_id):
        m = api.get(f"{BASE_URL}/api/sessions/{session_id}/manifest").json()
        m["schema"] = "torus.session.manifest/v99"
        r = api.post(f"{BASE_URL}/api/sessions/import", json={"manifest": m})
        assert r.status_code == 400
        assert "unsupported manifest schema" in r.json()["detail"].lower()


# ─── PWA static assets (served by frontend; through ingress root) ───
class TestPWA:
    def test_manifest_webmanifest(self):
        root = BASE_URL
        r = requests.get(f"{root}/manifest.webmanifest", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("name", "short_name", "start_url", "display", "icons"):
            assert k in data, f"missing {k}"
        assert data["display"] == "standalone"
        assert isinstance(data["icons"], list) and len(data["icons"]) >= 1

    def test_service_worker(self):
        r = requests.get(f"{BASE_URL}/service-worker.js", timeout=15)
        assert r.status_code == 200
        txt = r.text
        assert "install" in txt and "activate" in txt and "fetch" in txt

    def test_icon(self):
        r = requests.get(f"{BASE_URL}/torus-icon.svg", timeout=15)
        assert r.status_code == 200
        assert "svg" in r.text.lower()
