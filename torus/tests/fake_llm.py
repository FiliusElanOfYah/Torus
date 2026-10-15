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
        if "<<<FILE" in (req.get("system") or ""):
            if "failed with exit code" in prompt:
                text = ("Plan: fix the NameError by defining name.\n\n<<<FILE hello.py>>>\n"
                        "name = 'TORUS'\nprint(f'hello from {name}')\n<<<END>>>\n")
            else:
                text = ("Plan: create hello.py that greets.\n\n<<<FILE hello.py>>>\n"
                        "print(f'hello from {name}')\n<<<END>>>\n<<<FILE README.md>>>\n# demo\n<<<END>>>\n")
        else:
            text = f"Stub answer citing [1]. Messages seen: {len(req.get('messages') or [])}"
        await nc.publish(req["reply_to"], json.dumps({"ok": True, "text": text, "model": "stub"}).encode())

    await nc.subscribe("torus.llm.egress", cb=on)
    await asyncio.Event().wait()

asyncio.run(main())
