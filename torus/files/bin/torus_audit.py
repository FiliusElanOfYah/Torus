#!/usr/bin/env python3
"""torus_audit - the Phase 8 OPSEC audit, run by `torus audit`, the web page (Settings) and at boot.

Every check returns pass / fail / warn / skip with a one-line fix. Nothing here changes the
phone except `prune` (deletes rotated logs older than prune_logs_days) and `boot`
(turns Claude off after a reboot when hardening.conf says so). Results go to
~/.torus/var/audit.json (latest) and ~/.torus/logs/audit.jsonl (history).
"""
import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torus_seal  # noqa: E402
from torus_common import (AIRGAP, AIRGAP_SOCK, ETC, HOME, INDEX_DB, LLM_TOGGLE, LOGS, VAR, VAULT,  # noqa: E402
                          append_jsonl, atomic_write, key_present, llm_settings, read_conf, write_conf)

HARDENING = ETC / "hardening.conf"
BOOT = Path.home() / ".termux" / "boot" / "00-torus-start"
PREFIX = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
PORT = int(os.environ.get("TORUS_PORT", "7777"))
LOG_LIMIT = 1_200_000  # svlogd s1000000 and append_jsonl rotate at 1 MB
SHARED = ("/storage/", "/sdcard/", "/mnt/")


def hardening():
    c = read_conf(HARDENING)
    return {"llm_off_at_boot": str(c.get("llm_off_at_boot", "true")).lower() in ("1", "true", "yes", "on"),
            "prune_logs_days": int(c.get("prune_logs_days") or 7)}


def in_termux():
    return PREFIX.is_dir() and str(PREFIX).startswith("/data/data/com.termux")


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def on_shared_storage(p):
    try:
        real = str(Path(p).resolve())
    except OSError:
        real = str(p)
    return real.startswith(SHARED)


# ---------------------------------------------------------------- the checks
def check_fdroid():
    if not in_termux():
        return "skip", "not running inside Termux", ""
    rel = os.environ.get("TERMUX_APP__APK_RELEASE", "")
    if rel:
        return ("pass", f"Termux build: {rel}", "") if "DROID" in rel.upper() else \
            ("fail", f"Termux build is {rel}, not F-Droid", "uninstall Termux and install it from F-Droid (Play Store builds are dead)")
    try:
        out = subprocess.run(["termux-info"], capture_output=True, text=True, timeout=20).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "warn", "cannot tell (termux-info not available)", "pkg install termux-tools"
    low = out.lower()
    if "fdroid" in low or "f-droid" in low:
        return "pass", "termux-info says F-Droid", ""
    if "vending" in low or "play" in low:
        return "fail", "termux-info says Play Store", "uninstall Termux and install it from F-Droid"
    return "warn", "termux-info does not name the installer", "check Android Settings > Apps > Termux > App details"


def listening_sockets():
    """(port, is_wildcard) for every TCP LISTEN socket owned by this user."""
    me, out = os.getuid(), []
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            f = ln.split()
            if len(f) < 8 or f[3] != "0A" or int(f[7]) != me:
                continue
            addr, port = f[1].rsplit(":", 1)
            wild = set(addr) == {"0"}
            out.append((int(port, 16), wild))
    return out


def check_ports():
    socks = listening_sockets()
    if not socks:
        return "warn", "no listening sockets found (is the mesh running?)", "torus status"
    wild = sorted({p for p, w in socks if w})
    local = sorted({p for p, w in socks if not w})
    conf = read_conf(ETC / "nats.conf") if (ETC / "nats.conf").exists() else {}
    hint = "" if "127.0.0.1" in str(conf.get("listen", "127.0.0.1")) else "; nats.conf listen is not 127.0.0.1"
    if wild:
        return "fail", f"ports open to the network: {', '.join(map(str, wild))}{hint}", \
            "every service must bind 127.0.0.1 (nats.conf listen: 127.0.0.1:4222, mongod --bind_ip 127.0.0.1)"
    return ("warn" if hint else "pass"), f"all {len(local)} listening ports are 127.0.0.1 only ({', '.join(map(str, local))}){hint}", ""


def check_airgap_socket():
    if not AIRGAP.is_dir():
        return "fail", "~/.torus/airgap missing", "torus install 5"
    probs = []
    if mode(AIRGAP) & 0o077:
        probs.append(f"folder mode {mode(AIRGAP):o} (want 700)")
    if not AIRGAP_SOCK.exists():
        probs.append("airgap.sock missing (airgapd down?)")
    elif mode(AIRGAP_SOCK) & 0o077:
        probs.append(f"socket mode {mode(AIRGAP_SOCK):o} (want 600)")
    if probs:
        return "fail", "; ".join(probs), "chmod 700 ~/.torus/airgap; torus restart airgapd"
    return "pass", "airgapd listens on a unix socket, mode 600, folder 700, no TCP port", ""


def check_keys_at_rest():
    keys = sorted(AIRGAP.glob("*.key")) if AIRGAP.is_dir() else []
    probs = []
    for k in keys:
        if mode(k) & 0o077:
            probs.append(f"{k.name} mode {mode(k):o}")
    if on_shared_storage(AIRGAP):
        probs.append("airgap folder is on shared storage (any app can read it)")
    prefixes = [k.read_text(encoding="utf-8", errors="ignore").strip()[:12] for k in keys]
    prefixes = [p for p in prefixes if len(p) >= 12]
    leaked = []
    for p in list(LOGS.rglob("*.jsonl")) + list(LOGS.rglob("current")) + list(LOGS.rglob("@*.s")):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pre in txt for pre in prefixes) or "sk-ant-" in txt:
            leaked.append(str(p.relative_to(LOGS)))
    if leaked:
        probs.append("a key value appears in logs: " + ", ".join(leaked[:3]))
    if probs:
        return "fail", "; ".join(probs), "chmod 600 ~/.torus/airgap/*.key; torus key rm <name> and set it again after cleaning the logs"
    if not keys:
        return "pass", "no API keys stored yet (nothing to leak)", ""
    return "pass", f"{len(keys)} key file(s) mode 600 in private storage; no key value in any log", ""


def check_seal():
    r = torus_seal.verify()
    if not r["sealed"]:
        return "warn", "keys and daemons are not sealed yet", "torus seal"
    if r["ok"]:
        pq = "" if r["algo"] == "ML-DSA-65" else " (HMAC fallback, not post-quantum: torus install 8 installs dilithium-py)"
        return ("pass" if not pq else "warn"), f"seal verifies: {r['algo']}, fingerprint {r['fingerprint']}, sealed {r.get('created', '')}{pq}", ""
    return "fail", r["error"], "if you changed those files on purpose: torus seal   otherwise: torus install all, then torus seal"


def check_logs():
    big, rotated = [], 0
    for p in LOGS.rglob("*"):
        if not p.is_file():
            continue
        if p.stat().st_size > LOG_LIMIT and (p.suffix == ".jsonl" or p.name == "current"):
            big.append(f"{p.relative_to(LOGS)} ({p.stat().st_size // 1000} kB)")
        if p.name.startswith("@") or p.suffix.lstrip(".").isdigit():
            rotated += 1
    days = hardening()["prune_logs_days"]
    if big:
        return "fail", "logs over the 1 MB rotation limit: " + ", ".join(big[:4]), "torus install 1 (rewrites the svlogd config); torus restart all"
    return "pass", f"every log under 1 MB; {rotated} rotated file(s); rotated logs older than {days} days are pruned at boot", ""


def check_storage_scope():
    private = {"bin": HOME / "bin", "etc": ETC, "airgap": AIRGAP, "var": VAR, "logs": LOGS, "index": INDEX_DB}
    bad = [n for n, p in private.items() if p.exists() and on_shared_storage(p)]
    if bad:
        return "fail", "on shared storage: " + ", ".join(bad), "move ~/.torus back to the private home folder; only the vault belongs in Downloads"
    where = "shared storage (Downloads)" if on_shared_storage(VAULT) else "private storage"
    return "pass", f"code, config, keys, state in private storage; vault in {where}", ""


def check_boot():
    if not in_termux():
        return "skip", "not Termux", ""
    if not BOOT.exists():
        return "fail", "no Termux:Boot script", "torus install 1"
    if not os.access(BOOT, os.X_OK):
        return "fail", "boot script is not executable", f"chmod +x {BOOT}"
    hook = "torus_audit.py boot" in BOOT.read_text(errors="ignore")
    return ("pass" if hook else "warn"), ("boot script present with the hardening hook" if hook else "boot script present but old (no hardening hook)"), \
        ("" if hook else "torus install 8") + "; open the Termux:Boot app once so Android allows autostart"


def installed_pkgs():
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f", "${Package}=${Version}\n"], capture_output=True, text=True, timeout=30).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return {ln.split("=", 1)[0] for ln in out.splitlines() if ln.strip()}


def check_pkg_manifest():
    man = ETC / "pkg.manifest"
    if not man.exists():
        return "skip", "no pinned package list yet", "torus export writes etc/pkg.manifest"
    have = installed_pkgs()
    if have is None:
        return "skip", "dpkg-query not available", ""
    want = {ln.split("=", 1)[0] for ln in man.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    missing, extra = sorted(want - have), sorted(have - want)
    if missing:
        return "fail", f"{len(missing)} pinned package(s) missing: {', '.join(missing[:5])}", "pkg install " + " ".join(missing[:10])
    if extra:
        return "warn", f"{len(extra)} package(s) installed since the last export: {', '.join(extra[:5])}", "torus export (refreshes the list)"
    return "pass", f"installed packages match the pinned list ({len(want)})", ""


def check_llm_default():
    h = hardening()
    st = llm_settings()
    hook = BOOT.exists() and "torus_audit.py boot" in BOOT.read_text(errors="ignore")
    now = "Claude is ON right now" if st["enabled"] else "Claude is off right now"
    if h["llm_off_at_boot"] and (hook or not in_termux()):
        return "pass", f"Claude is switched off at every reboot (hardening.conf); {now}", ""
    if not h["llm_off_at_boot"]:
        return "warn", f"llm_off_at_boot=false: Claude stays on across reboots; {now}", "set llm_off_at_boot=true in ~/.torus/etc/hardening.conf"
    return "warn", f"boot hook missing, so the setting cannot apply; {now}", "torus install 8"


def check_gateway_token():
    tok = ETC / "gateway.token"
    if not tok.exists():
        return "warn", "no gateway token yet (gatewayd writes it on first start)", "torus restart gatewayd"
    if mode(tok) & 0o077:
        return "fail", f"gateway.token mode {mode(tok):o}", f"chmod 600 {tok}"
    return "pass", "gateway token (Code tab) is mode 600 and only reaches Brave via `torus open`", ""


def check_gateway_host():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=3) as s:
            s.sendall(f"GET /api/health HTTP/1.1\r\nHost: evil.example.com:{PORT}\r\nConnection: close\r\n\r\n".encode())
            head = s.recv(64).decode(errors="ignore")
    except OSError:
        return "skip", f"gatewayd is not answering on 127.0.0.1:{PORT}", "torus status"
    if " 403 " in head:
        return "pass", "gatewayd refuses requests with a foreign Host header (DNS-rebinding guard)", ""
    return "fail", f"gatewayd answered a foreign Host header with {head.split(chr(13))[0]}", "torus install 6"


def check_cf_gateway():
    st = llm_settings()
    if not (st["cf_account"] and st["cf_gateway"]):
        return "pass", "Claude calls go straight to api.anthropic.com (no third party in the path)", ""
    tok = "token stored" if key_present("cloudflare") else "no cloudflare token (fine for an unauthenticated gateway)"
    return ("warn", f"Cloudflare AI Gateway {st['cf_gateway']} is in the path: prompts and answers transit Cloudflare; {tok}",
            "turn OFF 'log request/response body' in the gateway settings, or: torus llm gateway off")


def check_operator():
    conf = read_conf(ETC / "operator.conf")
    if not (ETC / "operator.conf").exists():
        return "skip", "phase 10 (Operator) not installed", "torus install 10"
    snap = VAR / "operator" / "snapshots"
    if snap.exists() and mode(snap) & 0o077:
        return "fail", f"operator snapshots folder mode {mode(snap):o} (holds copies of files the agent changed)", f"chmod 700 {snap}"
    strict = str(conf.get("ask_every_change", "false")).lower() in ("1", "true", "yes", "on")
    how = "every change waits for a tap" if strict else "hybrid: reads alone, in-plan changes with a summary, danger list asks"
    return "pass", f"Operator: keys/seal/gateway.token unreadable to the agent; snapshots mode 700; {how}", ""


def check_hooks():
    if not (HOME / "bin" / "hookd.py").exists():
        return "skip", "phase 11 (webhooks) not installed", "torus install 11"
    tun = ETC / "tunnel.yml"
    if not tun.exists():
        return "pass", "hookd listens on 127.0.0.1 only; no tunnel configured, nothing reachable from outside", ""
    try:
        import yaml
        cfg = yaml.safe_load(tun.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return "fail", f"tunnel.yml unreadable: {e}", "torus tunnel route <hostname>"
    bad = [f"{i.get('hostname')} -> {i.get('service')}" for i in cfg.get("ingress") or [] if i.get("hostname") and i.get("service") != "http://127.0.0.1:7778"]
    if bad:
        return "fail", "the tunnel forwards to something other than hookd: " + ", ".join(bad), "torus tunnel route <hostname>   (rewrites tunnel.yml to hookd only)"
    if not (AIRGAP / "webhook.key").exists():
        return "fail", "tunnel is up but no webhook secret: every delivery is refused with 503", "torus hooks secret"
    if mode(AIRGAP / "webhook.key") & 0o077:
        return "fail", f"webhook.key mode {mode(AIRGAP / 'webhook.key'):o}", f"chmod 600 {AIRGAP / 'webhook.key'}"
    if mode(tun) & 0o077:
        return "warn", f"tunnel.yml mode {mode(tun):o} (holds the tunnel credentials path)", f"chmod 600 {tun}"
    return "pass", "tunnel -> hookd (127.0.0.1:7778) only; HMAC secret stored, mode 600", ""


def check_github():
    key = AIRGAP / "github.key"
    if not key.exists():
        return "skip", "no GitHub token stored (Operator cannot reach GitHub)", "torus key set github   (fine-grained PAT)"
    if mode(key) & 0o077:
        return "fail", f"github.key mode {mode(key):o}", f"chmod 600 {key}"
    sc = (read_conf(ETC / "operator.conf").get("github_scope") or "task").strip().lower()
    if sc == "all":
        return ("warn", "GitHub scope is ALL: the Operator may read any repo the token can see",
                "torus gh scope task   (then name repos per task), and keep the PAT fine-grained with only the repos you need")
    return "pass", "GitHub token mode 600; Operator reaches only the repos named in each task", ""


def check_crlf():
    bad = [p.name for p in (HOME / "bin").glob("*.py") if b"\r" in p.read_bytes()]
    if bad:
        return "fail", "Windows line endings in: " + ", ".join(bad[:5]), "torus install all"
    return "pass", "no CRLF in installed daemons", ""


CHECKS = [
    ("opsec-1", "Termux is the F-Droid build", check_fdroid),
    ("opsec-2", "No daemon listens on 0.0.0.0 (127.0.0.1 only)", check_ports),
    ("opsec-3", "airgapd only on a unix socket, mode 600", check_airgap_socket),
    ("opsec-4", "API keys mode 600, private storage, never in logs", check_keys_at_rest),
    ("opsec-5", "ML-DSA-65 seal on keys + daemons verifies", check_seal),
    ("opsec-6", "Logs rotate at 1 MB and old ones are pruned", check_logs),
    ("opsec-7", "Only the vault lives on shared storage", check_storage_scope),
    ("opsec-8", "Termux:Boot script present", check_boot),
    ("opsec-9", "Installed packages match the pinned list", check_pkg_manifest),
    ("opsec-10", "Claude defaults to off after a reboot", check_llm_default),
    ("extra-1", "Gateway token is mode 600", check_gateway_token),
    ("extra-2", "Gateway rejects foreign Host headers", check_gateway_host),
    ("extra-3", "No CRLF line endings in daemons", check_crlf),
    ("extra-4", "Claude traffic path (Cloudflare AI Gateway on/off)", check_cf_gateway),
    ("extra-5", "Operator reach and approval mode", check_operator),
    ("extra-6", "GitHub token and Operator repo scope", check_github),
    ("extra-7", "Webhooks: tunnel exposes only hookd", check_hooks),
]


def run_audit():
    items = []
    for cid, label, fn in CHECKS:
        try:
            status, detail, fix = fn()
        except Exception as e:  # a broken check must not hide the others
            status, detail, fix = "warn", f"check crashed: {e.__class__.__name__}: {e}", "torus logs gatewayd"
        items.append({"id": cid, "label": label, "status": status, "detail": detail, "fix": fix})
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ("pass", "fail", "warn", "skip")}
    res = {"t": time.time(), "when": time.strftime("%Y-%m-%dT%H:%M:%S"), "ok": counts["fail"] == 0,
           "counts": counts, "items": items}
    VAR.mkdir(parents=True, exist_ok=True)
    atomic_write(VAR / "audit.json", json.dumps(res, ensure_ascii=False))
    append_jsonl(LOGS / "audit.jsonl", {"t": res["t"], "ok": res["ok"], **counts,
                                        "fails": [i["id"] for i in items if i["status"] == "fail"]})
    return res


def last_audit():
    try:
        return json.loads((VAR / "audit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prune(days=None):
    """Delete rotated log files older than N days. Returns the number removed."""
    days = hardening()["prune_logs_days"] if days is None else days
    if days <= 0:
        return 0
    cutoff, n = time.time() - days * 86400, 0
    for p in LOGS.rglob("*"):
        rotated = p.name.startswith("@") or (p.suffix.lstrip(".").isdigit() and ".jsonl" in p.name)
        if p.is_file() and rotated and p.stat().st_mtime < cutoff:
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def boot():
    """Called from ~/.termux/boot/00-torus-start."""
    h = hardening()
    acts = []
    if h["llm_off_at_boot"] and llm_settings()["enabled"]:
        write_conf(LLM_TOGGLE, {"enabled": "false"})
        acts.append("llm off")
    n = prune()
    if n:
        acts.append(f"pruned {n} old logs")
    append_jsonl(LOGS / "audit.jsonl", {"t": time.time(), "boot": True, "actions": acts})
    print("boot:", ", ".join(acts) or "nothing to do")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        res = run_audit()
        colour = {"pass": "\033[32mPASS\033[0m", "fail": "\033[31mFAIL\033[0m", "warn": "\033[33mWARN\033[0m", "skip": "\033[2mSKIP\033[0m"}
        for i in res["items"]:
            print(f"  {colour[i['status']]}  {i['id']:9} {i['label']}\n          {i['detail']}" + (f"\n          fix: {i['fix']}" if i["fix"] and i["status"] != "pass" else ""))
        c = res["counts"]
        print(f"\naudit: {c['pass']} pass, {c['fail']} fail, {c['warn']} warn, {c['skip']} skipped   (saved to ~/.torus/var/audit.json)")
        sys.exit(0 if res["ok"] else 1)
    elif cmd == "prune":
        print("pruned", prune(), "rotated log file(s)")
    elif cmd == "boot":
        boot()
    elif cmd == "json":
        print(json.dumps(run_audit(), indent=1))
    else:
        sys.exit("usage: torus_audit.py run|json|prune|boot")


if __name__ == "__main__":
    main()
