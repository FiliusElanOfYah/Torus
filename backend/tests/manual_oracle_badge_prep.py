# Prep for the oracle "N new" pill UI test: reset the 'example' topic state so the next run reports changed=1.
import sys
import time

import requests

B = "http://127.0.0.1:7777"
H = {"X-Torus": "1"}


def wait():
    for _ in range(90):
        o = requests.get(f"{B}/api/oracle", headers=H).json()
        if not o.get("running"):
            return o
        time.sleep(1)


def cfg(yaml):
    r = requests.post(f"{B}/api/oracle/config", json={"yaml": yaml}, headers=H)
    print("config", r.status_code, r.text[:120])


def run(topic=None):
    r = requests.post(f"{B}/api/oracle/run", json={"topic": topic} if topic else {}, headers=H)
    print("run", r.status_code, r.text[:120])
    time.sleep(1)
    return wait()


if sys.argv[1] == "prep":
    wait()
    cfg('topics:\n  - name: "tmpreset"\n    seeds:\n      - https://example.com/\n')
    run()
    o = requests.get(f"{B}/api/oracle", headers=H).json()
    print("topics after reset:", [t["name"] for t in o["topics"]])
    cfg('topics:\n  - name: "example"\n    seeds:\n      - https://www.iana.org/help/example-domains\n')
    o = run("example")
    t = next(t for t in o["topics"] if t["name"] == "example")
    print("example changed:", t["changed"], "changed_at:", t["changed_at"], [(r["url"], r["hash"], r["changed"]) for r in t["results"]])
elif sys.argv[1] == "restore":
    cfg(open("/tmp/pinned.orig.yaml").read())
    o = run()
    print("restored topics:", [(t["name"], len(t["seeds"]), t["changed"]) for t in o["topics"]])
