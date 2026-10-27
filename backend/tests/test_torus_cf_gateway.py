"""Cloudflare AI Gateway option: llm.toggle cf_account/cf_gateway -> llmd base URL + cf-aig-authorization header,
airgapd allows the 'cloudflare' key, audit check extra-4, CLI `torus llm gateway` and `torus key set cloudflare`.
Pure unit tests (no mesh needed) plus bash CLI parsing with a throwaway TORUS_HOME.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
sys.path.insert(0, str(BIN))
os.environ.setdefault("TORUS_HOME", "/tmp/torus-cf-test")
import torus_common  # noqa: E402
import llmd  # noqa: E402
import airgapd  # noqa: E402
import torus_audit  # noqa: E402


def test_api_base_direct_when_unset():
    assert torus_common.cf_api_base("", "") == "https://api.anthropic.com/v1"
    assert torus_common.cf_api_base("acct", "") == "https://api.anthropic.com/v1"


def test_api_base_gateway_when_set():
    assert torus_common.cf_api_base(" abc123 ", "torus ") == "https://gateway.ai.cloudflare.com/v1/abc123/torus/anthropic/v1"


def test_llm_settings_reads_cf_keys(tmp_path, monkeypatch):
    tog = tmp_path / "llm.toggle"
    tog.write_text("enabled=true\nmodel=sonnet\ncf_account=acc\ncf_gateway=gw\n")
    monkeypatch.setattr(torus_common, "LLM_TOGGLE", tog)
    s = torus_common.llm_settings()
    assert s["cf_account"] == "acc" and s["cf_gateway"] == "gw"
    assert s["api_base"].endswith("/acc/gw/anthropic/v1")
    tog.write_text("enabled=true\nmodel=sonnet\ncf_account=\ncf_gateway=\n")
    assert torus_common.llm_settings()["api_base"] == "https://api.anthropic.com/v1"


def test_llmd_headers_add_cf_token_only_when_given():
    h = llmd.headers("sk-ant-x")
    assert "cf-aig-authorization" not in h and h["x-api-key"] == "sk-ant-x"
    h = llmd.headers("sk-ant-x", "cfut_tok")
    assert h["cf-aig-authorization"] == "Bearer cfut_tok"


def test_airgapd_allows_cloudflare_key():
    assert {"anthropic", "brave", "cloudflare"} <= airgapd.ALLOWED


def test_audit_check_cf_gateway(monkeypatch):
    monkeypatch.setattr(torus_audit, "llm_settings", lambda: {"cf_account": "", "cf_gateway": ""})
    assert torus_audit.check_cf_gateway()[0] == "pass"
    monkeypatch.setattr(torus_audit, "llm_settings", lambda: {"cf_account": "a", "cf_gateway": "g"})
    monkeypatch.setattr(torus_audit, "key_present", lambda n: False)
    st, msg, fix = torus_audit.check_cf_gateway()
    assert st == "warn" and "Cloudflare" in msg and "torus llm gateway off" in fix
    assert any(cid == "extra-4" for cid, _, _ in torus_audit.CHECKS)
    assert len(torus_audit.CHECKS) == 14


def torus(*args, home):
    env = {**os.environ, "TORUS_HOME": str(home), "PREFIX": "/usr", "PATH": f"{ROOT / 'torus' / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(ROOT / "torus" / "bin" / "torus"), *args], env=env, capture_output=True, text=True, timeout=30)


def test_cli_gateway_usage_and_validation(tmp_path):
    r = torus("llm", "gateway", "onlyone", home=tmp_path)
    assert r.returncode != 0 and "usage: torus llm gateway" in (r.stdout + r.stderr)
    r = torus("llm", "gateway", "acc", "bad name", home=tmp_path)
    assert r.returncode != 0 and "letters, digits" in (r.stdout + r.stderr)


def test_cli_key_set_accepts_cloudflare(tmp_path):
    r = torus("key", "set", "github", home=tmp_path)
    assert r.returncode != 0 and "anthropic|brave|cloudflare" in (r.stdout + r.stderr)
    r = torus("key", "status", home=tmp_path)
    assert "cloudflare key not set" in (r.stdout + r.stderr)


def test_docs_mention_gateway():
    inst = (ROOT / "torus" / "INSTALL.md").read_text()
    assert "Step 7b" in inst and "torus llm gateway" in inst and "torus key set cloudflare" in inst
    assert "torus llm gateway" in (ROOT / "README.md").read_text()
