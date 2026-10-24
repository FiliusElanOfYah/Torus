#!/usr/bin/env python3
"""torus_export - Phase 9: pack the whole TORUS setup into one signed tarball, and restore it.

  torus export [--with-keys] [--no-vault] [--to DIR]
      writes etc/requirements.freeze + etc/pkg.manifest + docs/RESTORE.md, then
      Downloads/torus-<date>.tar.gz (+ .sha256 + .sig) with ~/.torus (bin, etc, web, var,
      docs, vault, index) and ~/.termux/boot. API keys, the seal secret key and the gateway
      token are NOT included unless --with-keys.
  torus export list
  torus restore <tarball> [--yes]
      verifies the hash (and the signature when this phone has the public key), then
      unpacks into your home folder. Existing files are backed up first.

State that can be rebuilt is left out: venv, backups, logs, cache, jetstream, mongo data,
shards, the git checkout (~/.torus/src is re-cloned by the bootstrap line).
"""
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import torus_seal  # noqa: E402
from torus_common import AIRGAP, ETC, HOME, INDEX_DB, VAULT, atomic_write  # noqa: E402

HOMEDIR = Path.home()
DOCS = HOME / "docs"
BOOT_DIR = HOMEDIR / ".termux" / "boot"
INCLUDE = ["bin", "etc", "web", "var", "docs"]
EXCLUDE_NAMES = {"venv", "backups", "logs", "cache", "jetstream", "mongo", "shards", "src", "run", "__pycache__"}

RESTORE_MD = """# Restoring TORUS onto a fresh phone

1. Install **Termux** and **Termux:Boot** from F-Droid. Open both once.
2. In Termux: `termux-setup-storage` (allow files access).
3. Copy the export to the new phone's Downloads folder, e.g. `torus-{date}.tar.gz`
   (plus its `.sha256` / `.sig` files if you have them).
4. Install TORUS itself (one line, from torus/INSTALL.md):
   `curl -fsSL https://raw.githubusercontent.com/FiliusElanOfYah/Torus/main/torus/bootstrap.sh | bash`
5. Unpack your setup over it:  `torus restore ~/storage/downloads/torus-{date}.tar.gz`
   (it checks the hash first and backs up anything it overwrites)
6. Reinstall packages and Python modules, then every daemon:
   `torus install all`
   Package names are in `~/.torus/etc/pkg.manifest`, Python pins in `~/.torus/etc/requirements.freeze`.
7. Keys are not in the export unless you used `--with-keys`:  `torus key set anthropic`
8. Seal and audit:  `torus seal`  then  `torus audit`
9. `torus doctor` must show no failures. Reboot once and run `torus status`.
"""


def write_docs():
    DOCS.mkdir(parents=True, exist_ok=True)
    atomic_write(DOCS / "RESTORE.md", RESTORE_MD.format(date=time.strftime("%Y%m%d")))
    return DOCS / "RESTORE.md"


def write_pins():
    """etc/requirements.freeze and etc/pkg.manifest. Returns (n_pip, n_pkg)."""
    n_pip = n_pkg = 0
    try:
        out = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, timeout=120).stdout
        lines = [ln for ln in out.splitlines() if ln.strip()]
        atomic_write(ETC / "requirements.freeze", "\n".join(lines) + "\n")
        n_pip = len(lines)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f", "${Package}=${Version}\n"], capture_output=True, text=True, timeout=60).stdout
        lines = [ln for ln in out.splitlines() if ln.strip()]
        atomic_write(ETC / "pkg.manifest", "# Termux packages at export time (pkg install <name> to get them back)\n" + "\n".join(lines) + "\n")
        n_pkg = len(lines)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return n_pip, n_pkg


def export_dir(to=None):
    if to:
        return Path(to)
    if os.environ.get("TORUS_EXPORT_DIR"):
        return Path(os.environ["TORUS_EXPORT_DIR"])
    dl = HOMEDIR / "storage" / "downloads"
    return dl if dl.is_dir() else HOME / "exports"


SECRET_PATHS = (".torus/airgap/mldsa65.sk", ".torus/airgap/seal.hmac", ".torus/etc/gateway.token")
SEAL_PUBLIC = (".torus/airgap/mldsa65.pk", ".torus/airgap/MANIFEST.json", ".torus/airgap/MANIFEST.sig")


def _filter(with_keys):
    def f(ti):
        parts = ti.name.split("/")
        if any(p in EXCLUDE_NAMES for p in parts) or ti.name.endswith((".pyc", ".tmp")):
            return None
        if ti.isfile() and not with_keys and (ti.name.endswith(".key") or ti.name in SECRET_PATHS):
            return None
        return ti
    return f


def export(with_keys=False, with_vault=True, to=None):
    (HOME / "var").mkdir(parents=True, exist_ok=True)
    with open(HOME / "var" / "export.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # exports run one at a time (button + CLI)
        return _export(with_keys, with_vault, to)


def _export(with_keys, with_vault, to):
    write_docs()
    n_pip, n_pkg = write_pins()
    dest_dir = export_dir(to)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"torus-{stamp}.tar.gz"
    n = 2
    while dest.exists():
        dest = dest_dir / f"torus-{stamp}-{n}.tar.gz"
        n += 1
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    members, size_in = 0, 0
    with tarfile.open(tmp, "w:gz", dereference=True) as tar:
        def add(path, arc):
            nonlocal members
            if not Path(path).exists():
                return
            before = len(tar.getmembers())
            tar.add(str(path), arcname=arc, filter=_filter(with_keys))
            members += len(tar.getmembers()) - before

        for name in INCLUDE:
            add(HOME / name, f".torus/{name}")
        add(AIRGAP, ".torus/airgap")
        add(INDEX_DB, ".torus/index.sqlite")
        if with_vault:
            add(VAULT, ".torus/vault")
        add(BOOT_DIR, ".termux/boot")
        for m in tar.getmembers():
            size_in += m.size
    os.replace(tmp, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    (dest.with_name(dest.name + ".sha256")).write_text(f"{digest}  {dest.name}\n")
    sig = ""
    if torus_seal.PK.exists() or torus_seal.HMAC_KEY.exists():
        sig = str(torus_seal.sign_file(dest))
    info = {"path": str(dest), "bytes": dest.stat().st_size, "unpacked_bytes": size_in, "members": members,
            "sha256": digest, "sig": sig, "with_keys": with_keys, "with_vault": with_vault,
            "pip_pins": n_pip, "pkg_pins": n_pkg, "created": time.time()}
    atomic_write(HOME / "var" / "last_export.json", json.dumps(info))
    return info


def list_exports(to=None):
    d = export_dir(to)
    out = []
    for p in sorted(d.glob("torus-*.tar.gz"), reverse=True) if d.is_dir() else []:
        out.append({"path": str(p), "name": p.name, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime,
                    "signed": p.with_name(p.name + ".sig").exists()})
    return out


def check_tarball(path):
    """Return (ok, messages). Hash from .sha256 and, when possible, the signature."""
    path, msgs, ok = Path(path), [], True
    if not path.is_file():
        return False, [f"no such file: {path}"]
    sha = path.with_name(path.name + ".sha256")
    if sha.exists():
        want = sha.read_text().split()[0]
        have = hashlib.sha256(path.read_bytes()).hexdigest()
        if want == have:
            msgs.append("sha256 matches")
        else:
            ok = False
            msgs.append("sha256 does NOT match: the file is damaged or was swapped")
    else:
        msgs.append("no .sha256 file next to it (hash not checked)")
    if path.with_name(path.name + ".sig").exists():
        good, why = torus_seal.verify_file(path)
        if good:
            msgs.append(f"signature verifies ({torus_seal.fingerprint()})")
        elif torus_seal.PK.exists() or torus_seal.HMAC_KEY.exists():
            msgs.append("signature does not verify: " + why + " (expected when restoring onto a different phone)")
        else:
            msgs.append("signature present but this phone has no seal key yet (not checked)")
    return ok, msgs


def restore(path, yes=False):
    path = Path(path)
    ok, msgs = check_tarball(path)
    for m in msgs:
        print("  " + m)
    if not ok:
        sys.exit("FAIL refusing to restore a damaged file")
    with tarfile.open(path, "r:gz") as tar:
        members = [m for m in tar.getmembers()]
        names = [m.name for m in members if m.isfile()]
        bad = [n for n in names if n.startswith("/") or ".." in n.split("/") or not n.startswith((".torus/", ".termux/"))]
        if bad:
            sys.exit(f"FAIL the archive contains paths outside ~/.torus and ~/.termux: {bad[:3]}")
        keys = [n for n in names if n.startswith(".torus/airgap/") and n.endswith(".key")]
        if ".torus/airgap/mldsa65.sk" not in names:
            # no secret seal key inside: the public key and manifest would not match this phone's own key
            members = [m for m in members if m.name not in SEAL_PUBLIC]
            names = [m.name for m in members if m.isfile()]
            print("  seal public key and manifest skipped (export has no seal secret); run torus seal afterwards")
        print(f"  {len(names)} files -> {HOMEDIR}" + (f"   (includes {len(keys)} API key file(s))" if keys else ""))
        if not yes:
            ans = input("  overwrite existing files? Old copies go to ~/.torus/backups/restore-<time>/  [y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                sys.exit("cancelled")
        bdir = HOME / "backups" / f"restore-{time.strftime('%Y%m%d-%H%M%S')}"
        saved = 0
        for n in names:
            cur = HOMEDIR / n
            if cur.is_file():
                dst = bdir / n
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cur, dst)
                saved += 1
        if saved:
            (bdir / "FILES").write_text("".join(f"{HOMEDIR / n}\n" for n in names if (HOMEDIR / n).is_file()))
        if hasattr(tarfile, "data_filter"):
            tar.extractall(HOMEDIR, members=members, filter="data")
        else:
            tar.extractall(HOMEDIR, members=members)
    for k in AIRGAP.glob("*.key"):
        os.chmod(k, 0o600)
    if AIRGAP.is_dir():
        os.chmod(AIRGAP, 0o700)
    print(f"OK   restored {len(names)} files" + (f"; {saved} old copies in {bdir}" if saved else ""))
    print("next:  torus install all    then  torus seal  and  torus audit")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "export"
    if cmd == "export":
        to = args[args.index("--to") + 1] if "--to" in args else None
        info = export(with_keys="--with-keys" in args, with_vault="--no-vault" not in args, to=to)
        print(f"OK   {info['path']}  ({info['bytes'] / 1e6:.1f} MB, {info['members']} files, {info['pip_pins']} pip pins, {info['pkg_pins']} packages)")
        print(f"     sha256 {info['sha256'][:16]}...  " + ("signed: " + Path(info["sig"]).name if info["sig"] else "unsigned (run torus seal first to sign exports)"))
        if not info["with_keys"]:
            print("     API keys, the seal secret and the gateway token are NOT inside. Add them with --with-keys only onto media you trust.")
        print("     restore on another phone: torus restore <this file>   (steps in ~/.torus/docs/RESTORE.md)")
    elif cmd == "list":
        rows = list_exports()
        for r in rows:
            print(f"  {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['mtime']))}  {r['bytes'] / 1e6:6.1f} MB  {'signed  ' if r['signed'] else 'unsigned'}  {r['path']}")
        if not rows:
            print("  no exports yet: torus export")
    elif cmd == "docs":
        print("wrote", write_docs())
    elif cmd == "check" and len(args) > 1:
        ok, msgs = check_tarball(args[1])
        print("\n".join("  " + m for m in msgs))
        sys.exit(0 if ok else 1)
    elif cmd == "restore" and len(args) > 1:
        restore(args[1], yes="--yes" in args)
    else:
        sys.exit("usage: torus_export.py export [--with-keys] [--no-vault] [--to DIR] | list | check <file> | restore <file> [--yes] | docs")


if __name__ == "__main__":
    main()
