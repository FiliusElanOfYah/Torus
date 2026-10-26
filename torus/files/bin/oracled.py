#!/usr/bin/env python3
"""oracled - keeps your pinned topics fresh.

Reads ~/.torus/etc/pinned.yaml (topics -> seed URLs). On a schedule
(etc/oracle.conf: every_minutes=60) it asks fetcherd to fetch every seed again,
so parserd -> indexerd + vaultd refill the index and the vault inbox. It then
writes one digest per topic to vault/topics/<topic>.md: every seed, when it was
last read, and the key sentences from each page (TextRank, no LLM).

listens  torus.oracle.status  {}         -> reply: state (last run, next run, per-seed results)
         torus.oracle.run     {topic?}   -> reply {ok} and starts a refill now
         torus.oracle.reload  {}         -> reply {ok, topics} after re-reading pinned.yaml
emits    torus.oracle.done    {topic, ok, fail, when}

State lives in var/oracle.json; every run is appended to logs/oracle.jsonl.
Config is re-read on every cycle, so editing pinned.yaml never needs a restart.
"""
import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import (ETC, LOGS, VAR, VAULT, append_jsonl, atomic_write, extract, log, pack,  # noqa: E402
                          read_conf, run_daemon, slugify, textrank, truthy)

NAME = "oracled"
PINNED = ETC / "pinned.yaml"
CONF = ETC / "oracle.conf"
STATE_FILE = VAR / "oracle.json"
RUN_LOG = LOGS / "oracle.jsonl"
TOPICS_DIR = VAULT / "topics"
MAX_SEEDS = 25
MAX_TOPICS = 40
TICK = 30

STATE = {"last_run": 0, "next_run": 0, "runs": 0, "running": False, "current": "", "topics": {}}
task = {"run": None}


# ---------------------------------------------------------------- settings
def oracle_settings():
    c = read_conf(CONF)
    try:
        every = max(5, min(24 * 60, int(c.get("every_minutes") or 60)))
    except ValueError:
        every = 60
    try:
        delay = max(10, int(c.get("start_delay_seconds") or 90))
    except ValueError:
        delay = 90
    return {"enabled": truthy(c.get("enabled", "true")), "every_minutes": every,
            "fresh": truthy(c.get("fresh", "true")), "start_delay_seconds": delay}


# ---------------------------------------------------------------- pinned.yaml
def _scalar(v):
    v = v.strip()
    if "#" in v and not v.startswith(("http://", "https://")):
        v = v.split("#", 1)[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def parse_pinned(text):
    """Read the small YAML subset the web app and `torus oracle` write:
    topics: / - name: x / seeds: / - https://...   Uses PyYAML when it is installed."""
    try:
        import yaml

        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("pinned.yaml must start with 'topics:'")
        raw = data.get("topics") or []
    except ImportError:
        raw, cur, ctx = [], None, None
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("- "):
                item = s[2:].strip()
                if item.startswith("name:"):
                    cur = {"name": _scalar(item[5:]), "seeds": []}
                    raw.append(cur)
                    ctx = None
                elif ctx == "seeds" and cur is not None:
                    cur["seeds"].append(_scalar(item))
                continue
            key, _, val = s.partition(":")
            key = key.strip()
            if key == "topics":
                continue
            if cur is None:
                raise ValueError(f"unexpected line before the first '- name:': {s}")
            if key == "name":
                cur["name"] = _scalar(val)
            elif key == "seeds":
                ctx = "seeds"
                v = val.strip()
                if v.startswith("[") and v.endswith("]"):
                    cur["seeds"] += [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
            else:
                cur[key] = _scalar(val)
    return normalize_topics(raw)


def normalize_topics(raw):
    out, seen = [], set()
    if not isinstance(raw, list):
        raise ValueError("'topics' must be a list")
    for t in raw[:MAX_TOPICS]:
        if not isinstance(t, dict) or not str(t.get("name") or "").strip():
            raise ValueError("every topic needs a name")
        name = re.sub(r"\s+", " ", str(t["name"]).strip())[:80]
        if name.lower() in seen:
            raise ValueError(f"topic '{name}' appears twice")
        seen.add(name.lower())
        seeds = t.get("seeds") or []
        if isinstance(seeds, str):
            seeds = seeds.split()
        urls = []
        for u in seeds:
            u = str(u).strip()
            if not u.startswith(("http://", "https://")):
                raise ValueError(f"topic '{name}': '{u}' is not an http(s) url")
            if u not in urls:
                urls.append(u)
        out.append({"name": name, "seeds": urls[:MAX_SEEDS]})
    return out


def dump_pinned(topics):
    lines = ["# TORUS pinned topics. oracled re-reads every seed on its schedule (torus oracle status).",
             "# Edit here or on the web page (Vault tab). One topic = a name and a list of URLs.", "topics:"]
    for t in topics:
        lines.append(f"  - name: {json.dumps(t['name'])}")
        lines.append("    seeds:" if t["seeds"] else "    seeds: []")
        lines += [f"      - {u}" for u in t["seeds"]]
    return "\n".join(lines) + "\n"


def load_pinned():
    """-> (topics, error). A broken file is reported, never fatal."""
    try:
        text = PINNED.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], f"{PINNED} is missing: torus install 7"
    try:
        return parse_pinned(text), ""
    except Exception as e:  # yaml errors, our ValueErrors
        return [], f"pinned.yaml: {e}"


def save_pinned(text):
    topics = parse_pinned(text)
    atomic_write(PINNED, dump_pinned(topics))
    return topics


# ---------------------------------------------------------------- state
def load_state():
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        STATE.update({k: saved.get(k, STATE[k]) for k in ("last_run", "next_run", "runs", "topics")})
    except (FileNotFoundError, ValueError):
        pass
    STATE["running"], STATE["current"] = False, ""


def save_state():
    atomic_write(STATE_FILE, json.dumps(STATE, ensure_ascii=False))


def status_payload():
    topics, err = load_pinned()
    conf = oracle_settings()
    rows = []
    for t in topics:
        st = STATE["topics"].get(t["name"]) or {}
        results = [r for r in st.get("results", []) if r["url"] in t["seeds"]]
        ok = sum(1 for r in results if r["status"] == "ok")
        rows.append({"name": t["name"], "seeds": t["seeds"], "when": st.get("when", 0), "ok": ok,
                     "fail": len(results) - ok, "results": results, "digest": st.get("digest", "")})
    return {"ok": True, "enabled": conf["enabled"], "every_minutes": conf["every_minutes"], "fresh": conf["fresh"],
            "last_run": STATE["last_run"], "next_run": STATE["next_run"], "runs": STATE["runs"],
            "running": STATE["running"], "current": STATE["current"], "topics": rows, "error": err,
            "pinned_path": str(PINNED)}


# ---------------------------------------------------------------- refill
async def fetch_seed(nc, url, fresh):
    try:
        msg = await nc.request("torus.fetch.request", pack({"url": url, "fresh": fresh, "job_id": "oracle"}), timeout=120)
        return json.loads(msg.data)
    except Exception as e:
        return {"ok": False, "error": f"fetcherd did not answer ({e.__class__.__name__}): torus status"}


def write_digest(topic, results, when):
    TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOPICS_DIR / f"{slugify(topic['name'])}.md"
    ok = sum(1 for r in results if r["status"] == "ok")
    parts = [f"---\ntopic: {json.dumps(topic['name'])}\nrefilled: {time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(when))}\n"
             f"seeds: {len(results)}\nok: {ok}\n---\n\n# {topic['name']}\n\n"
             f"_Refilled {time.strftime('%Y-%m-%d %H:%M', time.localtime(when))} by oracled. {ok}/{len(results)} seeds read._\n"]
    for r in results:
        head = f"## {r.get('title') or r['url']}\n\n<{r['url']}>  \n"
        if r["status"] != "ok":
            parts.append(head + f"**{r['status']}** - {r.get('error') or ''}\n")
            continue
        sents = "\n".join(f"- {s}" for s in r.get("key", [])) or "- (no readable sentences)"
        parts.append(head + f"{r.get('chars', 0)} chars{' (from cache)' if r.get('cached') else ''}\n\n{sents}\n")
    atomic_write(path, "\n".join(parts))
    return str(path)


async def run_topic(nc, topic, fresh):
    results = []
    for url in topic["seeds"]:
        STATE["current"] = f"{topic['name']}: {url}"
        save_state()
        res = await fetch_seed(nc, url, fresh)
        row = {"url": url, "status": "ok", "title": "", "chars": 0, "error": "", "cached": bool(res.get("cached"))}
        if res.get("robots_blocked"):
            row.update(status="robots", error="robots.txt asks bots to stay out")
        elif res.get("blocked"):
            row.update(status="blocked", error=f"bot check or block (HTTP {res.get('status')})")
        elif not res.get("ok"):
            row.update(status="error", error=res.get("error") or "fetch failed")
        else:
            title, text = await asyncio.to_thread(extract, res.get("body") or "", res.get("final_url") or url,
                                                  res.get("content_type") or "")
            if len(text.strip()) < 80:
                row.update(status="empty", error="page came back almost empty (needs JavaScript?)")
            else:
                row.update(title=title or url, chars=len(text), key=await asyncio.to_thread(textrank, text, 3))
        results.append(row)
        log(NAME, topic["name"], row["status"], url, row["error"])
    when = time.time()
    ok = sum(1 for r in results if r["status"] == "ok")
    digest = write_digest(topic, results, when)
    STATE["topics"][topic["name"]] = {"when": when, "ok": ok, "fail": len(results) - ok, "digest": digest,
                                      "results": [{k: v for k, v in r.items() if k != "key"} for r in results]}
    save_state()
    append_jsonl(RUN_LOG, {"when": when, "topic": topic["name"], "ok": ok, "fail": len(results) - ok,
                           "seeds": [(r["url"], r["status"]) for r in results]})
    await nc.publish("torus.oracle.done", pack({"topic": topic["name"], "ok": ok, "fail": len(results) - ok, "when": when}))
    return ok, len(results) - ok


async def run_all(nc, only=None, why="schedule"):
    topics, err = load_pinned()
    conf = oracle_settings()
    if only:
        topics = [t for t in topics if t["name"].lower() == only.lower()]
    STATE["running"], STATE["current"] = True, "starting"
    save_state()
    log(NAME, f"refill ({why}): {len(topics)} topic(s)" + (f"  [{err}]" if err else ""))
    try:
        for t in topics:
            await run_topic(nc, t, conf["fresh"])
        # drop state for topics that are no longer pinned
        names = {t["name"] for t in (load_pinned()[0])}
        STATE["topics"] = {k: v for k, v in STATE["topics"].items() if k in names}
    except asyncio.CancelledError:
        log(NAME, "refill cancelled")
        raise
    except Exception as e:
        log(NAME, f"refill failed: {e.__class__.__name__}: {e}")
    finally:
        now = time.time()
        STATE.update(running=False, current="", last_run=now, next_run=now + conf["every_minutes"] * 60)
        STATE["runs"] = STATE.get("runs", 0) + 1
        save_state()
        task["run"] = None
        log(NAME, f"next refill at {time.strftime('%H:%M', time.localtime(STATE['next_run']))}")


def start_run(nc, only=None, why="manual"):
    if task["run"] and not task["run"].done():
        return False
    task["run"] = asyncio.create_task(run_all(nc, only, why))
    return True


async def scheduler(nc):
    conf = oracle_settings()
    now = time.time()
    if STATE["last_run"] and now - STATE["last_run"] < conf["every_minutes"] * 60:
        STATE["next_run"] = STATE["last_run"] + conf["every_minutes"] * 60
    else:
        STATE["next_run"] = now + conf["start_delay_seconds"]
    save_state()
    while True:
        conf = oracle_settings()
        if conf["enabled"] and time.time() >= STATE["next_run"]:
            if not start_run(nc, why="schedule"):
                STATE["next_run"] = time.time() + TICK
        await asyncio.sleep(TICK)


# ---------------------------------------------------------------- handlers
async def on_status(nc, msg, data):
    if msg.reply:
        await nc.publish(msg.reply, pack(status_payload()))


async def on_run(nc, msg, data):
    only = (data.get("topic") or "").strip() or None
    topics, err = load_pinned()
    if err and not topics:
        out = {"ok": False, "error": err}
    elif only and not any(t["name"].lower() == only.lower() for t in topics):
        out = {"ok": False, "error": f"no pinned topic called '{only}'"}
    elif start_run(nc, only):
        out = {"ok": True, "started": True, "topics": len(topics) if not only else 1}
    else:
        out = {"ok": False, "error": "a refill is already running"}
    if msg.reply:
        await nc.publish(msg.reply, pack(out))


async def on_reload(nc, msg, data):
    topics, err = load_pinned()
    if msg.reply:
        await nc.publish(msg.reply, pack({"ok": not err, "topics": topics, "error": err}))


async def on_start(nc):
    load_state()
    if not PINNED.exists():
        atomic_write(PINNED, dump_pinned([]))
    asyncio.create_task(scheduler(nc))


if __name__ == "__main__":
    run_daemon(NAME, {"torus.oracle.status": on_status, "torus.oracle.run": on_run, "torus.oracle.reload": on_reload},
               on_start=on_start)
