"""T1 helper: serve the tdiff page with a new 1st sentence on 127.0.0.1:8111 and run one refill (leaves a diff in state for UI checks)."""
import sys
import threading
import time
import socketserver

import requests

sys.path.insert(0, "/app/backend/tests")
import test_torus_iter10_t1 as t  # noqa: E402

NEW = "Epsilon sentence now opens the page and explains how glaciers carve deep valleys."
t.PAGE["text"] = " ".join([NEW, t.S2, t.S3])
socketserver.TCPServer.allow_reuse_address = True
sv = socketserver.TCPServer(("127.0.0.1", t.PORT), t.H)
threading.Thread(target=sv.serve_forever, daemon=True).start()
s = requests.Session()
s.headers.update({"X-Torus": "1", "Content-Type": "application/json"})
print(s.post(f"{t.BASE}/api/oracle/run", json={"topic": "tdiff"}).status_code)
time.sleep(1.5)
for _ in range(60):
    o = s.get(f"{t.BASE}/api/oracle").json()
    if not o["running"]:
        break
    time.sleep(1)
tp = next(x for x in o["topics"] if x["name"] == "tdiff")
print(tp["results"][0].get("changed"), tp["results"][0].get("diff"))
print(open(tp["rollup"]).read())
sv.shutdown()
