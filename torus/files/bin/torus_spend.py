"""torus_spend - what Claude cost you, from logs/llmd-usage.jsonl (one line per answered call).

Prices are *estimates* (USD per million tokens) taken from platform.claude.com/docs/en/about-claude/pricing
and kept in etc/prices.conf so you can correct them without touching code:
    sonnet_in=2  sonnet_out=10  opus_in=4  opus_out=20  haiku_in=0.10  haiku_out=0.50
    cache_read_factor=0.05 (share of the input price a cache hit costs)   cache_write_factor=1.25
A daily cap (llm.toggle: daily_cap_usd=2.50) makes llmd refuse new calls once today's estimate reaches it;
TORUS then falls back to the local summary and says so. 0 or empty = no cap. Day = local midnight to midnight.
"""
import json
import time

from torus_common import ETC, LLM_TOGGLE, LOGS, read_conf

PRICES_CONF = ETC / "prices.conf"
USAGE = LOGS / "llmd-usage.jsonl"
DEFAULT_PRICES = {"sonnet_in": 2.0, "sonnet_out": 10.0, "opus_in": 4.0, "opus_out": 20.0, "haiku_in": 0.10, "haiku_out": 0.50,
                  "cache_read_factor": 0.05, "cache_write_factor": 1.25}
MAX_READ = 4 * 1024 * 1024  # only the newest 4 MB of the usage log is ever read (a month is far smaller)


def prices():
    out = dict(DEFAULT_PRICES)
    for k, v in read_conf(PRICES_CONF).items():
        if k in out:
            try:
                out[k] = max(0.0, float(v))
            except ValueError:
                pass
    return out


def family(model_id):
    m = (model_id or "").lower()
    for fam in ("haiku", "opus", "sonnet"):
        if fam in m:
            return fam
    return "sonnet"  # unknown ids are priced like Sonnet rather than as free


def cost(model_id, usage, p=None):
    """USD for one call. usage = Anthropic's usage block (input_tokens, output_tokens, cache_*_input_tokens)."""
    p = p or prices()
    fam = family(model_id)
    u = usage or {}
    tok = lambda k: max(0, int(u.get(k) or 0))  # noqa: E731
    in_price, out_price = p[f"{fam}_in"], p[f"{fam}_out"]
    usd = (tok("input_tokens") * in_price + tok("output_tokens") * out_price
           + tok("cache_read_input_tokens") * in_price * p["cache_read_factor"]
           + tok("cache_creation_input_tokens") * in_price * p["cache_write_factor"]) / 1e6
    return round(usd, 6)


def daily_cap():
    try:
        return max(0.0, float(read_conf(LLM_TOGGLE).get("daily_cap_usd") or 0))
    except ValueError:
        return 0.0


def _rows(since):
    """Usage rows with t >= since, newest-4MB window, tolerant of a torn last line."""
    try:
        size = USAGE.stat().st_size
        with USAGE.open("rb") as f:
            if size > MAX_READ:
                f.seek(size - MAX_READ)
                f.readline()  # drop the partial line
            data = f.read().decode("utf-8", "ignore")
    except FileNotFoundError:
        return []
    rows = []
    for line in data.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and float(r.get("t") or 0) >= since:
            rows.append(r)
    return rows


def _bucket(rows, p):
    b = {"usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_calls": 0, "by_model": {}}
    for r in rows:
        u = r.get("usage") or {}
        usd = r["cost"] if isinstance(r.get("cost"), (int, float)) else cost(r.get("model"), u, p)
        b["usd"] += usd
        b["calls"] += 1
        b["input_tokens"] += int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
        b["output_tokens"] += int(u.get("output_tokens") or 0)
        b["cached_calls"] += 1 if r.get("cached") else 0
        fam = family(r.get("model"))
        bm = b["by_model"].setdefault(fam, {"usd": 0.0, "calls": 0})
        bm["usd"] = round(bm["usd"] + usd, 6)
        bm["calls"] += 1
    b["usd"] = round(b["usd"], 4)
    return b


def day_start(now=None):
    lt = time.localtime(now or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def month_start(now=None):
    lt = time.localtime(now or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def today_usd():
    return _bucket(_rows(day_start()), prices())["usd"]


def summary(now=None):
    now = now or time.time()
    p = prices()
    month_rows = _rows(month_start(now))
    ds = day_start(now)
    today = _bucket([r for r in month_rows if float(r.get("t") or 0) >= ds], p)
    month = _bucket(month_rows, p)
    cap = daily_cap()
    return {"ok": True, "today": today, "month": month, "cap_usd": cap, "cap_hit": bool(cap and today["usd"] >= cap),
            "cap_left_usd": round(max(0.0, cap - today["usd"]), 4) if cap else None,
            "prices": p, "prices_path": str(PRICES_CONF), "estimate": True,
            "resets_at": ds + 86400, "log": str(USAGE)}


def cap_message(cap, spent):
    return (f"Daily Claude cap reached: ${spent:.2f} of ${cap:.2f} spent today (estimate). "
            f"Raise or remove it with  torus llm cap <usd> | off  , or wait until midnight.")


if __name__ == "__main__":
    s = summary()
    print(f"today  ≈ ${s['today']['usd']:.4f}  ({s['today']['calls']} calls, {s['today']['input_tokens']} in / {s['today']['output_tokens']} out)")
    print(f"month  ≈ ${s['month']['usd']:.4f}  ({s['month']['calls']} calls)")
    for fam, b in sorted(s["today"]["by_model"].items()):
        print(f"   {fam:7} today ≈ ${b['usd']:.4f} in {b['calls']} call(s)")
    print(f"cap    {('$%.2f/day, $%.2f left' % (s['cap_usd'], s['cap_left_usd'])) if s['cap_usd'] else 'none (torus llm cap <usd>)'}"
          + ("  <- REACHED, Claude is paused until midnight" if s["cap_hit"] else ""))
    print(f"prices are estimates; edit {s['prices_path']}")
