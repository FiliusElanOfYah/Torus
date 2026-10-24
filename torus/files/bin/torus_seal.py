#!/usr/bin/env python3
"""torus_seal - sign the airgap manifest so tampered keys or daemons are noticed.

Phase 8. The manifest (~/.torus/airgap/MANIFEST.json) lists the SHA3-256 of every
API key file and every installed daemon (~/.torus/bin/*.py). It is signed with
ML-DSA-65 (FIPS 204, formerly Dilithium3) using a keypair that never leaves the
phone: airgap/mldsa65.sk (mode 600) + airgap/mldsa65.pk.

  torus seal      write and sign the manifest (after torus install / torus key set)
  torus seal verify   check it (airgapd also checks before handing out a key)

Without the pure-Python `dilithium-py` package the seal falls back to HMAC-SHA256
with a random 0600 secret (airgap/seal.hmac). Same tamper evidence, not post-quantum;
`torus install 8` installs dilithium-py so this is rare.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import AIRGAP, ETC, HOME, atomic_write  # noqa: E402

PK = AIRGAP / "mldsa65.pk"
SK = AIRGAP / "mldsa65.sk"
HMAC_KEY = AIRGAP / "seal.hmac"
MANIFEST = AIRGAP / "MANIFEST.json"
SIG = AIRGAP / "MANIFEST.sig"
BIN = HOME / "bin"


def _mldsa():
    try:
        from dilithium_py.ml_dsa import ML_DSA_65
        return ML_DSA_65
    except ImportError:
        return None


def algo():
    return "ML-DSA-65" if _mldsa() else "HMAC-SHA256"


def sha3(path):
    h = hashlib.sha3_256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_secret(path, data):
    AIRGAP.mkdir(parents=True, exist_ok=True)
    os.chmod(AIRGAP, 0o700)
    old = os.umask(0o177)
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)


def _pair_works():
    m = _mldsa()
    if not (m and PK.exists() and SK.exists()):
        return False
    try:
        probe = b"torus-seal-probe"
        return bool(m.verify(PK.read_bytes(), probe, m.sign(SK.read_bytes(), probe)))
    except Exception:
        return False


def keygen():
    """Create the signing key if missing or mismatched. Returns the public-key fingerprint."""
    m = _mldsa()
    if m:
        if not _pair_works():
            pk, sk = m.keygen()
            _write_secret(SK, sk)
            PK.write_bytes(pk)
    elif not HMAC_KEY.exists():
        _write_secret(HMAC_KEY, secrets.token_bytes(32))
    return fingerprint()


def fingerprint():
    """Short id of the public key. Write it on paper; `torus seal verify` prints it so you can compare."""
    if PK.exists():
        return hashlib.sha256(PK.read_bytes()).hexdigest()[:16]
    if HMAC_KEY.exists():
        return "hmac-" + hashlib.sha256(HMAC_KEY.read_bytes()).hexdigest()[:11]
    return ""


def sign_bytes(data):
    m = _mldsa()
    if m and SK.exists():
        return m.sign(SK.read_bytes(), data)
    if HMAC_KEY.exists():
        return hmac.new(HMAC_KEY.read_bytes(), data, hashlib.sha256).digest()
    raise RuntimeError("no signing key: run  torus seal")


def verify_bytes(data, sig):
    m = _mldsa()
    if PK.exists():
        if not m:
            raise RuntimeError("manifest is ML-DSA signed but dilithium-py is not installed: torus install 8")
        return bool(m.verify(PK.read_bytes(), data, sig))
    if HMAC_KEY.exists():
        return hmac.compare_digest(hmac.new(HMAC_KEY.read_bytes(), data, hashlib.sha256).digest(), sig)
    return False


def protected_files():
    """relative path -> absolute path for everything the seal covers."""
    out = {}
    for p in sorted(AIRGAP.glob("*.key")):
        out[f"airgap/{p.name}"] = p
    if PK.exists():
        out["airgap/mldsa65.pk"] = PK
    tok = ETC / "gateway.token"
    if tok.exists():
        out["etc/gateway.token"] = tok
    for p in sorted(BIN.glob("*.py")):
        out[f"bin/{p.name}"] = p
    return out


def seal():
    keygen()
    files = {rel: sha3(p) for rel, p in protected_files().items()}
    man = {"version": 1, "algo": algo(), "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "fingerprint": fingerprint(), "files": files}
    data = json.dumps(man, sort_keys=True, separators=(",", ":")).encode()
    sig = sign_bytes(data)
    atomic_write(MANIFEST, data.decode())
    SIG.write_bytes(sig)
    return man


def is_sealed():
    return MANIFEST.exists() and SIG.exists()


def verify(check_files=True):
    """Return {ok, sealed, algo, fingerprint, changed, missing, added, error}."""
    res = {"ok": False, "sealed": is_sealed(), "algo": algo(), "fingerprint": fingerprint(),
           "changed": [], "missing": [], "added": [], "error": ""}
    if not res["sealed"]:
        res["error"] = "not sealed yet: torus seal"
        return res
    data = MANIFEST.read_bytes()
    try:
        man = json.loads(data)
        good = verify_bytes(data, SIG.read_bytes())
    except (ValueError, RuntimeError) as e:
        res["error"] = f"cannot read the seal ({e})"
        return res
    if not good:
        res["error"] = "signature does NOT verify: the manifest or the key was changed"
        return res
    res["algo"], res["created"] = man.get("algo", res["algo"]), man.get("created", "")
    if check_files:
        want, have = man.get("files", {}), protected_files()
        for rel, digest in want.items():
            p = have.get(rel)
            if p is None:
                res["missing"].append(rel)
            elif sha3(p) != digest:
                res["changed"].append(rel)
        res["added"] = sorted(set(have) - set(want))
        if res["changed"] or res["missing"] or res["added"]:
            res["error"] = ("files differ from the sealed manifest: "
                            + ", ".join(res["changed"] + [m + " (missing)" for m in res["missing"]]
                                        + [a + " (new)" for a in res["added"]]) + ". If you changed them on purpose: torus seal")
            return res
    res["ok"] = True
    return res


def has_signing_key():
    return SK.exists() or HMAC_KEY.exists()


class KeyGuard:
    """Used by airgapd: is this key file still the one that was sealed?
    Re-reads the manifest only when it changes on disk."""

    def __init__(self):
        self._stamp, self._man, self._sig_ok, self._err = None, {}, False, ""

    def _refresh(self):
        if not is_sealed():
            self._stamp, self._man, self._sig_ok, self._err = None, {}, False, ""
            return
        st = MANIFEST.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        if stamp == self._stamp:
            return
        self._stamp = stamp
        data = MANIFEST.read_bytes()
        try:
            self._man = json.loads(data)
            self._sig_ok = verify_bytes(data, SIG.read_bytes())
            self._err = "" if self._sig_ok else "manifest signature does not verify"
        except (ValueError, RuntimeError) as e:
            self._man, self._sig_ok, self._err = {}, False, str(e)

    def check(self, key_path):
        """Return '' when the key may be served, else the reason to refuse."""
        self._refresh()
        if not is_sealed():
            # a signing key without a manifest means the seal was removed: fail closed
            return "this phone has a seal key but no sealed manifest (torus seal)" if has_signing_key() else ""
        if not self._sig_ok:
            return self._err or "bad seal"
        want = self._man.get("files", {}).get(f"airgap/{key_path.name}")
        if want is None:
            return "key is not in the sealed manifest (torus seal)"
        if sha3(key_path) != want:
            return "key file changed since it was sealed (torus seal)"
        return ""


def sign_file(path):
    """Detached signature for a file (used for export tarballs). Returns the .sig path."""
    sig = sign_bytes(hashlib.sha3_256(open(path, "rb").read()).digest())
    out = path.with_name(path.name + ".sig")
    out.write_text(json.dumps({"algo": algo(), "fingerprint": fingerprint(),
                               "sha3_256": sha3(path), "sig": base64.b64encode(sig).decode()}) + "\n")
    return out


def verify_file(path, sig_path=None):
    sig_path = sig_path or path.with_name(path.name + ".sig")
    try:
        meta = json.loads(sig_path.read_text())
        digest = sha3(path)
        if digest != meta.get("sha3_256"):
            return False, "file hash differs from the signature file"
        ok = verify_bytes(bytes.fromhex(digest), base64.b64decode(meta["sig"]))
        return ok, "" if ok else f"signature does not verify (signed by {meta.get('fingerprint')}, this phone is {fingerprint()})"
    except FileNotFoundError:
        return False, "no .sig file next to it"
    except (ValueError, KeyError, RuntimeError) as e:
        return False, f"unreadable signature ({e})"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seal"
    quiet = "--quiet" in sys.argv
    if cmd == "seal":
        man = seal()
        if not quiet:
            print(f"sealed {len(man['files'])} files with {man['algo']}   key fingerprint: {man['fingerprint']}")
            print("write the fingerprint on paper; `torus seal verify` must always print the same one")
    elif cmd == "verify":
        r = verify()
        print(("OK   " if r["ok"] else "FAIL ") + (f"seal verifies ({r['algo']}, sealed {r.get('created', '')}, fingerprint {r['fingerprint']})"
                                                     if r["ok"] else r["error"]))
        sys.exit(0 if r["ok"] else 1)
    elif cmd == "fingerprint":
        print(fingerprint() or "(no key yet: torus seal)")
    elif cmd == "sign" and len(sys.argv) > 2:
        from pathlib import Path
        print("signature written:", sign_file(Path(sys.argv[2])))
    elif cmd == "verify-file" and len(sys.argv) > 2:
        from pathlib import Path
        ok, why = verify_file(Path(sys.argv[2]))
        print("OK   signature verifies" if ok else "FAIL " + why)
        sys.exit(0 if ok else 1)
    else:
        sys.exit("usage: torus_seal.py seal|verify|fingerprint|sign <file>|verify-file <file>")


if __name__ == "__main__":
    main()
