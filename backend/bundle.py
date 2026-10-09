"""
Phase-bundle ZIP generator.

For a given phase number, assemble install.sh (concatenated commands),
README.md (phase overview), and any artifacts declared on the phase catalog
into an in-memory ZIP that gatewayd streams back to the browser.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from phases import get_phase


def _install_sh(phase: dict) -> str:
    lines = [
        "#!/data/data/com.termux/files/usr/bin/sh",
        "# TORUS — generated bundle",
        f"# Phase {phase['number']}: {phase['name']}",
        f"# Goal: {phase['goal']}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        "set -euo pipefail",
        "",
    ]
    for step in phase["steps"]:
        lines.append(f"# ── Step {step['id']}: {step['title']} ──")
        lines.append(step["command"])
        lines.append("# verify:")
        lines.append(f"# {step['verify']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _readme(phase: dict) -> str:
    lines = [
        f"# TORUS — Phase {phase['number']}: {phase['name']}",
        "",
        f"**Goal:** {phase['goal']}",
        "",
        f"**Gate:** {phase['gate']}",
        "",
        "## Steps",
        "",
    ]
    for step in phase["steps"]:
        lines.append(f"### {step['id']} — {step['title']}")
        lines.append("")
        lines.append("```sh")
        lines.append(step["command"])
        lines.append("```")
        lines.append("")
        lines.append(f"**Expected:** {step['expected']}")
        lines.append("")
        lines.append(f"**Verify:** `{step['verify']}`")
        lines.append("")
        if step.get("troubleshooting"):
            lines.append(f"> _Troubleshooting:_ {step['troubleshooting']}")
            lines.append("")
    if phase.get("opsec_checklist"):
        lines.append("## OPSEC Checklist")
        lines.append("")
        for item in phase["opsec_checklist"]:
            lines.append(f"- [ ] {item['label']}")
        lines.append("")
    return "\n".join(lines)


def build_phase_bundle(phase_number: int) -> tuple[bytes, str]:
    """Return (zip_bytes, filename). Raises ValueError on unknown phase."""
    phase = get_phase(phase_number)
    if not phase:
        raise ValueError(f"unknown phase {phase_number}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        root = f"torus-phase-{phase_number}"
        z.writestr(f"{root}/README.md", _readme(phase))
        z.writestr(f"{root}/install.sh", _install_sh(phase))
        for rel_path, content in (phase.get("artifacts") or {}).items():
            z.writestr(f"{root}/{rel_path}", content)
    buf.seek(0)
    return buf.read(), f"torus-phase-{phase_number}.zip"


def build_full_mesh_bundle() -> tuple[bytes, str]:
    """Mega bundle: every phase folded together."""
    from phases import get_all_phases

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        root = "torus-mesh-complete"
        master = [
            "#!/data/data/com.termux/files/usr/bin/sh",
            "# TORUS — complete mesh bootstrap (all phases concatenated)",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            "set -euo pipefail",
            "",
        ]
        for phase in get_all_phases():
            folder = f"{root}/phase-{phase['number']}-{phase['name'].lower().replace(' ', '-').replace('(', '').replace(')', '')}"
            z.writestr(f"{folder}/README.md", _readme(phase))
            z.writestr(f"{folder}/install.sh", _install_sh(phase))
            for rel_path, content in (phase.get("artifacts") or {}).items():
                z.writestr(f"{folder}/{rel_path}", content)
            master.append(f"echo '=== Phase {phase['number']}: {phase['name']} ==='")
            master.append(f"sh {folder.replace(root + '/', '')}/install.sh")
            master.append("")
        z.writestr(f"{root}/bootstrap-all.sh", "\n".join(master) + "\n")
    buf.seek(0)
    return buf.read(), "torus-mesh-complete.zip"
