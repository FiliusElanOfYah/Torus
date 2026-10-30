"""Phase 2b: uploads. gateway_upload begin/chunk/finish/abort/resume + limits, zip listing + safe unpack, project
destination, gatewayd raw-body routing behind the token guard, operatord adopting a batch folder as the task's uploads,
prune, CLI local_copy, wiring."""
import asyncio
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "torus" / "files" / "bin"
HOME = Path("/tmp/torus-up-test")
os.environ["TORUS_HOME"] = str(HOME)
sys.path.insert(0, str(BIN))
import gateway_upload as up  # noqa: E402
import operatord as op  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    import shutil
    if HOME.exists():
        shutil.rmtree(HOME)
    for sub in ("etc", "logs", "var/operator/snapshots", "vault/answers", "airgap", "work", "projects"):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    (HOME / "etc" / "coder.conf").write_text(f"root={HOME / 'projects'}\n")
    (HOME / "etc" / "operator.conf").write_text(f"ask_every_change=false\ndefault_budget_usd=1\nmodel=sonnet\nmax_steps=20\ncwd={HOME / 'work'}\n")
    monkeypatch.setattr(up, "UPLOADS", HOME / "uploads")
    monkeypatch.setattr(op, "CONF", HOME / "etc" / "operator.conf")
    monkeypatch.setattr(op, "DIR", HOME / "var" / "operator")
    monkeypatch.setattr(op, "SNAP", HOME / "var" / "operator" / "snapshots")
    monkeypatch.setattr(op, "ANSWERS", HOME / "vault" / "answers")
    monkeypatch.setattr(op, "TERMUX_HOME", HOME)
    monkeypatch.setattr(op, "PROTECTED", [HOME / "airgap", HOME / "etc" / "gateway.token", HOME / "var" / "operator"])
    monkeypatch.setattr(op, "SHARED", [HOME / "shared"])
    up.SESSIONS.clear()
    op.TASKS.clear(); op.EVENTS.clear(); op.RUN.clear()
    yield


def R(coro):
    return asyncio.run(coro)


def make_zip(entries, bad=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if bad:
            zf.writestr("../escape.txt", "x")
            zf.writestr("/abs.txt", "x")
    return buf.getvalue()


def upload(data, name, batch="", project="", unpack=False, chunk=None, sha=True):
    st, b = R(up.route("POST", "/api/upload/begin", {}, {"name": name, "size": len(data), "batch": batch, "project": project, "unpack": unpack,
                                                          "sha256": hashlib.sha256(data).hexdigest() if sha else ""}))
    assert st == 200, b
    off, step = b["offset"], chunk or b["chunk"]
    while off < len(data):
        st, c = R(up.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": [str(off)]}, {"_raw": data[off:off + step]}))
        assert st == 200, c
        off = c["offset"]
    st, f = R(up.route("POST", f"/api/upload/{b['id']}/finish", {}, {}))
    return st, f, b


# ------------------------------------------------------------ gateway_upload
def test_begin_chunk_finish_and_listing():
    data = os.urandom(3000)
    st, f, b = upload(data, "notes.bin", chunk=1000)
    assert st == 200 and f["size"] == 3000 and f["name"] == "notes.bin" and f["batch"].startswith("b-")
    p = Path(f["path"])
    assert p.read_bytes() == data and oct(p.stat().st_mode)[-3:] == "600" and p.parent == HOME / "uploads" / f["batch"]
    assert oct((HOME / "uploads").stat().st_mode)[-3:] == "700"
    st, f2, _ = upload(b"second", "notes.bin", batch=f["batch"])
    assert f2["name"] == "notes-2.bin"
    st, lst = R(up.route("GET", f"/api/upload/{f['batch']}", {}, {}))
    assert st == 200 and [x["name"] for x in lst["files"]] == ["notes-2.bin", "notes.bin"] and lst["bytes"] == 3006
    assert not up.SESSIONS


def test_limits_names_offsets_checksum_abort():
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": "x", "size": up.MAX_FILE + 1}))
    assert st == 413
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": "../x", "size": 1}))
    assert st == 200 and up.SESSIONS[e["id"]]["name"] == "x"  # path parts are stripped, plain name kept
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": "a/", "size": 1}))
    assert st == 400
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": ".hidden", "size": 1}))
    assert st == 400
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": "x", "size": 1, "batch": "zzz"}))
    assert st == 400
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": "x", "size": 1, "project": "bad name!"}))
    assert st == 400
    # batch cap: pretend 499.99 MB already there
    (HOME / "uploads" / "b-00000000").mkdir(parents=True)
    big = HOME / "uploads" / "b-00000000" / "big"
    with open(big, "wb") as fh:
        fh.truncate(up.MAX_BATCH - 10)
    st, e = R(up.route("POST", "/api/upload/begin", {}, {"name": "x", "size": 11, "batch": "b-00000000"}))
    assert st == 413 and "500 MB" in e["error"]
    # offsets + oversize
    st, b = R(up.route("POST", "/api/upload/begin", {}, {"name": "f.txt", "size": 10}))
    st, c = R(up.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": ["5"]}, {"_raw": b"abcde"}))
    assert st == 409 and c["offset"] == 0
    st, c = R(up.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": ["0"]}, {"_raw": b"a" * 11}))
    assert st == 400
    st, c = R(up.route("POST", f"/api/upload/{b['id']}/finish", {}, {}))
    assert st == 409 and "0 of 10" in c["error"]
    st, c = R(up.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": ["0"]}, {"_raw": b"a" * 10}))
    assert c["done"] is True
    st, c = R(up.route("POST", f"/api/upload/{b['id']}/finish", {}, {"sha256": "00" * 32}))
    assert st == 400 and "checksum" in c["error"] and not list((HOME / "uploads" / b["batch"]).glob("f.txt*"))
    st, b = R(up.route("POST", "/api/upload/begin", {}, {"name": "g.txt", "size": 10}))
    assert (HOME / "uploads" / b["batch"] / "g.txt.part").exists()
    st, _ = R(up.route("POST", f"/api/upload/{b['id']}/abort", {}, {}))
    assert not (HOME / "uploads" / b["batch"] / "g.txt.part").exists()
    st, _ = R(up.route("GET", "/api/upload/u-000000000000/finish", {}, {}))
    assert st == 405
    st, _ = R(up.route("POST", "/api/upload/u-000000000000/finish", {}, {}))
    assert st == 404


def test_resume_same_name_returns_offset():
    data = b"x" * 100
    st, b = R(up.route("POST", "/api/upload/begin", {}, {"name": "r.txt", "size": 100}))
    R(up.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": ["0"]}, {"_raw": data[:40]}))
    st, b2 = R(up.route("POST", "/api/upload/begin", {}, {"name": "r.txt", "size": 100, "batch": b["batch"]}))
    assert b2["id"] == b["id"] and b2["offset"] == 40 and b2["resumed"] is True
    st, lst = R(up.route("GET", f"/api/upload/{b['batch']}", {}, {}))
    assert lst["partial"][0]["offset"] == 40
    R(up.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": ["40"]}, {"_raw": data[40:]}))
    st, f = R(up.route("POST", f"/api/upload/{b['id']}/finish", {}, {}))
    assert st == 200 and Path(f["path"]).read_bytes() == data


def test_zip_listing_kept_and_safe_unpack():
    z = make_zip({"proj/README.md": "hi", "proj/src/a.py": "print(1)", "proj/empty/": ""}, bad=True)
    st, f, _ = upload(z, "proj.zip", unpack=True)
    assert st == 200 and f["zip"]["files"] == 4 and "proj/README.md" in f["zip"]["list"]
    d = Path(f["path"]).parent
    assert (d / "proj.zip").exists() and (d / "proj.zip.list").read_text().startswith("# proj.zip: 4 file(s)")
    assert f["unpacked"] == 2 and f["unpacked_to"] == str(d / "proj")
    assert (d / "proj" / "proj" / "src" / "a.py").read_text() == "print(1)"
    assert not (d / "escape.txt").exists() and not (HOME / "uploads" / "escape.txt").exists() and not (d / "proj" / "abs.txt").exists()
    st, lst = R(up.route("GET", f"/api/upload/{f['batch']}", {}, {}))
    row = next(x for x in lst["files"] if x["name"] == "proj.zip")
    assert row["zip"]["summary"].startswith("proj.zip: 4 file(s)") and "proj/src/a.py" in row["zip"]["list"]
    text = up.files_text(d)
    assert "proj.zip (" in text and "zip: proj.zip: 4 file(s)" in text and "proj.zip.list" in text


def test_zip_caps(monkeypatch):
    monkeypatch.setattr(up, "ZIP_MAX_ENTRIES", 2)
    z = make_zip({"a": "1", "b": "2", "c": "3"})
    st, f, _ = upload(z, "many.zip", unpack=True)
    assert st == 200 and "not unpacked" in f["warning"] and "entries" in f["warning"] and Path(f["path"]).exists()
    monkeypatch.setattr(up, "ZIP_MAX_ENTRIES", 20000)
    monkeypatch.setattr(up, "ZIP_MAX_BYTES", 5)
    st, f, _ = upload(make_zip({"a": "123456"}), "fat.zip", unpack=True)
    assert "1 GB" in f["warning"]


def test_project_destination():
    z = make_zip({"app.py": "print('x')"})
    st, f, b = upload(z, "app.zip", project="demo", unpack=True)
    assert st == 200 and b["batch"] == "" and f["path"] == str(HOME / "projects" / "demo" / "app.zip")
    assert (HOME / "projects" / "demo" / "app.py").read_text() == "print('x')"
    assert oct(Path(f["path"]).stat().st_mode)[-3:] == "644"


def test_local_copy_and_prune():
    src = HOME / "work" / "data.csv"
    src.write_text("a,b\n1,2\n")
    r = up.local_copy(src, HOME / "uploads" / "20260101-000000-abcd")
    assert r["name"] == "data.csv" and Path(r["path"]).read_text() == "a,b\n1,2\n"
    zsrc = HOME / "work" / "pack.zip"
    zsrc.write_bytes(make_zip({"x/y.txt": "y"}))
    r = up.local_copy(zsrc, HOME / "projects" / "p1", unpack=True)
    assert r["unpacked"] == 1 and (HOME / "projects" / "p1" / "x" / "y.txt").exists()
    with pytest.raises(ValueError):
        up.local_copy(HOME / "work" / "missing", HOME / "uploads" / "x")
    old = HOME / "uploads" / "20250101-000000-old"
    old.mkdir(parents=True)
    os.utime(old, (0, 0))
    keep = HOME / "uploads" / "20250101-000000-keep"
    keep.mkdir()
    os.utime(keep, (0, 0))
    assert up.prune(7, {"20250101-000000-keep"}) == 1
    assert not old.exists() and keep.exists() and (HOME / "uploads" / "20260101-000000-abcd").exists()
    assert up.prune(0) == 0


# ------------------------------------------------------------ gatewayd routing
def test_gateway_raw_body_and_token_guard():
    import gatewayd
    gatewayd.STATE["token"] = "secret"
    st, e = R(gatewayd.route("POST", "/api/upload/begin", {}, {"name": "a", "size": 1}, {"x-torus-token": "wrong"}))
    assert st == 401 and "Uploads" in e["error"]
    st, b = R(gatewayd.route("POST", "/api/upload/begin", {}, {"name": "a.txt", "size": 3}, {"x-torus-token": "secret"}))
    assert st == 200
    st, c = R(gatewayd.route("POST", f"/api/upload/{b['id']}/chunk", {"offset": ["0"]}, {"_raw": b"abc"}, {"x-torus-token": "secret"}))
    assert st == 200 and c["done"]
    src = (BIN / "gatewayd.py").read_text()
    assert 'headers.get("content-type", "").startswith("application/octet-stream")' in src and '{"_raw": raw}' in src


# ------------------------------------------------------------ operatord adopts the batch
class FakeNC:
    def __init__(self):
        self.out = []

    async def publish(self, subject, data):
        self.out.append(json.loads(data))


class Msg:
    reply = "r"


def test_task_adopts_batch_folder_and_prompt_lists_files(monkeypatch):
    z = make_zip({"d/a.txt": "a"})
    st, f, _ = upload(z, "bundle.zip")
    upload(b"col1,col2\n", "data.csv", batch=f["batch"])
    seen = {}

    async def fake_llm(t, system, messages, max_tokens=4096):
        seen["prompt"] = messages[0]["content"]
        return json.dumps({"phases": [{"title": "Look", "reads": ["data.csv"], "changes": [], "runs": []}], "estimate_usd": 0.1})
    monkeypatch.setattr(op, "llm", fake_llm)
    nc = FakeNC()

    async def flow():
        await op.on_create(nc, Msg(), {"task": "analyse the uploaded files", "uploads": f["batch"]})
        t = op.TASKS[nc.out[-1]["task"]["id"]]
        await op.RUN[t["id"]]
        return t
    t = R(flow())
    assert t["uploads"] == str(HOME / "uploads" / t["id"]) and not (HOME / "uploads" / f["batch"]).exists()
    assert [x["name"] for x in t["files"]] == ["bundle.zip", "data.csv"] and t["files"][0]["zip"]["summary"].startswith("bundle.zip: 1 file(s)")
    assert "Uploaded files (in " in seen["prompt"] and "data.csv (10 bytes)" in seen["prompt"] and "bundle.zip.list" in seen["prompt"]
    assert op.classify({"tool": "read", "path": str(HOME / "uploads" / t["id"] / "data.csv"), "_cwd": t["cwd"]}, set(), False)[0] == "read"
    op.write_dossier(t)
    assert "## Uploaded files" in Path(t["dossier"]).read_text()
    nc2 = FakeNC()
    R(op.on_create(nc2, Msg(), {"task": "another one", "uploads": "nonsense"}))
    assert not nc2.out[-1]["ok"] and "batch" in nc2.out[-1]["error"]
    R(op.on_create(nc2, Msg(), {"task": "no files", "uploads": "b-deadbeef"}))
    assert nc2.out[-1]["ok"] and nc2.out[-1]["task"]["uploads"] == ""


def test_load_prunes_old_uploads_but_keeps_active():
    old = HOME / "uploads" / "20250101-000000-aaaa"
    old.mkdir(parents=True)
    os.utime(old, (0, 0))
    act = HOME / "uploads" / "20250101-000000-bbbb"
    act.mkdir()
    os.utime(act, (0, 0))
    (HOME / "var" / "operator" / "20250101-000000-bbbb.json").write_text(json.dumps(
        {"id": "20250101-000000-bbbb", "task": "x", "status": "paused", "phases": [], "log": [], "created": 0}))
    op.load()
    assert not old.exists() and act.exists()


def test_wiring():
    assert "bin/gateway_upload.py" in (ROOT / "torus" / "manifest.txt").read_text()
    assert "uploads_keep_days" in (ROOT / "torus" / "files" / "etc" / "hardening.conf").read_text()
    torus = (ROOT / "torus" / "bin" / "torus").read_text()
    assert "upload) " in torus and "ctl upload" in torus
    ctl = (BIN / "torusctl.py").read_text()
    assert 'add_parser("upload")' in ctl and "--unpack" in ctl
    web = (ROOT / "torus" / "files" / "web" / "index.html").read_text()
    for tid in ("op-files-input", "op-dest-select", "op-project-input", "op-unpack-check", "op-upload-list", "op-upload-row", "op-files"):
        assert f'data-testid="{tid}"' in web, tid
    assert "application/octet-stream" in web and "/api/upload/begin" in web and "uploads: opUploads.batch" in web
