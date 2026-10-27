#!/usr/bin/env python3
"""llmd - calls Claude (Anthropic API) with httpx using YOUR key.

listens  torus.llm.egress  (from sentineld)  -> publishes result to reply_to
         torus.llm.models  -> reply {ok, models:[{id, display_name, created_at}]}

Settings come from ~/.torus/etc/llm.toggle, re-read on every request:
  enabled=true|false   model=sonnet|opus|haiku   max_tokens=4096
  sonnet_id=...  opus_id=...  haiku_id=...   (optional exact model ids; torus llm verify fixes them)
Rate limits / overloads are retried with backoff, so long sessions slow
down instead of stopping.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx  # noqa: E402
from torus_common import LOGS, append_jsonl, get_secret, key_present, llm_settings, log, pack, resolve_model, run_daemon  # noqa: E402
import torus_spend  # noqa: E402

NAME = "llmd"
API = "https://api.anthropic.com/v1"
RETRY = {429, 500, 502, 503, 504, 529}
client = None


async def on_start(nc):
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15))


def headers(key, cf_token=""):
    h = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    if cf_token:  # Cloudflare "authenticated gateway" token; the Anthropic key above still does the paying
        h["cf-aig-authorization"] = f"Bearer {cf_token}"
    return h


async def call_claude(req):
    s = llm_settings()
    if not s["enabled"]:
        return {"ok": False, "error": "LLM is off. Turn it on with: torus llm on"}
    cap = torus_spend.daily_cap()
    if cap:
        spent = torus_spend.today_usd()
        if spent >= cap:
            log(NAME, f"refused: daily cap ${cap:.2f} reached (${spent:.4f})", req.get("job_id"))
            return {"ok": False, "error": torus_spend.cap_message(cap, spent), "cap_hit": True}
    key = await get_secret("anthropic")
    cf_token = ""
    if s["cf_account"] and key_present("cloudflare"):
        cf_token = await get_secret("cloudflare")
    model = resolve_model(req["model"]) if req.get("model") else s["model_id"]
    messages = req.get("messages") or [{"role": "user", "content": req.get("prompt") or ""}]
    body = {"model": model, "max_tokens": int(req.get("max_tokens") or s["max_tokens"]), "messages": messages}
    if req.get("system"):
        body["system"] = req["system"]
    err = ""
    for attempt in range(6):
        wait = min(60, 2 ** attempt * 2)
        try:
            r = await client.post(f"{s['api_base']}/messages", json=body, headers=headers(key, cf_token))
        except httpx.HTTPError as e:
            err = f"{e.__class__.__name__}: {e}"
        else:
            if r.status_code == 200:
                j = r.json()
                text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
                usage = j.get("usage", {}) or {}
                usd = torus_spend.cost(j.get("model", model), usage)
                cached = (r.headers.get("cf-aig-cache-status") or "").upper() == "HIT"  # Cloudflare AI Gateway cache hit
                append_jsonl(LOGS / "llmd-usage.jsonl", {"t": time.time(), "model": j.get("model", model),
                                                        "usage": usage, "cost": usd, "cached": cached, "job": req.get("job_id")})
                return {"ok": True, "text": text, "model": j.get("model", model),
                        "usage": usage, "cost": usd, "cached": cached, "stop_reason": j.get("stop_reason")}
            err = f"HTTP {r.status_code}: {r.text[:400]}"
            if r.status_code not in RETRY:
                return {"ok": False, "error": err}
            try:
                wait = float(r.headers.get("retry-after") or wait)
            except ValueError:
                pass
        log(NAME, f"attempt {attempt + 1} failed ({err[:120]}); retry in {wait:.0f}s")
        await asyncio.sleep(wait)
    return {"ok": False, "error": f"gave up after 6 tries: {err}"}


async def on_egress(nc, msg, data):
    try:
        out = await call_claude(data)
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    log(NAME, "done" if out.get("ok") else "failed", data.get("job_id"), out.get("error", "")[:200])
    if data.get("reply_to"):
        await nc.publish(data["reply_to"], pack(out))


async def on_models(nc, msg, data):
    try:
        key = await get_secret("anthropic")
        r = await client.get(f"{API}/models", params={"limit": 100}, headers=headers(key))
        r.raise_for_status()
        out = {"ok": True, "models": [{"id": m.get("id"), "display_name": m.get("display_name", ""),
                                       "created_at": m.get("created_at", "")} for m in r.json().get("data", []) if m.get("id")]}
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    if msg.reply:
        await nc.publish(msg.reply, pack(out))


if __name__ == "__main__":
    run_daemon(NAME, {"torus.llm.egress": on_egress, "torus.llm.models": on_models}, concurrent=True, on_start=on_start)
