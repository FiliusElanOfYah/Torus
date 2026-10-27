"""Shared helpers for every TORUS daemon.

Installed to ~/.torus/bin/torus_common.py by `torus install 0`.
Do not edit the installed copy. Edit ~/.torus/src/torus/files/bin/torus_common.py
and run `torus install all`.
"""
import asyncio
import html
import json
import math
import os
import re
import traceback
from html.parser import HTMLParser
from pathlib import Path

HOME = Path(os.environ.get("TORUS_HOME", str(Path.home() / ".torus")))
ETC = HOME / "etc"
LOGS = HOME / "logs"
VAR = HOME / "var"
VAULT = Path(os.environ.get("TORUS_VAULT", str(HOME / "vault")))
AIRGAP = HOME / "airgap"
AIRGAP_SOCK = AIRGAP / "airgap.sock"
INDEX_DB = HOME / "index.sqlite"
NATS_URL = os.environ.get("TORUS_NATS_URL", "nats://127.0.0.1:4222")
LLM_TOGGLE = ETC / "llm.toggle"
SEARCH_CONF = ETC / "search.conf"
# Honest UA with a contact URL (Wikimedia and others reject anonymous bot UAs)
USER_AGENT = "Mozilla/5.0 (Linux; Android 16) TORUS/1.0 (+https://github.com/FiliusElanOfYah/Torus; personal research assistant)"
ROBOTS_AGENT = "TORUS"

# Default Anthropic model ids. Override without code changes:
#   torus llm id sonnet <model-id>   (writes sonnet_id=... into llm.toggle)
MODEL_ALIASES = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "haiku": "claude-haiku-5-5"}


def log(name, *parts):
    print(f"[{name}]", *parts, flush=True)


def pack(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- config files
def read_conf(path):
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def write_conf(path, updates):
    path = Path(path)
    cur = read_conf(path)
    cur.update({k: str(v) for k, v in updates.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(f"{k}={v}\n" for k, v in cur.items()), encoding="utf-8")
    os.replace(tmp, path)


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def resolve_model(name, conf=None):
    conf = conf if conf is not None else read_conf(LLM_TOGGLE)
    name = (name or "sonnet").strip()
    return conf.get(f"{name}_id") or MODEL_ALIASES.get(name, name)


def llm_settings():
    c = read_conf(LLM_TOGGLE)
    model = c.get("model") or "sonnet"
    return {
        "enabled": truthy(c.get("enabled", "false")),
        "model": model,
        "model_id": resolve_model(model, c),
        "max_tokens": int(c.get("max_tokens") or 4096),
        # auto_route=true: short follow-ups without new URLs go to Haiku (cheap); full answers keep the default tier
        "auto_route": truthy(c.get("auto_route", "false")),
        # Cloudflare AI Gateway (optional): torus llm gateway <account_id> <gateway_name>. Messages go via
        # gateway.ai.cloudflare.com (logs, cache, caps); the Anthropic key still pays. Model list stays direct.
        "cf_account": (c.get("cf_account") or "").strip(),
        "cf_gateway": (c.get("cf_gateway") or "").strip(),
        "api_base": cf_api_base(c.get("cf_account"), c.get("cf_gateway")),
    }


def cf_api_base(account, gateway):
    account, gateway = (account or "").strip(), (gateway or "").strip()
    if account and gateway:
        return f"https://gateway.ai.cloudflare.com/v1/{account}/{gateway}/anthropic/v1"
    return "https://api.anthropic.com/v1"


def search_settings():
    c = read_conf(SEARCH_CONF)
    mode = c.get("mode") or "urls"
    if mode not in ("urls", "brave", "both"):
        mode = "urls"
    return {"mode": mode, "count": max(1, min(10, int(c.get("count") or 5)))}


def key_present(name):
    return (AIRGAP / f"{name}.key").is_file()


async def get_secret(name):
    """Ask airgapd for a key over its 0600 unix socket."""
    try:
        r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(AIRGAP_SOCK)), 5)
    except (FileNotFoundError, ConnectionRefusedError, asyncio.TimeoutError) as e:
        raise RuntimeError(f"airgapd is not answering ({e.__class__.__name__}): torus status") from None
    try:
        w.write((name + "\n").encode())
        await w.drain()
        data = await asyncio.wait_for(r.read(), 5)
    finally:
        w.close()
    val = data.decode("utf-8", "ignore").strip()
    if not val or val.startswith("ERR"):
        raise RuntimeError(f"no '{name}' key stored: run  torus key set {name}")
    return val


# ---------------------------------------------------------------- NATS daemon
async def connect_nats(name):
    import nats

    delay = 1
    while True:
        try:
            return await nats.connect(
                servers=[NATS_URL], name=name, connect_timeout=5,
                max_reconnect_attempts=-1, reconnect_time_wait=2,
            )
        except Exception as e:  # nats not up yet (boot order) - keep trying
            log(name, f"NATS not ready ({e.__class__.__name__}); retry in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)


def run_daemon(name, handlers, concurrent=False, on_start=None):
    """Subscribe handlers {subject: async fn(nc, msg, data)} and run forever.
    A failing message is logged and skipped - it never kills the daemon."""

    async def main():
        nc = await connect_nats(name)
        tasks = set()

        def wrap(fn):
            async def safe(msg):
                try:
                    data = json.loads(msg.data) if msg.data else {}
                except ValueError:
                    log(name, "bad JSON on", msg.subject)
                    return
                try:
                    await fn(nc, msg, data)
                except Exception:
                    log(name, "handler error on", msg.subject, "\n" + traceback.format_exc())

            if not concurrent:
                return safe

            async def spawn(msg):
                t = asyncio.create_task(safe(msg))
                tasks.add(t)
                t.add_done_callback(tasks.discard)

            return spawn

        if on_start:
            await on_start(nc)
        for subject, fn in handlers.items():
            await nc.subscribe(subject, cb=wrap(fn))
        log(name, "up, listening on", ", ".join(handlers))
        await asyncio.Event().wait()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------- text helpers
def slugify(s, n=60):
    s = re.sub(r"^https?://", "", s or "")
    return (re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")[:n] or "page").lower()


class _TextParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def html_to_text(body):
    p = _TextParser()
    try:
        p.feed(body)
    except Exception:
        pass
    text = "".join(p.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return p.title.strip(), text.strip()


def page_title(body):
    m = re.search(r"<title[^>]*>(.*?)</title>", body or "", re.S | re.I)
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def extract(body, url="", content_type=""):
    """Return (title, readable_text). Uses trafilatura when installed,
    falls back to a pure-Python HTML stripper."""
    body = body or ""
    looks_html = "html" in (content_type or "").lower() or body.lstrip()[:200].lower().startswith(("<!doctype", "<html", "<"))
    if not looks_html:
        return "", body.strip()
    title = page_title(body)
    try:
        import trafilatura

        md = trafilatura.extract(body, url=url or None, output_format="markdown",
                                 include_links=False, include_tables=True, favor_recall=True)
        if md and len(md.strip()) > 80:
            return title, md.strip()
    except Exception:
        pass
    t2, text = html_to_text(body)
    return title or t2, text


# ---------------------------------------------------------------- TextRank (pure Python)
STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that
these those he she they we you i not no do does did have has had will would can could should may might must than then
there their them his her our your into over under about after before between through during also such which who whom
whose what when where why how all any each more most other some only own same so too very just one two new used use
using see may however while both many much well even like""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]+")
_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)")


def _mathy(s):
    if re.search(r"\\[a-zA-Z]{2,}|\{\\displaystyle|\$\$?|\^\{|_\{|\\\(|\\\[", s):
        return True
    sym = sum(1 for ch in s if ch in "=+*/^_{}[]<>|∑∫√≤≥≈±×÷∂∞πθλΣΔ")
    return sym / max(len(s), 1) > 0.08


def split_sentences(text):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text or "")       # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)           # links -> label
    text = re.sub(r"(?m)^\s*(#+|[-*>|]+)\s*", "", text)            # md markers
    text = re.sub(r"<[^>\n]{1,40}>", "", text)                      # stray html tags (<sup> etc.)
    text = re.sub(r"\*\*?|__", "", text)                            # bold / italic marks
    out = []
    for block in re.split(r"\n\s*\n", text):
        block = " ".join(block.split())
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", block):
            s = s.strip()
            if 40 <= len(s) <= 500 and s[-1] in ".!?\"')" and not _mathy(s):
                out.append(s)
    return out


def _tokens(s):
    return {w.lower() for w in _WORD.findall(s) if w.lower() not in STOP and len(w) > 2}


def rank_sentences(sents, k=5, query=""):
    """Return indices of the k best sentences (in original order)."""
    n = len(sents)
    if n <= k:
        return list(range(n))
    toks = [_tokens(s) for s in sents]
    w = [[0.0] * n for _ in range(n)]
    for i in range(n):
        if len(toks[i]) < 2:
            continue
        for j in range(i + 1, n):
            if len(toks[j]) < 2:
                continue
            ov = len(toks[i] & toks[j])
            if ov:
                v = ov / (math.log(len(toks[i])) + math.log(len(toks[j])))
                w[i][j] = w[j][i] = v
    out_sum = [sum(r) or 1.0 for r in w]
    score = [1.0] * n
    for _ in range(30):
        score = [0.15 + 0.85 * sum(w[j][i] / out_sum[j] * score[j] for j in range(n) if w[j][i]) for i in range(n)]
    # proper-noun boost: names that recur across the text matter
    freq = {}
    for s in sents:
        for m in _PROPER.findall(s):
            freq[m] = freq.get(m, 0) + 1
    common = {p for p, c in freq.items() if c >= 2}
    q = _tokens(query)
    for i, s in enumerate(sents):
        hits = sum(1 for p in common if p in s)
        score[i] *= 1 + 0.15 * min(3, hits)
        if q:
            score[i] *= 1 + 1.5 * len(q & toks[i]) / len(q)
    best = sorted(range(n), key=lambda i: score[i], reverse=True)[:k]
    return sorted(best)


def textrank(text, k=5, query=""):
    sents = split_sentences(text)
    return [sents[i] for i in rank_sentences(sents, k, query)]


# ---------------------------------------------------------------- rotating JSONL logs
def append_jsonl(path, obj, max_bytes=1_000_000, keep=3):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > max_bytes:
        oldest = path.with_name(f"{path.name}.{keep}")
        if oldest.exists():
            oldest.unlink()
        for i in range(keep - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
        os.replace(path, path.with_name(path.name + ".1"))
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
