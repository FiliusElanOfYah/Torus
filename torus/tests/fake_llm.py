#!/usr/bin/env python3
"""Dev-only stand-in for llmd: answers torus.llm.egress with canned replies so the
code write/run/fix loop can be tested without an Anthropic key.
First code request -> a buggy hello.py; any request that mentions a failed run -> the fix."""
import asyncio
import json

import nats


async def main():
    nc = await nats.connect("nats://127.0.0.1:4222")

    async def on(msg):
        req = json.loads(msg.data)
        prompt = req.get("prompt") or json.dumps(req.get("messages"))
        system = req.get("system") or ""
        if "TORUS Operator, planning" in system:
            text = json.dumps({"phases": [{"title": "Look around", "reads": ["the working folder"], "changes": [], "runs": ["ls -la"]},
                                          {"title": "Write a note", "reads": [], "changes": ["op-note.txt"], "runs": ["cat op-note.txt", "rm -rf nothing"]}],
                               "estimate_usd": 0.02, "notes": "stub plan from fake_llm"})
        elif "TORUS Operator executing phase 1" in system:
            text = json.dumps({"phase_done": True, "summary": "Looked around (stub)."}) if "### ls -la" in prompt else \
                json.dumps({"say": "listing the folder", "actions": [{"tool": "shell", "cmd": "ls -la"}]})
        elif "TORUS Operator executing phase 2" in system:
            text = json.dumps({"phase_done": True, "summary": "Wrote op-note.txt (stub)."}) if "### write" in prompt else \
                json.dumps({"say": "writing the note", "actions": [{"tool": "write", "path": "op-note.txt", "content": "hello from the operator\n"},
                                                                    {"tool": "shell", "cmd": "rm -rf nothing"}]})
        elif "TORUS Console" in system:
            last = (req.get("messages") or [{}])[-1].get("content", "")
            if last.startswith("###"):
                text = json.dumps({"say": "done: the listing is above (stub)."})
            elif "note" in last.lower():
                text = json.dumps({"say": "writing the note", "actions": [{"tool": "write", "path": "console-note.txt", "content": "hello from the console\n"}]})
            else:
                text = json.dumps({"say": "listing the folder", "actions": [{"tool": "shell", "cmd": "ls -la"}]})
        elif "<<<FILE" in system:
            if "failed with exit code" in prompt:
                text = ("Plan: fix the NameError by defining name.\n\n<<<FILE hello.py>>>\n"
                        "name = 'TORUS'\nprint(f'hello from {name}')\n<<<END>>>\n")
            else:
                text = ("Plan: create hello.py that greets.\n\n<<<FILE hello.py>>>\n"
                        "print(f'hello from {name}')\n<<<END>>>\n<<<FILE README.md>>>\n# demo\n<<<END>>>\n")
        else:
            text = f"Stub answer citing [1]. Messages seen: {len(req.get('messages') or [])}"
        await nc.publish(req["reply_to"], json.dumps({"ok": True, "text": text, "model": "stub",
                                                       "usage": {"input_tokens": 1200, "output_tokens": 300},
                                                       "cost": 0.0054, "cached": False}).encode())

    await nc.subscribe("torus.llm.egress", cb=on)

    async def on_models(msg):  # what Anthropic's /v1/models looks like, with one tier outdated and one retired
        models = [{"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "created_at": "2026-03-01T00:00:00Z"},
                  {"id": "claude-sonnet-5-0", "display_name": "Claude Sonnet 5", "created_at": "2025-11-01T00:00:00Z"},
                  {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "created_at": "2026-03-01T00:00:00Z"},
                  {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8", "created_at": "2025-12-01T00:00:00Z"},
                  {"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5", "created_at": "2026-03-01T00:00:00Z"},
                  {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5", "created_at": "2025-10-01T00:00:00Z"}]
        await nc.publish(msg.reply, json.dumps({"ok": True, "models": models}).encode())

    await nc.subscribe("torus.llm.models", cb=on_models)
    await asyncio.Event().wait()

asyncio.run(main())
