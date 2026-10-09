"""
TORUS gateway — FastAPI backend.

Serves the static phase catalog, persists device-profile + per-step
verification progress in Mongo, and streams per-phase bundle ZIPs.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware

from phases import get_all_phases, get_daemons, get_phase
from bundle import build_full_mesh_bundle, build_phase_bundle

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]
SIGNING_KEY = os.environ["TORUS_SIGNING_KEY"].encode()

app = FastAPI(title="TORUS gateway")
api = APIRouter(prefix="/api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
log = logging.getLogger("torus.gateway")


# ─────────────────────────────── Models ───────────────────────────────


class DeviceProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_name: str = Field(default="Samsung Galaxy S26 Ultra")
    termux_source: str = Field(default="")  # f-droid | play | other
    android_api: str = Field(default="")
    free_disk_gb: str = Field(default="")
    acode_installed: bool = Field(default=False)
    termux_boot_installed: bool = Field(default=False)
    vault_location: str = Field(default="internal")  # internal | sdcard
    default_llm: str = Field(default="sonnet-4.5")
    opus_for_code: bool = Field(default=True)
    jev_gating: bool = Field(default=True)


class SessionDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    profile: DeviceProfile = Field(default_factory=DeviceProfile)
    verified_steps: list[str] = Field(default_factory=list)
    opsec_checked: list[str] = Field(default_factory=list)
    notes: dict[str, str] = Field(default_factory=dict)
    pinned_topics: list[dict] = Field(default_factory=list)


def _ser(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _ser(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_ser(v) for v in obj]
    return obj


def _deser(doc: dict) -> dict:
    doc = {k: v for k, v in doc.items() if k != "_id"}
    for key in ("created_at", "updated_at"):
        if isinstance(doc.get(key), str):
            doc[key] = datetime.fromisoformat(doc[key])
    return doc


# ─────────────────────────────── Routes ───────────────────────────────


@api.get("/")
async def root():
    return {"service": "torus-gateway", "ok": True}


@api.get("/phases")
async def list_phases():
    phases = get_all_phases()
    return {
        "count": len(phases),
        "phases": [
            {
                "number": p["number"],
                "name": p["name"],
                "goal": p["goal"],
                "gate": p["gate"],
                "step_count": len(p["steps"]),
                "has_opsec_checklist": bool(p.get("opsec_checklist")),
            }
            for p in phases
        ],
    }


@api.get("/phases/{number}")
async def get_phase_detail(number: int):
    phase = get_phase(number)
    if not phase:
        raise HTTPException(status_code=404, detail="phase not found")
    return phase


@api.get("/daemons")
async def list_daemons():
    return {"daemons": get_daemons()}


# ─── sessions ───


@api.post("/sessions")
async def create_session():
    session = SessionDoc()
    doc = _ser(session.model_dump())
    await db.sessions.insert_one(doc)
    return session.model_dump()


@api.get("/sessions/{session_id}")
async def get_session(session_id: str):
    doc = await db.sessions.find_one({"id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    return _deser(doc)


class ProfileUpdate(BaseModel):
    profile: DeviceProfile


@api.patch("/sessions/{session_id}/profile")
async def update_profile(session_id: str, body: ProfileUpdate):
    now = datetime.now(timezone.utc).isoformat()
    res = await db.sessions.update_one(
        {"id": session_id},
        {"$set": {"profile": body.profile.model_dump(), "updated_at": now}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="session not found")
    doc = await db.sessions.find_one({"id": session_id})
    return _deser(doc)


class StepToggle(BaseModel):
    step_id: str
    verified: bool


@api.post("/sessions/{session_id}/steps")
async def toggle_step(session_id: str, body: StepToggle):
    now = datetime.now(timezone.utc).isoformat()
    op = "$addToSet" if body.verified else "$pull"
    res = await db.sessions.update_one(
        {"id": session_id},
        {op: {"verified_steps": body.step_id}, "$set": {"updated_at": now}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="session not found")
    doc = await db.sessions.find_one({"id": session_id})
    return _deser(doc)


class OpsecToggle(BaseModel):
    item_id: str
    checked: bool


@api.post("/sessions/{session_id}/opsec")
async def toggle_opsec(session_id: str, body: OpsecToggle):
    now = datetime.now(timezone.utc).isoformat()
    op = "$addToSet" if body.checked else "$pull"
    res = await db.sessions.update_one(
        {"id": session_id},
        {op: {"opsec_checked": body.item_id}, "$set": {"updated_at": now}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="session not found")
    doc = await db.sessions.find_one({"id": session_id})
    return _deser(doc)


class NoteUpdate(BaseModel):
    step_id: str
    note: str


@api.post("/sessions/{session_id}/notes")
async def set_note(session_id: str, body: NoteUpdate):
    # NOTE: step_ids contain '.' (e.g. "0.1"). Using {"$set": {f"notes.{step_id}": ...}}
    # makes Mongo interpret the dot as nested-path traversal, which corrupts the shape.
    # Read-modify-write keeps the literal key intact.
    now = datetime.now(timezone.utc).isoformat()
    doc = await db.sessions.find_one({"id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    notes = dict(doc.get("notes") or {})
    if body.note:
        notes[body.step_id] = body.note
    else:
        notes.pop(body.step_id, None)
    await db.sessions.update_one(
        {"id": session_id},
        {"$set": {"notes": notes, "updated_at": now}},
    )
    doc = await db.sessions.find_one({"id": session_id})
    return _deser(doc)


# ─── bundles ───


@api.get("/bundle/phase/{number}")
async def download_phase_bundle(number: int):
    try:
        data, name = build_phase_bundle(number)
    except ValueError:
        raise HTTPException(status_code=404, detail="phase not found")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@api.get("/bundle/mesh")
async def download_full_bundle():
    data, name = build_full_mesh_bundle()
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ─── progress summary ───


@api.get("/sessions/{session_id}/progress")
async def progress(session_id: str):
    doc = await db.sessions.find_one({"id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    verified = set(doc.get("verified_steps") or [])
    per_phase = []
    for p in get_all_phases():
        step_ids = [s["id"] for s in p["steps"]]
        done = sum(1 for s in step_ids if s in verified)
        per_phase.append(
            {
                "number": p["number"],
                "name": p["name"],
                "total": len(step_ids),
                "verified": done,
                "complete": done == len(step_ids),
            }
        )
    total = sum(x["total"] for x in per_phase)
    done_total = sum(x["verified"] for x in per_phase)
    return {
        "overall": {
            "total_steps": total,
            "verified_steps": done_total,
            "percent": round(100 * done_total / total, 1) if total else 0,
        },
        "phases": per_phase,
    }


# ─── pinned topics (oracled seed config) ───


class PinnedTopic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    seeds: list[str] = Field(default_factory=list)


class PinnedUpdate(BaseModel):
    topics: list[PinnedTopic]


DEFAULT_PINNED_TOPICS: list[dict] = [
    {
        "name": "post-quantum-crypto",
        "seeds": [
            "https://openquantumsafe.org/",
            "https://pq-crystals.org/",
        ],
    }
]


@api.get("/sessions/{session_id}/pinned")
async def get_pinned(session_id: str):
    doc = await db.sessions.find_one({"id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    topics = doc.get("pinned_topics") or DEFAULT_PINNED_TOPICS
    return {"topics": topics}


@api.put("/sessions/{session_id}/pinned")
async def set_pinned(session_id: str, body: PinnedUpdate):
    now = datetime.now(timezone.utc).isoformat()
    topics = [t.model_dump() for t in body.topics]
    res = await db.sessions.update_one(
        {"id": session_id},
        {"$set": {"pinned_topics": topics, "updated_at": now}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="session not found")
    return {"topics": topics}


@api.get("/sessions/{session_id}/pinned.yaml")
async def download_pinned_yaml(session_id: str):
    doc = await db.sessions.find_one({"id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    topics = doc.get("pinned_topics") or DEFAULT_PINNED_TOPICS
    payload = {"topics": topics}
    body = (
        "# TORUS — oracled pinned-topics config\n"
        "# Drop this at ~/.torus/etc/pinned.yaml on the device.\n"
        "# oracled wakes once per hour and refills the vault for every seed.\n\n"
        + yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
    )
    return Response(
        content=body,
        media_type="application/x-yaml",
        headers={"Content-Disposition": 'attachment; filename="pinned.yaml"'},
    )


# ─── shareable signed manifest ───


def _sign(payload: bytes) -> str:
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


class ManifestImport(BaseModel):
    manifest: dict


@api.get("/sessions/{session_id}/manifest")
async def export_manifest(session_id: str):
    import json as _json

    doc = await db.sessions.find_one({"id": session_id})
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    body = {
        "schema": "torus.session.manifest/v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "session": {
            "id": doc["id"],
            "profile": doc.get("profile", {}),
            "verified_steps": sorted(doc.get("verified_steps") or []),
            "opsec_checked": sorted(doc.get("opsec_checked") or []),
            "notes": doc.get("notes") or {},
            "pinned_topics": doc.get("pinned_topics") or [],
        },
    }
    canonical = _json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["signature"] = {
        "algo": "HMAC-SHA256",
        "value": _sign(canonical),
    }
    pretty = _json.dumps(body, indent=2, sort_keys=False)
    return Response(
        content=pretty,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="torus-session-{doc["id"][:8]}.json"'
        },
    )


@api.post("/sessions/import")
async def import_manifest(body: ManifestImport):
    import json as _json

    m = body.manifest
    if m.get("schema") != "torus.session.manifest/v1":
        raise HTTPException(status_code=400, detail="unsupported manifest schema")
    sig = m.get("signature")
    if not sig or sig.get("algo") != "HMAC-SHA256":
        raise HTTPException(status_code=400, detail="missing or unsupported signature")
    provided = sig.get("value", "")
    unsigned = {k: v for k, v in m.items() if k != "signature"}
    canonical = _json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    expected = _sign(canonical)
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=400, detail="signature verification failed")
    src = m.get("session") or {}
    new = SessionDoc()
    doc = _ser(new.model_dump())
    doc["profile"] = {**doc["profile"], **(src.get("profile") or {})}
    doc["verified_steps"] = list(src.get("verified_steps") or [])
    doc["opsec_checked"] = list(src.get("opsec_checked") or [])
    doc["notes"] = dict(src.get("notes") or {})
    doc["pinned_topics"] = list(src.get("pinned_topics") or [])
    await db.sessions.insert_one(doc)
    return _deser(doc)


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
