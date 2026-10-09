"""
TORUS gateway — post-quantum signing module.

Uses CRYSTALS-Dilithium3 (FIPS 204 ML-DSA-65) for signing session manifests.
The gateway owns a stable keypair persisted on disk; both export and import
operations use this keypair. On first boot the keypair is generated and
written to TORUS_PQ_KEY_DIR; subsequent boots load it.

This matches the Phase-8 posture described in the setup manual: liboqs on
the device, Dilithium3 for signing airgap manifests. The gateway's own
manifest signatures use the same primitive so one key covers both airgap
tokens and session manifests.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from pqcrypto.sign import ml_dsa_65 as _dilithium

ALGORITHM = "ML-DSA-65 (Dilithium3)"


class _Keyring:
    def __init__(self, key_dir: Path):
        self.key_dir = key_dir
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self.pk_path = key_dir / "dilithium3.pk"
        self.sk_path = key_dir / "dilithium3.sk"
        if self.pk_path.exists() and self.sk_path.exists():
            self.pk = self.pk_path.read_bytes()
            self.sk = self.sk_path.read_bytes()
        else:
            self.pk, self.sk = _dilithium.keygen()
            self.pk_path.write_bytes(self.pk)
            self.sk_path.write_bytes(self.sk)
            # Best-effort — host FS may not support chmod (e.g. docker overlay)
            try:
                os.chmod(self.sk_path, 0o600)
            except OSError:
                pass

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.pk).hexdigest()[:12]

    def sign(self, payload: bytes) -> bytes:
        return _dilithium.sign(self.sk, payload)

    def verify(self, payload: bytes, signature: bytes) -> bool:
        try:
            _dilithium.verify(self.pk, payload, signature)
            return True
        except Exception:
            return False


_default_dir = Path(os.environ.get("TORUS_PQ_KEY_DIR", str(Path(__file__).parent / ".pq_keys")))
_keyring: _Keyring | None = None


def keyring() -> _Keyring:
    """Lazy singleton — created once per process."""
    global _keyring
    if _keyring is None:
        _keyring = _Keyring(_default_dir)
    return _keyring


def public_key_b64() -> str:
    return base64.b64encode(keyring().pk).decode("ascii")


def fingerprint() -> str:
    return keyring().fingerprint


def sign_b64(payload: bytes) -> str:
    return base64.b64encode(keyring().sign(payload)).decode("ascii")


def verify_b64(payload: bytes, signature_b64: str) -> bool:
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except Exception:
        return False
    return keyring().verify(payload, sig)
