"""Pytest suite for TORUS phases 8 (hardening) and 9 (export/restore).
Needs the local mesh: torus/tests/run_local_mesh.sh start   (TORUS_HOME=/tmp/torus-test, venv /tmp/tv)
"""
import json
import os
import socket
import subprocess
import tarfile
from pathlib import Path

import pytest
import requests

BASE = "http://127.0.0.1:7777"
HOME = Path("/tmp/torus-test")
PY = "/tmp/tv/bin/python"
TOKEN = (HOME / "etc" / "gateway.token").read_text().strip()
ENV = {**os.environ, "TORUS_HOME": str(HOME), "TORUS_EXPORT_DIR": "/tmp/torus-exports-test"}


def run(*args, env=None):
    return subprocess.run([PY, *args], capture_output=True, text=True, env=env or ENV, timeout=120)


def airgap(name):
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(HOME / "airgap" / "airgap.sock"))
    s.sendall(f"{name}\n".encode())
    out = s.recv(500)
    s.close()
    return out.decode()


@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
    return sess


@pytest.fixture(scope="module", autouse=True)
def key_and_seal():
    k = HOME / "airgap" / "anthropic.key"
    k.write_text("sk-ant-test-1234567890abcdef\n")
    os.chmod(k, 0o600)
    r = run(str(HOME / "bin" / "torus_seal.py"), "seal")
    assert r.returncode == 0, r.stderr
    yield
    run(str(HOME / "bin" / "torus_seal.py"), "seal", "--quiet")


# ---------- seal ----------
def test_seal_verify_ok():
    r = run(str(HOME / "bin" / "torus_seal.py"), "verify")
    assert r.returncode == 0 and "ML-DSA-65" in r.stdout
    man = json.loads((HOME / "airgap" / "MANIFEST.json").read_text())
    assert "airgap/anthropic.key" in man["files"] and "bin/airgapd.py" in man["files"]
    assert oct(os.stat(HOME / "airgap" / "mldsa65.sk").st_mode & 0o777) == "0o600"


def test_airgapd_serves_sealed_key():
    assert airgap("anthropic").strip() == "sk-ant-test-1234567890abcdef"


def test_airgapd_refuses_tampered_key():
    k = HOME / "airgap" / "anthropic.key"
    k.write_text("sk-ant-EVIL\n")
    try:
        out = airgap("anthropic")
        assert out.startswith("ERR sealed") and "changed" in out
        r = run(str(HOME / "bin" / "torus_seal.py"), "verify")
        assert r.returncode == 1 and "anthropic.key" in r.stdout
    finally:
        k.write_text("sk-ant-test-1234567890abcdef\n")
    assert airgap("anthropic").strip() == "sk-ant-test-1234567890abcdef"


def test_airgapd_refuses_tampered_manifest():
    man = HOME / "airgap" / "MANIFEST.json"
    orig = man.read_bytes()
    man.write_bytes(orig.replace(b'"version":1', b'"version":2'))
    try:
        out = airgap("anthropic")
        assert out.startswith("ERR sealed") and "signature" in out
    finally:
        run(str(HOME / "bin" / "torus_seal.py"), "seal", "--quiet")
    assert airgap("anthropic").strip() == "sk-ant-test-1234567890abcdef"


def test_sign_and_verify_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    assert run(str(HOME / "bin" / "torus_seal.py"), "sign", str(f)).returncode == 0
    assert run(str(HOME / "bin" / "torus_seal.py"), "verify-file", str(f)).returncode == 0
    f.write_bytes(b"hellp")
    assert run(str(HOME / "bin" / "torus_seal.py"), "verify-file", str(f)).returncode == 1


# ---------- audit ----------
def test_audit_cli_and_file():
    r = run(str(HOME / "bin" / "torus_audit.py"), "json")
    a = json.loads(r.stdout)
    ids = [i["id"] for i in a["items"]]
    assert ids[:10] == [f"opsec-{n}" for n in range(1, 11)] and len(ids) == 13
    by = {i["id"]: i for i in a["items"]}
    assert by["opsec-3"]["status"] == "pass"
    assert by["opsec-4"]["status"] == "pass"
    assert by["opsec-5"]["status"] == "pass" and "ML-DSA-65" in by["opsec-5"]["detail"]
    assert by["opsec-10"]["status"] == "pass"
    assert by["extra-2"]["status"] == "pass"
    assert by["opsec-1"]["status"] == "skip"  # not Termux here
    assert json.loads((HOME / "var" / "audit.json").read_text())["when"] == a["when"]
    for i in a["items"]:
        if i["status"] != "pass":
            assert i["detail"]


def test_audit_detects_key_leak_and_bad_mode():
    leak = HOME / "logs" / "leak.jsonl"
    leak.write_text('{"x": "sk-ant-test-1234567890abcdef"}\n')
    os.chmod(HOME / "airgap" / "anthropic.key", 0o644)
    try:
        a = json.loads(run(str(HOME / "bin" / "torus_audit.py"), "json").stdout)
        i = next(x for x in a["items"] if x["id"] == "opsec-4")
        assert i["status"] == "fail" and "mode 644" in i["detail"] and "leak.jsonl" in i["detail"]
    finally:
        leak.unlink()
        os.chmod(HOME / "airgap" / "anthropic.key", 0o600)


def test_audit_api(s):
    r = s.post(f"{BASE}/api/audit", json={})
    assert r.status_code == 200 and r.json()["counts"]["pass"] >= 8
    g = s.get(f"{BASE}/api/audit").json()
    assert g["when"] == r.json()["when"] and len(g["items"]) == 13


def test_boot_hook_turns_llm_off_and_prunes():
    tog = HOME / "etc" / "llm.toggle"
    txt = tog.read_text()
    tog.write_text(txt.replace("enabled=false", "enabled=true") if "enabled=" in txt else txt + "enabled=true\n")
    old = HOME / "logs" / "zz.jsonl.3"
    old.write_text("x")
    os.utime(old, (0, 0))
    fresh = HOME / "logs" / "zz.jsonl.1"
    fresh.write_text("x")
    try:
        r = run(str(HOME / "bin" / "torus_audit.py"), "boot")
        assert "llm off" in r.stdout and "pruned 1" in r.stdout
        assert "enabled=false" in tog.read_text()
        assert not old.exists() and fresh.exists()
    finally:
        fresh.unlink(missing_ok=True)
        old.unlink(missing_ok=True)
        tog.write_text(txt)  # put llm.toggle back so the fake LLM stays in use


# ---------- export / restore ----------
def test_export_default_excludes_keys():
    (HOME / "vault" / "inbox").mkdir(parents=True, exist_ok=True)
    (HOME / "vault" / "inbox" / "note.md").write_text("hello\n")
    r = run(str(HOME / "bin" / "torus_export.py"), "export")
    assert r.returncode == 0, r.stderr
    info = json.loads((HOME / "var" / "last_export.json").read_text())
    p = Path(info["path"])
    assert p.exists() and p.with_name(p.name + ".sha256").exists() and p.with_name(p.name + ".sig").exists()
    names = tarfile.open(p).getnames()
    assert ".torus/airgap/mldsa65.pk" in names and ".torus/airgap/MANIFEST.json" in names
    assert not any(n.endswith((".key", "mldsa65.sk")) for n in names)
    assert ".torus/vault/inbox/note.md" in names and ".torus/etc/requirements.freeze" in names
    assert not any("/logs/" in n or "/venv/" in n or "/backups/" in n for n in names)
    assert (HOME / "docs" / "RESTORE.md").exists() and (HOME / "etc" / "requirements.freeze").exists()
    assert run(str(HOME / "bin" / "torus_export.py"), "check", str(p)).returncode == 0
    assert run(str(HOME / "bin" / "torus_seal.py"), "verify-file", str(p)).returncode == 0


def test_export_with_keys_no_vault():
    r = run(str(HOME / "bin" / "torus_export.py"), "export", "--with-keys", "--no-vault")
    assert r.returncode == 0
    p = Path(json.loads((HOME / "var" / "last_export.json").read_text())["path"])
    names = tarfile.open(p).getnames()
    assert ".torus/airgap/anthropic.key" in names and ".torus/airgap/mldsa65.sk" in names
    assert not any(n.startswith(".torus/vault/") for n in names)


def test_export_list_cli_and_api(s):
    r = run(str(HOME / "bin" / "torus_export.py"), "list")
    assert "signed" in r.stdout and "torus-" in r.stdout
    g = s.get(f"{BASE}/api/export")
    assert g.status_code == 200 and isinstance(g.json()["exports"], list)


def test_export_api_needs_token(s):
    assert s.post(f"{BASE}/api/export", json={}).status_code == 401
    r = s.post(f"{BASE}/api/export", json={"no_vault": True}, headers={"X-Torus-Token": TOKEN})
    assert r.status_code == 200 and r.json()["bytes"] > 1000 and r.json()["sig"]
    Path(r.json()["path"]).unlink()


def fresh_export():
    assert run(str(HOME / "bin" / "torus_export.py"), "export", "--no-vault").returncode == 0
    return Path(json.loads((HOME / "var" / "last_export.json").read_text())["path"])


def test_restore_into_fresh_home(tmp_path):
    p = fresh_export()
    fake = tmp_path / "home"
    (fake / ".torus" / "etc").mkdir(parents=True)
    (fake / ".torus" / "etc" / "llm.toggle").write_text("old=1\n")
    env = {**ENV, "HOME": str(fake), "TORUS_HOME": str(fake / ".torus")}
    r = run(str(HOME / "bin" / "torus_export.py"), "restore", str(p), "--yes", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sha256 matches" in r.stdout and "restored" in r.stdout
    assert (fake / ".torus" / "web" / "index.html").exists()
    assert "enabled=" in (fake / ".torus" / "etc" / "llm.toggle").read_text()
    backups = list((fake / ".torus" / "backups").glob("restore-*/.torus/etc/llm.toggle"))
    assert backups and backups[0].read_text() == "old=1\n"
    assert oct(os.stat(fake / ".torus" / "airgap").st_mode & 0o777) == "0o700"


def test_restore_refuses_bad_hash(tmp_path):
    p = fresh_export()
    bad = tmp_path / p.name
    bad.write_bytes(p.read_bytes() + b"x")
    bad.with_name(bad.name + ".sha256").write_text(p.with_name(p.name + ".sha256").read_text())
    r = run(str(HOME / "bin" / "torus_export.py"), "restore", str(bad), "--yes", env={**ENV, "HOME": str(tmp_path)})
    assert r.returncode != 0 and "does NOT match" in r.stdout


# ---------- web page ----------
def test_web_has_hardening_ui():
    html = (HOME / "web" / "index.html").read_text()
    for tid in ("settings-audit", "audit-run", "settings-export", "export-run", "export-with-keys", "audit-summary"):
        assert f'data-testid="{tid}"' in html or f'data-testid="{tid}' in html, tid
    assert "/api/audit" in html and "/api/export" in html
