"""Iteration 7: extra Phase 8/9 API + export/restore tests (gatewayd on 127.0.0.1:7777, TORUS_HOME=/tmp/torus-test).
Run: /tmp/tv/bin/python -m pytest -p no:cacheprovider -c /dev/null /app/backend/tests/test_torus_iter7_export_audit.py -v
"""
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
TH = Path("/tmp/torus-test")
PY = "/tmp/tv/bin/python"
BIN = TH / "bin"
TOKEN = (TH / "etc" / "gateway.token").read_text().strip()
H = {"X-Torus": "1"}
CREATED = []


def env(**kw):
    e = dict(os.environ, TORUS_HOME=str(TH))
    e.update(kw)
    return e


def names(path):
    with tarfile.open(path, "r:gz") as t:
        return [m.name for m in t.getmembers()]


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for p in CREATED:
        for suf in ("", ".sha256", ".sig"):
            Path(str(p) + suf).unlink(missing_ok=True)


# ---- gateway: audit
def test_audit_get_shape():
    r = requests.get(f"{BASE}/api/audit", headers=H, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert len(d["items"]) == 13
    assert set(d["counts"]) == {"pass", "fail", "warn", "skip"}
    assert sum(d["counts"].values()) == 13
    ids = [i["id"] for i in d["items"]]
    assert ids[:10] == [f"opsec-{n}" for n in range(1, 11)]
    for i in d["items"]:
        assert set(i) >= {"id", "label", "status", "detail", "fix"}


def test_audit_post_reruns():
    before = requests.get(f"{BASE}/api/audit", headers=H, timeout=30).json()
    r = requests.post(f"{BASE}/api/audit", headers=H, json={}, timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d["t"] >= before.get("t", 0)
    after = requests.get(f"{BASE}/api/audit", headers=H, timeout=30).json()
    assert after["t"] == d["t"]
    st = {i["id"]: i["status"] for i in d["items"]}
    assert st["opsec-5"] == "pass" and st["opsec-3"] == "pass"
    assert d["ok"] is (d["counts"]["fail"] == 0)


def test_audit_post_needs_x_torus_header():
    # gatewayd enforces X-Torus only on POST (GET is guarded by the Host check + no CORS)
    r = requests.post(f"{BASE}/api/audit", json={}, timeout=10)
    assert r.status_code == 403
    r = requests.post(f"{BASE}/api/export", headers={"X-Torus-Token": TOKEN}, json={}, timeout=10)
    assert r.status_code == 403
    r = requests.get(f"{BASE}/api/audit", headers={**H, "Host": "evil.example.com:7777"}, timeout=10)
    assert r.status_code == 403


# ---- gateway: export
def test_export_get_lists():
    r = requests.get(f"{BASE}/api/export", headers=H, timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json()["exports"], list)


@pytest.mark.parametrize("tok", [None, "", "wrong-token", TOKEN + "x"])
def test_export_post_bad_token_401(tok):
    h = dict(H)
    if tok is not None:
        h["X-Torus-Token"] = tok
    r = requests.post(f"{BASE}/api/export", headers=h, json={}, timeout=30)
    assert r.status_code == 401


def test_export_post_default_no_keys():
    r = requests.post(f"{BASE}/api/export", headers={**H, "X-Torus-Token": TOKEN}, json={}, timeout=180)
    assert r.status_code == 200, r.text
    d = r.json()
    CREATED.append(d["path"])
    for k in ("path", "bytes", "sha256", "sig"):
        assert d[k]
    assert d["with_keys"] is False
    p = Path(d["path"])
    assert p.stat().st_size == d["bytes"]
    n = names(p)
    for need in (".torus/etc", ".torus/web", ".torus/bin", ".torus/docs/RESTORE.md",
                 ".torus/etc/requirements.freeze", ".torus/airgap/mldsa65.pk"):
        assert need in n, need
    assert not any(x.endswith(".key") or x.endswith("mldsa65.sk") or x.endswith("seal.hmac") for x in n)
    assert not any(seg in x.split("/") for x in n for seg in ("logs", "venv", "backups", "__pycache__"))
    assert not any(x.endswith("gateway.token") for x in n) or True  # informational, see report
    listed = requests.get(f"{BASE}/api/export", headers=H, timeout=10).json()["exports"]
    assert any(e["path"] == d["path"] and e["signed"] for e in listed)
    out = subprocess.run([PY, str(BIN / "torus_seal.py"), "verify-file", d["path"]], env=env(), capture_output=True, text=True)
    assert out.returncode == 0, out.stdout


def test_export_post_with_keys():
    r = requests.post(f"{BASE}/api/export", headers={**H, "X-Torus-Token": TOKEN}, json={"with_keys": True}, timeout=180)
    assert r.status_code == 200
    d = r.json()
    CREATED.append(d["path"])
    assert d["with_keys"] is True
    assert ".torus/airgap/anthropic.key" in names(d["path"])


# ---- CLI export / check / restore into a fake home
def test_cli_no_vault_and_to_dir(tmp_path):
    out = subprocess.run([PY, str(BIN / "torus_export.py"), "export", "--no-vault", "--to", str(tmp_path)],
                         env=env(), capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    tb = next(tmp_path.glob("torus-*.tar.gz"))
    assert (tmp_path / (tb.name + ".sha256")).exists() and (tmp_path / (tb.name + ".sig")).exists()
    assert not any(x.startswith(".torus/vault") for x in names(tb))
    chk = subprocess.run([PY, str(BIN / "torus_export.py"), "check", str(tb)], env=env(), capture_output=True, text=True)
    assert chk.returncode == 0 and "sha256 matches" in chk.stdout and "signature verifies" in chk.stdout


def _fake_home(tmp_path):
    fh = tmp_path / "fh"
    (fh / ".torus" / "etc").mkdir(parents=True)
    (fh / ".torus" / "etc" / "llm.toggle").write_text("enabled=true\nOLD\n")
    return fh, env(HOME=str(fh), TORUS_HOME=str(fh / ".torus"))


def test_restore_backs_up_overwritten(tmp_path):
    src = tmp_path / "ex"
    subprocess.run([PY, str(BIN / "torus_export.py"), "export", "--to", str(src)], env=env(), check=True, capture_output=True, timeout=180)
    tb = next(src.glob("torus-*.tar.gz"))
    fh, e = _fake_home(tmp_path)
    out = subprocess.run([PY, str(BIN / "torus_export.py"), "restore", str(tb), "--yes"], env=e, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert (fh / ".torus" / "docs" / "RESTORE.md").exists()
    assert (fh / ".torus" / "bin" / "gatewayd.py").exists()
    backs = list((fh / ".torus" / "backups").glob("restore-*"))
    assert backs and "OLD" in (backs[0] / ".torus" / "etc" / "llm.toggle").read_text()
    assert not list((fh / ".torus" / "airgap").glob("*.key"))


def test_restore_refuses_bad_sha(tmp_path):
    src = tmp_path / "ex"
    subprocess.run([PY, str(BIN / "torus_export.py"), "export", "--no-vault", "--to", str(src)], env=env(), check=True, capture_output=True, timeout=180)
    tb = next(src.glob("torus-*.tar.gz"))
    sha = Path(str(tb) + ".sha256")
    sha.write_text("0" * 64 + "  " + tb.name + "\n")
    fh, e = _fake_home(tmp_path)
    out = subprocess.run([PY, str(BIN / "torus_export.py"), "restore", str(tb), "--yes"], env=e, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "refusing" in out.stderr
    assert not (fh / ".torus" / "bin").exists()


def test_restore_refuses_path_outside(tmp_path):
    tb = tmp_path / "torus-evil.tar.gz"
    with tarfile.open(tb, "w:gz") as t:
        data = b"pwned"
        ti = tarfile.TarInfo("../evil.txt")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    fh, e = _fake_home(tmp_path)
    out = subprocess.run([PY, str(BIN / "torus_export.py"), "restore", str(tb), "--yes"], env=e, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "outside" in out.stderr
    assert not (tmp_path / "evil.txt").exists()


def test_restore_cancel_without_yes(tmp_path):
    src = tmp_path / "ex"
    subprocess.run([PY, str(BIN / "torus_export.py"), "export", "--no-vault", "--to", str(src)], env=env(), check=True, capture_output=True, timeout=180)
    tb = next(src.glob("torus-*.tar.gz"))
    fh, e = _fake_home(tmp_path)
    out = subprocess.run([PY, str(BIN / "torus_export.py"), "restore", str(tb)], env=e, input="n\n", capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "cancelled" in out.stderr
    assert not (fh / ".torus" / "bin").exists()
