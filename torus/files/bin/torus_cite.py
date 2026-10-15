"""Citation map for a TORUS job: which sources link to which, how often the
answer cites each one, and outside pages that several sources point to.
Installed to ~/.torus/bin/torus_cite.py (phase 6)."""
import html
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

HREF = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"'#][^"']*)["']""", re.I)
CITE = re.compile(r"\[(\d{1,2})\]")
SECOND_LEVEL = {"co", "com", "org", "ac", "gov", "net", "edu"}
# share buttons, trackers and licence boilerplate that every page links to
JUNK = {"facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com", "pinterest.com", "tiktok.com",
        "whatsapp.com", "google.com", "goo.gl", "creativecommons.org", "wikimediafoundation.org", "mediawiki.org",
        "wikidata.org", "wikimedia.org", "wikipedia.org", "wikimedia.de", "doubleclick.net", "addtoany.com", "sharethis.com", "t.me", "reddit.com", "apple.com"}
MAX_LINKS = 1500


def norm(url):
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.netloc:
        return ""
    host = p.netloc.lower().split("@")[-1]
    host = host[4:] if host.startswith("www.") else host
    host = re.sub(r"^(\w+)\.m\.", r"\1.", host)  # en.m.wikipedia.org -> en.wikipedia.org
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))])
    return urlunparse(("https", host, p.path.rstrip("/"), "", q, ""))


def site(url):
    host = urlparse(url).netloc
    parts = host.split(".")
    if len(parts) > 2 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def extract_links(body, base):
    out, seen = [], set()
    for m in HREF.finditer(body or ""):
        u = norm(urljoin(base, html.unescape(m.group(1))))
        if u and u not in seen:
            seen.add(u)
            out.append(u)
            if len(out) >= MAX_LINKS:
                break
    return out


def answer_cites(j):
    counts = {}
    for text in [j.get("answer") or ""] + [t.get("answer") or "" for t in j.get("followups", [])]:
        for m in CITE.findall(text):
            counts[int(m)] = counts.get(int(m), 0) + 1
    return counts


def citation_map(j):
    srcs = j.get("sources", [])
    info = []
    for s in srcs:
        nu = norm(s["url"])
        links = set(s.get("links") or [])
        info.append({"n": s["n"], "norm": nu, "site": site(nu) if nu else "", "links": links,
                     "link_sites": {site(x) for x in links}})
    edges = []
    for a in info:
        for b in info:
            if a is b or not b["norm"]:
                continue
            if b["norm"] in a["links"]:
                edges.append({"from": a["n"], "to": b["n"], "kind": "page"})
            elif b["site"] != a["site"] and b["site"] in a["link_sites"]:
                edges.append({"from": a["n"], "to": b["n"], "kind": "site"})
    cites = answer_cites(j)
    ranking = []
    for s, i in zip(srcs, info):
        page_in = sum(1 for e in edges if e["to"] == i["n"] and e["kind"] == "page")
        site_in = sum(1 for e in edges if e["to"] == i["n"] and e["kind"] == "site")
        c = cites.get(i["n"], 0)
        ranking.append({"n": i["n"], "title": s.get("title") or s["url"], "url": s["url"], "status": s["status"],
                        "linked_by": page_in, "site_linked_by": site_in, "answer_cites": c,
                        "links_out": sum(1 for e in edges if e["from"] == i["n"]), "has_links": bool(i["links"]),
                        "score": 3 * page_in + site_in + c})
    ranking.sort(key=lambda r: (-r["score"], r["n"]))
    src_norms = {i["norm"] for i in info}
    shared = {}
    for i in info:
        for link in i["links"]:
            ls = site(link)
            if link in src_norms or ls == i["site"] or ls in JUNK:
                continue
            shared.setdefault(link, set()).add(i["n"])
    shared_list = sorted(({"url": u, "site": site(u), "by": sorted(ns)} for u, ns in shared.items() if len(ns) >= 2),
                         key=lambda x: (-len(x["by"]), x["url"]))[:20]
    return {"ranking": ranking, "edges": edges, "shared": shared_list,
            "sources_with_links": sum(1 for i in info if i["links"])}
