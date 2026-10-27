"""Spend meter + daily cap + gateway status card: torus_spend (prices, cost, summary, cap), llmd refusal past the cap,
gatewayd /api/spend + settings fields (cf_enabled toggle with saved ids, daily_cap_usd), CLI `torus llm cap|spend|gateway on`.
Unit level (no mesh): gatewayd.route is driven directly with a throwaway TORUS_HOME.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-spend-test")
os.environ["TORUS_HOME"] = str(HOME)
sys.path.insert(0, str(BIN))
import torus_common  # noqa: E402
import torus_spend  # noqa: E402
import llmd  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_home(monkeypatch):
    import gatewayd
    for sub in ("etc", "logs", "var", "vault/answers", "vault/inbox", "vault/topics", "web"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    # other suites in the same pytest worker may have imported torus_common with another TORUS_HOME: pin every path
    tog = HOME / "etc" / "llm.toggle"
    for mod in (torus_common, torus_spend, gatewayd):
        monkeypatch.setattr(mod, "LLM_TOGGLE", tog)
    monkeypatch.setattr(torus_spend, "USAGE", HOME / "logs" / "llmd-usage.jsonl")
    monkeypatch.setattr(torus_spend, "PRICES_CONF", HOME / "etc" / "prices.conf")
    monkeypatch.setattr(torus_common, "AIRGAP", HOME / "airgap")
    monkeypatch.setattr(llmd, "LOGS", HOME / "logs")
    tog.write_text("enabled=true\nmodel=sonnet\n")
    for p in (torus_spend.USAGE, torus_spend.PRICES_CONF):
        p.unlink(missing_ok=True)
    yield


def usage_row(model, inp, out, t=None, **extra):
    torus_common.append_jsonl(torus_spend.USAGE, {"t": t or time.time(), "model": model,
                                                  "usage": {"input_tokens": inp, "output_tokens": out}, **extra})


def test_cost_by_family_and_cache():
    p = torus_spend.DEFAULT_PRICES
    assert torus_spend.cost("claude-sonnet-5-5", {"input_tokens": 1_000_000, "output_tokens": 0}, p) == p["sonnet_in"]
    assert torus_spend.cost("claude-haiku-5-5", {"input_tokens": 0, "output_tokens": 1_000_000}, p) == p["haiku_out"]
    assert torus_spend.cost("claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 1000}, p) == pytest.approx((4 + 20) / 1000)
    hit = torus_spend.cost("claude-sonnet-5-5", {"cache_read_input_tokens": 1_000_000}, p)
    assert hit == pytest.approx(p["sonnet_in"] * p["cache_read_factor"])
    assert torus_spend.cost("mystery-model", {"input_tokens": 1_000_000}, p) == p["sonnet_in"]  # unknown priced as sonnet, never free
    assert torus_spend.cost("x", {}) == 0


def test_prices_conf_overrides_and_ignores_junk():
    (HOME / "etc" / "prices.conf").write_text("sonnet_in=3\nhaiku_out=abc\nbogus=9\n")
    p = torus_spend.prices()
    assert p["sonnet_in"] == 3 and p["haiku_out"] == torus_spend.DEFAULT_PRICES["haiku_out"] and "bogus" not in p


def test_summary_today_vs_month_and_cap():
    now = time.time()
    usage_row("claude-sonnet-5-5", 1000, 500)                                  # today: 0.002 + 0.005
    usage_row("claude-haiku-5-5", 10_000, 1000, cached=True)                   # today: 0.001 + 0.0005
    usage_row("claude-sonnet-5-5", 1_000_000, 0, t=torus_spend.day_start(now) - 60)  # yesterday (if same month)
    usage_row("claude-sonnet-5-5", 5, 5, t=now - 40 * 86400)                   # last month: ignored
    torus_spend.USAGE.open("a").write("{not json\n")
    s = torus_spend.summary(now)
    assert s["today"]["calls"] == 2 and s["today"]["usd"] == pytest.approx(0.0085, abs=1e-4)
    assert s["today"]["cached_calls"] == 1 and set(s["today"]["by_model"]) == {"sonnet", "haiku"}
    yesterday_counts = torus_spend.day_start(now) - 60 >= torus_spend.month_start(now)
    assert s["month"]["calls"] == (3 if yesterday_counts else 2)
    assert s["cap_usd"] == 0 and s["cap_hit"] is False and s["cap_left_usd"] is None
    torus_common.write_conf(torus_spend.LLM_TOGGLE, {"daily_cap_usd": "0.005"})
    s = torus_spend.summary(now)
    assert s["cap_hit"] is True and s["cap_left_usd"] == 0
    torus_common.write_conf(torus_spend.LLM_TOGGLE, {"daily_cap_usd": "1"})
    assert torus_spend.summary(now)["cap_left_usd"] == pytest.approx(1 - 0.0085, abs=1e-4)


def test_stored_cost_wins_over_recomputation():
    usage_row("claude-sonnet-5-5", 1_000_000, 0, cost=0.5)
    assert torus_spend.summary()["today"]["usd"] == 0.5


def test_llmd_refuses_past_cap_and_logs_cost(monkeypatch):
    torus_common.write_conf(torus_spend.LLM_TOGGLE, {"daily_cap_usd": "0.01"})
    usage_row("claude-sonnet-5-5", 100_000, 0)  # $0.20 today already
    out = asyncio.run(llmd.call_claude({"prompt": "x"}))
    assert out["ok"] is False and out.get("cap_hit") is True and "torus llm cap" in out["error"]

    torus_common.write_conf(torus_spend.LLM_TOGGLE, {"daily_cap_usd": ""})

    async def secret(n):
        return "sk-ant-A"

    class R:
        status_code = 200
        headers = {"cf-aig-cache-status": "HIT"}

        def json(self):
            return {"content": [{"type": "text", "text": "hi"}], "model": "claude-haiku-5-5",
                    "usage": {"input_tokens": 1000, "output_tokens": 1000}}

    class C:
        async def post(self, url, json, headers):
            return R()

    monkeypatch.setattr(llmd, "get_secret", secret)
    monkeypatch.setattr(llmd, "client", C())
    out = asyncio.run(llmd.call_claude({"prompt": "x", "job_id": "j1"}))
    assert out["ok"] and out["cached"] is True and out["cost"] == pytest.approx(0.0006)
    rows = [json.loads(l) for l in torus_spend.USAGE.read_text().splitlines()]
    assert rows[-1]["cost"] == pytest.approx(0.0006) and rows[-1]["cached"] is True and rows[-1]["job"] == "j1"


@pytest.fixture
def gw(monkeypatch):
    import gatewayd
    return gatewayd


def call(gw, method, path, body=None):
    status, payload = asyncio.run(gw.route(method, path, {}, body or {}))
    return status, payload


def test_api_spend_and_settings_fields(gw):
    usage_row("claude-sonnet-5-5", 1000, 1000)
    st, sp = call(gw, "GET", "/api/spend")
    assert st == 200 and sp["today"]["calls"] == 1 and sp["estimate"] is True and "prices_path" in sp
    st, s = call(gw, "GET", "/api/settings")
    assert s["cf_enabled"] is False and s["daily_cap_usd"] == 0 and s["keys"]["cloudflare"] is False


def test_settings_daily_cap_roundtrip(gw):
    st, s = call(gw, "POST", "/api/settings", {"daily_cap_usd": "2.5"})
    assert st == 200 and s["daily_cap_usd"] == 2.5 and torus_spend.daily_cap() == 2.5
    st, s = call(gw, "POST", "/api/settings", {"daily_cap_usd": 0})
    assert s["daily_cap_usd"] == 0 and torus_common.read_conf(torus_spend.LLM_TOGGLE).get("daily_cap_usd", "") == ""
    st, e = call(gw, "POST", "/api/settings", {"daily_cap_usd": "lots"})
    assert st == 400
    st, s = call(gw, "POST", "/api/settings", {"daily_cap_usd": -4})
    assert s["daily_cap_usd"] == 0


def test_settings_gateway_toggle_remembers_ids(gw):
    st, e = call(gw, "POST", "/api/settings", {"cf_enabled": True})
    assert st == 400 and "Account ID" in e["error"]
    st, e = call(gw, "POST", "/api/settings", {"cf_enabled": True, "cf_account": "abc 123", "cf_gateway": "torus"})
    assert st == 400
    st, s = call(gw, "POST", "/api/settings", {"cf_enabled": True, "cf_account": "abc123", "cf_gateway": "torus"})
    assert st == 200 and s["cf_enabled"] is True and s["cf_account"] == "abc123"
    assert torus_common.llm_settings()["api_base"].endswith("/abc123/torus/anthropic/v1")
    st, s = call(gw, "POST", "/api/settings", {"cf_enabled": False})
    assert s["cf_enabled"] is False and s["cf_saved_account"] == "abc123" and s["cf_saved_gateway"] == "torus"
    assert torus_common.llm_settings()["api_base"] == "https://api.anthropic.com/v1"
    st, s = call(gw, "POST", "/api/settings", {"cf_enabled": True})  # one tap back on, no ids needed
    assert st == 200 and s["cf_enabled"] is True and s["cf_gateway"] == "torus"
    st, e = call(gw, "POST", "/api/settings", {"cf_enabled": "yes"})
    assert st == 400


def torus(*args, home):
    env = {**os.environ, "TORUS_HOME": str(home), "PREFIX": "/usr", "PATH": f"{ROOT / 'torus' / 'bin'}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(ROOT / "torus" / "bin" / "torus"), *args], env=env, capture_output=True, text=True, timeout=30)


def test_cli_cap_validation(tmp_path):
    r = torus("llm", "cap", "abc", home=tmp_path)
    assert r.returncode != 0 and "usage: torus llm cap" in (r.stdout + r.stderr)
    r = torus("llm", "cap", "1.2.3", home=tmp_path)
    assert r.returncode != 0


def test_cli_gateway_on_without_saved(tmp_path):
    r = torus("llm", "gateway", "on", home=tmp_path)
    assert r.returncode != 0 and "no saved gateway" in (r.stdout + r.stderr)


def test_web_page_and_docs():
    html = (ROOT / "torus" / "files" / "web" / "index.html").read_text()
    for tid in ("ask-spend-meter", "settings-gateway-toggle", "settings-cap-input", "spend-today", "turn-cost", "settings-gateway-cloudflare"):
        assert tid in html, tid
    inst = (ROOT / "torus" / "INSTALL.md").read_text()
    assert "never call Claude" in inst and "torus llm cap" in inst and "Claude traffic path" in inst
    man = (ROOT / "torus" / "manifest.txt").read_text()
    assert "bin/torus_spend.py" in man and "etc/prices.conf" in man
