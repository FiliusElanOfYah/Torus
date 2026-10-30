"""gateway_upload - chunked file uploads for the Operator (phase 10), served by gatewayd behind the X-Torus-Token guard.

  POST /api/upload/begin   {name, size, batch?, project?, unpack?, sha256?}  -> {id, batch, offset, chunk}
  POST /api/upload/<id>/chunk?offset=N   body: raw bytes (Content-Type: application/octet-stream, <= 8 MB)
  POST /api/upload/<id>/finish {}        -> {name, path, size, zip?, unpacked?}
  POST /api/upload/<id>/abort  {}
  GET  /api/upload/<batch>               -> {dir, files:[{name, size, zip}]}

Where files land: ~/.torus/uploads/<batch>/ (operatord renames the folder to the task id when the task is created), or
~/projects/<project>/ when `project` is given (zips can be unpacked there; the zip itself is kept too).
Limits: 100 MB per file, 500 MB per batch, zips: 20 000 entries / 1 GB unpacked, no links, no path escapes.
Resumable: a reload asks GET /api/upload/<batch> and `begin` with the same name returns the current offset.
"""
import hashlib
import json
import os
import re
import secrets
import time
import zipfile
from pathlib import Path

from torus_common import ETC, HOME, atomic_write, read_conf

UPLOADS = HOME / "uploads"
MAX_FILE = 100 * 1024 * 1024
MAX_BATCH = 500 * 1024 * 1024
CHUNK = 8 * 1024 * 1024
ZIP_MAX_ENTRIES = 20_000
ZIP_MAX_BYTES = 1024 ** 3
NAME_RE = re.compile(r"^[^/\\\0]{1,200}$")
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
SESSIONS = {}  # id -> {name, size, part, final, batch, dir, offset, sha256, unpack, project, t}


def projects_root():
    c = read_conf(ETC / "coder.conf")
    return Path(os.path.expanduser(c.get("root") or str(Path.home() / "projects"))).resolve()


def err(code, msg):
    return code, {"error": msg}


def safe_name(name):
    name = os.path.basename(str(name or "")).strip()
    if not NAME_RE.match(name) or name in (".", "..") or name.startswith("."):
        raise ValueError("file name must be a plain name (no slashes, not hidden)")
    return name


def unique(path):
    if not path.exists():
        return path
    stem, suf = path.stem, path.suffix
    for i in range(2, 1000):
        p = path.with_name(f"{stem}-{i}{suf}")
        if not p.exists():
            return p
    raise ValueError("too many files with that name")


def batch_dir(batch):
    if not re.fullmatch(r"b-[0-9a-f]{8}", batch or ""):
        raise ValueError("bad batch id")
    return UPLOADS / batch


def batch_bytes(d):
    return sum(p.stat().st_size for p in d.iterdir() if p.is_file()) if d.exists() else 0


def zip_summary(path, limit=50):
    """-> {files, total, list:[...first entries], listed} or None when not a zip. Writes <name>.list next to it."""
    if not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        total = sum(i.file_size for i in infos)
        rows = [f"{i.file_size:>12}  {i.filename}" for i in infos[:2000]]
    listing = f"# {path.name}: {len(infos)} file(s), {total} bytes unpacked\n" + "\n".join(rows) + ("\n# ... more\n" if len(infos) > 2000 else "\n")
    atomic_write(path.with_name(path.name + ".list"), listing)
    return {"files": len(infos), "total": total, "list": [i.filename for i in infos[:limit]], "listed": str(path.with_name(path.name + ".list"))}


def unpack_zip(path, dest):
    """Extract into dest (zip-slip safe, size/entry caps, skips links). -> number of files."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    n = total = 0
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > ZIP_MAX_ENTRIES:
            raise ValueError(f"zip has {len(infos)} entries (limit {ZIP_MAX_ENTRIES})")
        if sum(i.file_size for i in infos) > ZIP_MAX_BYTES:
            raise ValueError("zip unpacks to more than 1 GB")
        for i in infos:
            name = i.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts:
                continue
            if (i.external_attr >> 16) & 0o170000 == 0o120000:  # symlink
                continue
            target = (dest / rel)
            if not str(target.resolve()).startswith(str(dest) + "/"):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > ZIP_MAX_BYTES:
                        raise ValueError("zip unpacks to more than 1 GB")
                    out.write(chunk)
            n += 1
    return n


def file_row(p):
    row = {"name": p.name, "size": p.stat().st_size}
    lst = p.with_name(p.name + ".list")
    if lst.exists():
        head = lst.read_text(encoding="utf-8", errors="replace").splitlines()
        row["zip"] = {"summary": head[0].lstrip("# ") if head else "", "list": [ln.strip().split(None, 1)[-1] for ln in head[1:51] if ln.strip() and not ln.startswith("#")]}
    return row


def listing(d):
    files = sorted(p for p in d.iterdir() if p.is_file() and not p.name.endswith(".list") and not p.name.endswith(".part")) if d.exists() else []
    return [file_row(p) for p in files]


def files_text(d, limit=40):
    """One line per uploaded file for Claude's prompt."""
    rows = listing(Path(d))
    out = []
    for r in rows[:limit]:
        line = f"- {r['name']} ({r['size']} bytes)"
        if r.get("zip"):
            line += f"  zip: {r['zip']['summary']}; listing in {r['name']}.list; first entries: " + ", ".join(r["zip"]["list"][:8])
        out.append(line)
    if len(rows) > limit:
        out.append(f"- ... {len(rows) - limit} more")
    return "\n".join(out)


def begin(body):
    try:
        name = safe_name(body.get("name"))
        size = int(body.get("size"))
    except (ValueError, TypeError):
        return err(400, "name and size are required")
    if size < 0 or size > MAX_FILE:
        return err(413, "files are limited to 100 MB each (zip or split bigger ones)")
    project = str(body.get("project") or "").strip()
    unpack = bool(body.get("unpack"))
    if project:
        if not PROJECT_RE.match(project):
            return err(400, "project name: letters, digits, . _ - only")
        d = projects_root() / project
        batch = ""
    else:
        batch = str(body.get("batch") or "")
        if not batch:
            batch = "b-" + secrets.token_hex(4)
        try:
            d = batch_dir(batch)
        except ValueError as e:
            return err(400, str(e))
        if batch_bytes(d) + size > MAX_BATCH:
            return err(413, "this upload would push the batch over 500 MB; start a new task for the rest")
    d.mkdir(parents=True, exist_ok=True)
    try:
        UPLOADS.chmod(0o700)
    except OSError:
        pass
    # resume: same name + size still uploading in this folder
    for sid, s in SESSIONS.items():
        if s["dir"] == str(d) and s["name"] == name and s["size"] == size and s["part"].exists():
            s["unpack"] = unpack or s["unpack"]
            return 200, {"id": sid, "batch": batch, "offset": s["offset"], "chunk": CHUNK, "resumed": True}
    sid = "u-" + secrets.token_hex(6)
    part = d / (name + ".part")
    part.write_bytes(b"")
    SESSIONS[sid] = {"name": name, "size": size, "part": part, "dir": str(d), "batch": batch, "offset": 0,
                     "sha256": str(body.get("sha256") or "").lower(), "unpack": unpack, "project": project, "t": time.time(), "h": hashlib.sha256()}
    return 200, {"id": sid, "batch": batch, "offset": 0, "chunk": CHUNK}


def chunk(sid, query, raw):
    s = SESSIONS.get(sid)
    if not s:
        return err(404, "unknown upload (start again)")
    try:
        offset = int((query.get("offset") or ["0"])[0])
    except ValueError:
        return err(400, "offset must be a number")
    if offset != s["offset"]:
        return 409, {"error": "offset mismatch; resume from the offset given", "offset": s["offset"]}
    if len(raw) > CHUNK:
        return err(413, "chunk over 8 MB")
    if s["offset"] + len(raw) > s["size"]:
        return err(400, "more bytes than announced")
    with open(s["part"], "ab") as f:
        f.write(raw)
    s["h"].update(raw)
    s["offset"] += len(raw)
    s["t"] = time.time()
    return 200, {"offset": s["offset"], "done": s["offset"] == s["size"]}


def finish(sid, body):
    s = SESSIONS.get(sid)
    if not s:
        return err(404, "unknown upload (start again)")
    if s["offset"] != s["size"]:
        return 409, {"error": f"only {s['offset']} of {s['size']} bytes arrived", "offset": s["offset"]}
    digest = s["h"].hexdigest()
    want = (body.get("sha256") or s["sha256"] or "").lower()
    if want and want != digest:
        s["part"].unlink(missing_ok=True)
        SESSIONS.pop(sid, None)
        return err(400, "checksum mismatch; the file was damaged in transit, try again")
    final = unique(Path(s["dir"]) / s["name"])
    os.replace(s["part"], final)
    os.chmod(final, 0o600 if not s["project"] else 0o644)
    SESSIONS.pop(sid, None)
    res = {"name": final.name, "path": str(final), "size": s["size"], "sha256": digest, "batch": s["batch"]}
    try:
        z = zip_summary(final)
    except (zipfile.BadZipFile, OSError) as e:
        z = None
        res["warning"] = f"could not read the zip listing: {e}"
    if z:
        res["zip"] = z
        if s["unpack"]:
            try:
                dest = Path(s["dir"]) if s["project"] else Path(s["dir"]) / final.stem
                res["unpacked"] = unpack_zip(final, dest)
                res["unpacked_to"] = str(dest)
            except (ValueError, zipfile.BadZipFile, OSError) as e:
                res["warning"] = f"zip kept but not unpacked: {e}"
    return 200, res


def abort(sid):
    s = SESSIONS.pop(sid, None)
    if s:
        Path(s["part"]).unlink(missing_ok=True)
    return 200, {"ok": True}


def get_batch(batch):
    try:
        d = batch_dir(batch)
    except ValueError:
        return err(400, "bad batch id")
    if not d.exists():
        return 200, {"dir": str(d), "files": [], "bytes": 0}
    return 200, {"dir": str(d), "files": listing(d), "bytes": batch_bytes(d),
                 "partial": [{"name": s["name"], "offset": s["offset"], "size": s["size"], "id": sid} for sid, s in SESSIONS.items() if s["dir"] == str(d)]}


def expire(max_age=6 * 3600):
    """Drop upload sessions idle for 6 h and their .part files."""
    for sid in [k for k, s in SESSIONS.items() if time.time() - s["t"] > max_age]:
        abort(sid)


async def route(method, path, query, body):
    expire()
    m = re.fullmatch(r"/api/upload/(begin|b-[0-9a-f]{8}|u-[0-9a-f]{12})(?:/(chunk|finish|abort))?", path)
    if not m:
        return err(404, "not found")
    head, action = m.group(1), m.group(2)
    if head == "begin":
        return begin(body) if method == "POST" else err(405, "POST")
    if head.startswith("b-"):
        return get_batch(head) if method == "GET" and not action else err(404, "not found")
    if method != "POST":
        return err(405, "POST")
    if action == "chunk":
        return chunk(head, query, body.get("_raw") if isinstance(body.get("_raw"), (bytes, bytearray)) else b"")
    if action == "finish":
        return finish(head, body)
    if action == "abort":
        return abort(head)
    return err(404, "not found")


def local_copy(src, dest_dir, unpack=False):
    """CLI path (torus upload): copy a local file into dest_dir with the same listing/unpack rules. -> result dict."""
    src = Path(src).expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"not a file: {src}")
    if src.stat().st_size > MAX_FILE:
        raise ValueError("files are limited to 100 MB each")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = unique(dest_dir / safe_name(src.name))
    import shutil
    shutil.copy2(src, final)
    res = {"name": final.name, "path": str(final), "size": final.stat().st_size}
    z = zip_summary(final)
    if z:
        res["zip"] = z
        if unpack:
            dest = dest_dir if str(dest_dir).startswith(str(projects_root())) else dest_dir / final.stem
            res["unpacked"] = unpack_zip(final, dest)
            res["unpacked_to"] = str(dest)
    return res


def prune(days=7, keep_ids=()):
    """Delete ~/.torus/uploads/<id>/ folders untouched for `days` (0 = never), except keep_ids. -> count."""
    if not days or not UPLOADS.exists():
        return 0
    import shutil
    cutoff = time.time() - days * 86400
    n = 0
    for d in UPLOADS.iterdir():
        if d.is_dir() and d.name not in keep_ids and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


if __name__ == "__main__":
    print(json.dumps({"uploads": str(UPLOADS), "projects": str(projects_root()), "files": [str(p) for p in UPLOADS.glob("*/*")] if UPLOADS.exists() else []}, indent=1))
