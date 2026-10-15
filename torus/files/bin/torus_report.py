"""TORUS PDF reports, written with the standard library only (no reportlab/fpdf
to build on Termux). Uses the PDF built-in fonts (Helvetica/Courier), so
characters outside Western European text become '?'.
Installed to ~/.torus/bin/torus_report.py (phase 6)."""
import re
import time
import unicodedata
import zlib

PAGE_W, PAGE_H, MARGIN = 595.0, 842.0, 56.0
TOP, BOTTOM = PAGE_H - MARGIN, MARGIN + 24
TEXT_W = PAGE_W - 2 * MARGIN
FONTS = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Courier", "F4": "Helvetica-Oblique"}
INK, DIM, ACCENT, LINK = (0.08, 0.09, 0.09), (0.42, 0.45, 0.44), (0.62, 0.38, 0.0), (0.05, 0.33, 0.62)

_CH = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
        [278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722,
         778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500,
         556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500,
         500, 500, 334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + \
        [333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722,
         778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556,
         611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556,
         556, 500, 389, 280, 389, 584]
WIDTHS = {"F1": dict(zip(_CH, _HELV)), "F4": dict(zip(_CH, _HELV)), "F2": dict(zip(_CH, _BOLD))}


def to_pdf_text(s):
    s = (s or "").replace("\u00a0", " ").replace("\u2212", "-").replace("\t", "    ")
    out = []
    for ch in s:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(base or "?")
    return "".join(out)


def esc(s):
    return "".join(chr(c) if 32 <= c < 127 and c not in (40, 41, 92) else "\\%03o" % c
                   for c in to_pdf_text(s).encode("cp1252", "replace"))


def width(text, font, size):
    if font == "F3":
        return 0.6 * size * len(text)
    table = WIDTHS[font]
    return sum(table.get(c, 556) for c in text) * size / 1000.0


class Doc:
    def __init__(self, footer):
        self.pages, self.annots, self.cur, self.y, self.footer = [], [], None, TOP, footer

    def page(self):
        self.cur = []
        self.pages.append(self.cur)
        self.annots.append([])
        self.y = TOP

    def need(self, h):
        if self.cur is None or self.y - h < BOTTOM:
            self.page()

    def text(self, x, y, font, size, s, color=INK):
        r, g, b = color
        self.cur.append(f"BT /{font} {size:.1f} Tf {r} {g} {b} rg {x:.2f} {y:.2f} Td ({esc(s)}) Tj ET")

    def rule(self, color=(0.85, 0.85, 0.85)):
        r, g, b = color
        self.cur.append(f"{r} {g} {b} RG 0.6 w {MARGIN:.1f} {self.y:.1f} m {PAGE_W - MARGIN:.1f} {self.y:.1f} l S")

    def link(self, x, y, w, h, url):
        self.annots[-1].append((x, y, x + w, y + h, url))

    # runs: list of (text, font, color, url|None)
    def para(self, runs, size=10.5, leading=15.0, indent=0.0, bullet=None):
        lines = wrap(runs, TEXT_W - indent, size)
        for i, line in enumerate(lines):
            self.need(leading)
            self.y -= leading
            x = MARGIN + indent
            if bullet and i == 0:
                self.text(MARGIN + indent - 12, self.y, "F1", size, bullet, DIM)
            for txt, font, color, url in line:
                self.text(x, self.y, font, size, txt, color)
                w = width(txt, font, size)
                if url:
                    self.link(x, self.y - 2, w, size + 2, url)
                x += w
        self.y -= leading * 0.35

    def code(self, text, size=8.6, leading=11.5):
        for raw in text.rstrip("\n").split("\n") or [""]:
            chunks = [raw[i:i + 92] for i in range(0, max(len(raw), 1), 92)] or [""]
            for c in chunks:
                self.need(leading)
                self.cur.append(f"0.95 0.95 0.94 rg {MARGIN - 4:.1f} {self.y - leading - 2:.1f} {TEXT_W + 8:.1f} {leading:.1f} re f")
                self.y -= leading
                self.text(MARGIN, self.y, "F3", size, c)
        self.y -= 6

    def heading(self, s, size=13.0, color=ACCENT):
        self.need(size * 2.4)
        self.y -= size * 0.6
        self.para([(s, "F2", color, None)], size=size, leading=size * 1.3)

    def build(self, title):
        total = len(self.pages)
        for i, ops in enumerate(self.pages, 1):
            self.cur = ops
            ft = f"{self.footer}   |   page {i} of {total}"
            self.text(MARGIN, MARGIN, "F1", 8, ft, DIM)
        objs = []

        def add(b):
            objs.append(b)
            return len(objs)

        cat, pages = add(b""), add(b"")
        font_ids = {k: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{v} /Encoding /WinAnsiEncoding >>".encode())
                    for k, v in FONTS.items()}
        font_res = " ".join(f"/{k} {v} 0 R" for k, v in font_ids.items())
        kids = []
        for ops, annots in zip(self.pages, self.annots):
            data = zlib.compress("\n".join(ops).encode("ascii"))
            c = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
            aids = [add((f"<< /Type /Annot /Subtype /Link /Rect [{x0:.1f} {y0:.1f} {x1:.1f} {y1:.1f}] /Border [0 0 0] "
                         f"/A << /S /URI /URI ({esc(url)}) >> >>").encode()) for x0, y0, x1, y1, url in annots]
            annot_ref = f"/Annots [{' '.join(f'{a} 0 R' for a in aids)}]" if aids else ""
            kids.append(add((f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
                             f"/Resources << /Font << {font_res} >> >> /Contents {c} 0 R {annot_ref} >>").encode()))
        objs[pages - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
        objs[cat - 1] = f"<< /Type /Catalog /Pages {pages} 0 R >>".encode()
        info = add(f"<< /Title ({esc(title)}) /Producer (TORUS) /CreationDate (D:{time.strftime('%Y%m%d%H%M%S')}) >>".encode())
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


def wrap(runs, maxw, size):
    lines, line, lw = [], [], 0.0
    for text, font, color, url in runs:
        for tok in re.findall(r"\S+\s*|\s+", text):
            w = width(tok.rstrip(), font, size)
            if line and lw + w > maxw:
                lines.append(line)
                line, lw = [], 0.0
                tok = tok.lstrip()
            while width(tok.rstrip(), font, size) > maxw:  # one huge word (a URL): hard split
                cut = max(1, int(len(tok) * maxw / max(width(tok, font, size), 1)) - 1)
                lines.append([(tok[:cut], font, color, url)])
                tok = tok[cut:]
            line.append((tok, font, color, url))
            lw += width(tok, font, size)
    if line:
        lines.append(line)
    return lines or [[("", "F1", INK, None)]]


INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:[^)\s]+\)|\[\d{1,2}\])")


def inline_runs(s, font="F1", color=INK):
    runs = []
    for part in INLINE.split(s):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            runs.append((part[2:-2], "F2", color, None))
        elif part.startswith("`") and part.endswith("`"):
            runs.append((part[1:-1], "F3", color, None))
        elif re.fullmatch(r"\[\d{1,2}\]", part):
            runs.append((part, "F2", ACCENT, None))
        elif part.startswith("[") and "](" in part:
            label, url = part[1:].split("](", 1)
            runs.append((label, font, LINK, url[:-1]))
        else:
            runs.append((part, font, color, None))
    return runs


def markdown(doc, src):
    blocks = re.split(r"(```[^\n]*\n.*?```)", src or "", flags=re.S)
    for blk in blocks:
        if blk.startswith("```"):
            doc.code(re.sub(r"^```[^\n]*\n|```$", "", blk))
            continue
        for para in re.split(r"\n\s*\n", blk):
            para = para.strip("\n")
            if not para.strip():
                continue
            h = re.match(r"^(#{1,4})\s+(.*)", para)
            if h and "\n" not in para:
                doc.heading(h.group(2).strip("# "), size={1: 14.0, 2: 12.5}.get(len(h.group(1)), 11.5))
                continue
            lines = para.split("\n")
            if all(re.match(r"^\s*([-*+]|\d+[.)])\s+", ln) for ln in lines):
                for ln in lines:
                    m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)", ln)
                    depth = min(len(m.group(1)) // 2, 3)
                    mark = "\u2022" if m.group(2) in "-*+" else m.group(2)
                    doc.para(inline_runs(m.group(3)), indent=16 + 14 * depth, bullet=mark)
                continue
            doc.para(inline_runs(" ".join(ln.strip() for ln in lines)))


def build_pdf(j, cmap=None):
    created = time.strftime("%Y-%m-%d %H:%M", time.localtime(j.get("created") or time.time()))
    doc = Doc(f"TORUS report  {j['id']}")
    doc.page()
    doc.para([(j["question"], "F2", INK, None)], size=18, leading=23)
    doc.para([(f"TORUS research report  |  {created}  |  answered by {j.get('engine') or 'n/a'}", "F1", DIM, None)], size=9, leading=12)
    doc.y -= 4
    doc.rule()
    doc.heading("Answer")
    markdown(doc, j.get("answer") or "_No answer yet._")
    for t in j.get("followups", []):
        doc.heading("Follow-up: " + t["question"])
        markdown(doc, t.get("answer") or "_No answer yet._")
        if t.get("engine"):
            doc.para([(f"answered by {t['engine']}", "F4", DIM, None)], size=8.5, leading=11)
    if cmap and any(r["score"] for r in cmap["ranking"]):
        doc.heading("Strongest references")
        for r in [r for r in cmap["ranking"] if r["score"]][:6]:
            why = []
            if r["linked_by"]:
                why.append(f"linked by {r['linked_by']} other source(s)")
            if r["site_linked_by"]:
                why.append(f"its site is linked by {r['site_linked_by']}")
            if r["answer_cites"]:
                why.append(f"cited {r['answer_cites']}x in the answer")
            doc.para([(f"[{r['n']}] ", "F2", ACCENT, None), (r["title"], "F1", INK, r["url"]),
                      ("  " + ", ".join(why), "F4", DIM, None)], indent=16, bullet="\u2022")
    doc.heading("Sources")
    for s in j.get("sources", []):
        doc.para([(f"[{s['n']}] ", "F2", ACCENT, None), ((s.get("title") or s["url"]) + "  ", "F1", INK, None),
                  (s["url"], "F1", LINK, s["url"]), (f"  ({s['status']}, {s['origin']})", "F4", DIM, None)], size=9.5, leading=13)
    if cmap and cmap.get("shared"):
        doc.heading("Pages several sources point to")
        for x in cmap["shared"][:12]:
            doc.para([(x["url"], "F1", LINK, x["url"]), ("  linked by " + ", ".join(f"[{n}]" for n in x["by"]), "F4", DIM, None)],
                     size=9.5, leading=13, indent=16, bullet="\u2022")
    return doc.build(j["question"])
