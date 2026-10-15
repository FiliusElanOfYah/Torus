#!/usr/bin/env python3
"""sentineld - the gate in front of the LLM.

listens  torus.llm.request  {prompt, system?, model?, max_tokens?, job_id?}
emits    torus.llm.egress   same request with secrets/emails redacted + reply_to

Every outgoing prompt is logged (size + redaction count, never the text) to
~/.torus/logs/sentineld.jsonl, which rotates at 1 MB (keeps 3 old files).
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import LOGS, append_jsonl, log, pack, run_daemon  # noqa: E402

NAME = "sentineld"
MAX_PROMPT = 400_000
PATTERNS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{20,}"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
]


def redact(s):
    total = 0
    for p in PATTERNS:
        s, n = p.subn("[REDACTED]", s)
        total += n
    return s, total


async def on_request(nc, msg, data):
    reply_to = msg.reply or data.get("reply_to")
    prompt, hits = redact(str(data.get("prompt") or ""))
    msgs = []
    for m in data.get("messages") or []:
        if m.get("role") in ("user", "assistant"):
            text, n = redact(str(m.get("content") or ""))
            msgs.append({"role": m["role"], "content": text})
            hits += n
    size = len(prompt) + sum(len(m["content"]) for m in msgs)
    entry = {"t": time.time(), "job": data.get("job_id"), "chars": size, "redactions": hits}
    if size > MAX_PROMPT:
        entry["blocked"] = "too large"
        append_jsonl(LOGS / "sentineld.jsonl", entry)
        if reply_to:
            await nc.publish(reply_to, pack({"ok": False, "error": f"prompt too large ({size} chars)"}))
        return
    append_jsonl(LOGS / "sentineld.jsonl", entry)
    data.update(prompt=prompt, reply_to=reply_to)
    if msgs:
        data["messages"] = msgs
    await nc.publish("torus.llm.egress", pack(data))
    log(NAME, "forwarded", entry)


if __name__ == "__main__":
    run_daemon(NAME, {"torus.llm.request": on_request})
