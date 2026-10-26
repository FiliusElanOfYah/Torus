#!/usr/bin/env python3
"""torus_models - keep the Claude model ids in llm.toggle pointing at models that exist.

TORUS talks about tiers (sonnet = synthesis, opus = code, haiku = fast/cheap). Each tier maps to a
real Anthropic model id: MODEL_ALIASES default, or `<tier>_id=` in etc/llm.toggle. Anthropic retires
ids, so `check()` compares what is configured with the live list from /v1/models (fetched by llmd with
your key) and `fix()` points each tier at the newest id of its family. Last report: var/models.json.
"""
import json
import re
import time

from torus_common import LLM_TOGGLE, MODEL_ALIASES, VAR, atomic_write, read_conf, resolve_model, write_conf

TIERS = ("sonnet", "opus", "haiku")
TIER_ROLE = {"sonnet": "answers and follow-ups (default)", "opus": "code and hard questions", "haiku": "fast and cheap"}
REPORT = VAR / "models.json"


def family(model_id):
    for t in TIERS:
        if t in (model_id or "").lower():
            return t
    return None


def version_key(m):
    """Newest first when sorted descending: version numbers in the id, then created_at."""
    mid = m["id"] if isinstance(m, dict) else m
    nums = [int(x) for x in re.findall(r"\d+", mid.replace("claude", ""))]
    created = (m.get("created_at") or "") if isinstance(m, dict) else ""
    return (tuple(nums), created)


def check(models, conf=None):
    """models: list of {id, display_name?, created_at?} from Anthropic. -> report dict (not saved)."""
    conf = conf if conf is not None else read_conf(LLM_TOGGLE)
    rows = [m if isinstance(m, dict) else {"id": m} for m in models]
    ids = {m["id"] for m in rows}
    tiers = {}
    for t in TIERS:
        current = resolve_model(t, conf)
        fam = sorted((m for m in rows if family(m["id"]) == t), key=version_key, reverse=True)
        newest = fam[0]["id"] if fam else ""
        if not newest:
            status = "unknown"  # the key cannot see this family at all
        elif current == newest:
            status = "ok"
        elif current in ids:
            status = "outdated"
        else:
            status = "missing"
        tiers[t] = {"current": current, "exists": current in ids, "newest": newest, "status": status,
                    "role": TIER_ROLE[t], "available": [m["id"] for m in fam]}
    return {"ok": True, "when": time.time(), "models": rows, "tiers": tiers, "changes": [],
            "problems": sum(1 for t in tiers.values() if t["status"] in ("missing", "outdated"))}


def fix(report):
    """Write <tier>_id for every missing/outdated tier. Returns the list of changes (also in report)."""
    changes = []
    for t, info in report["tiers"].items():
        if info["status"] in ("missing", "outdated") and info["newest"]:
            write_conf(LLM_TOGGLE, {f"{t}_id": info["newest"]})
            changes.append({"tier": t, "from": info["current"], "to": info["newest"]})
            info.update(current=info["newest"], exists=True, status="ok")
    report["changes"] = changes
    report["problems"] = 0 if changes else report["problems"]
    return changes


def save(report):
    atomic_write(REPORT, json.dumps(report, ensure_ascii=False))


def last():
    try:
        return json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def validate_tier_id(tier, model_id):
    """-> '' when fine, else the reason."""
    if tier not in TIERS:
        return f"tier must be one of {', '.join(TIERS)}"
    if not re.fullmatch(r"claude-[a-z0-9.-]+", model_id or ""):
        return "model id must look like claude-<family>-<version>"
    if family(model_id) != tier:
        return f"{model_id} is not a {tier} model; the {tier} tier only takes claude-{tier}-* ids"
    return ""


def set_tier_id(tier, model_id):
    why = validate_tier_id(tier, model_id)
    if why:
        raise ValueError(why)
    write_conf(LLM_TOGGLE, {f"{tier}_id": model_id})
    return resolve_model(tier)
