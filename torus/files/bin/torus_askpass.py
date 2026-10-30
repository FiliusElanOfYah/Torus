#!/usr/bin/env python3
"""torus_askpass - GIT_ASKPASS helper: git asks "Username for ..." then "Password for ..."; we answer with
x-access-token and the GitHub token fetched from airgapd (TORUS_ASKPASS_KEY, default github). The token is
never written to disk or to git's config; the clone keeps a plain https remote.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from torus_common import get_secret  # noqa: E402

prompt = " ".join(sys.argv[1:]).lower()
if "username" in prompt:
    print("x-access-token")
else:
    try:
        print(asyncio.run(get_secret(os.environ.get("TORUS_ASKPASS_KEY") or "github")))
    except Exception as e:  # git shows our stderr
        sys.exit(f"torus_askpass: {e}")
