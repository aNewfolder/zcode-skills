#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""annotate_papers.py — 给 PDF 生成「解析卡」：另存 __note.pdf + 同名 .md。

安全原则：
  * **原件一个字节都不改**。默认输出 <stem>__note.pdf（解析卡插在第 1 页之前）。
  * 用 --in-place 才原地插入（需显式加 --backup，会在同目录写 <stem>__backup.pdf）。
  * 解析卡内容全部来自本地提取，不调用任何模型；元数据可与 Crossref 交叉核对。

用法:
  python -X utf8 annotate_papers.py --files "a.pdf" "b.pdf" [--in-place --backup]
  python -X utf8 annotate_papers.py --list files.txt          # 每行一个路径
  python -X utf8 annotate_papers.py --propose 解析\\propose.tsv --accept-only   # 只处理 accept=y

生成的 md 长这样：
  ---
  title: ...
  doi: ...
  ---
  正文...
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",    # 微软雅黑
    r"C:\Windows\Fonts\simsun.ttc",  # 宋体
    r"C:\Windows\Fonts\simhei.ttf",  # 黑体
]

NOISE = re.compile(
    r"^(article|research|review|letter|open access|check for updates|https?:|doi|received|accepted|"
    r"published|abstract|www\.|©|copyright|downloaded from|see discussions|content downloaded)",
    re.I)


def find_cjk_font():
    """跨平台：优先用同目录的 cjk_font.py；失败则退回内置 china-s（不报错）。"""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from cjk_font import find_cjk_font as _f
        return _f()
    except Exception:
        for p in CJK_FONT_CANDIDATES:
            if os.path.exists(p):
                return p
        return None


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def guess_title(text):
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    cands = [l for l in lines if 18 < len(l) < 200 and not NOISE.match(l)
             and not re.fullmatch(r"[\d\s.,;:()\-–—]+", l)]
    return norm(" ".join(cands[:2] or lines[:2]))[:240]


def guess_authors(text):
    """首页里最像作者行的一行（含逗号/上标数字，且不是机构行）。"""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()][:14]
    for l in lines:
        if NOISE.match(l):
            continue
        if re.search(r"univers|institut|depart|laborator|academy|college|school|大学|学院|研究所|实验室", l, re.I):
            continue
        if 8 < len(l) < 160 and (l.count(",") >= 1 or re.search(r"\d", l)):
            return norm(l)[:200]
    return ""


def guess_doi(text, path):
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text or "")
    if m:
        return m.group(0).rstrip(".,;)")
    m = re.search(r"(10\.\d{4,9}[-._;()/:A-Za-z0-9]+)", os.path.basename(path))
    return m.group(1) if m else ""


def guess_abstract(text):
    """从首页文本里截摘要段。"""
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text)
    m = re.search(r"(?:ABSTRACT|Abstract|摘要)[\s:：]*(.{80,1800}?)(?:Index Terms|Keywords|关键词|1\.\s*INTRODUCTION|I\.\s*INTRODUCTION|Introduction)", t)
    if m:
        return norm(m.group(1))[:1200]
    m = re.search(r"(?:ABSTRACT|Abstract|摘要)[\s:：]*(.{80,1200})", t)
    return norm(m.group(1))[:1200] if m else ""


def crossref(doi, timeout=8):
    """按 DOI 取权威元数据；失败返回 None（不抛错，不阻塞流程）。"""
    if not doi:
        return None
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "zju-lit-downloader/1.0 (mailto:none@example.com)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None
    m = j.get("message", {})
    authors = []
    for a in (m.get("author") or [])[:12]:
        fam, giv = a.get("family", ""), a.get("given", "")
        authors.append((fam + " " + giv).strip() or a.get("name", ""))
    return {
        "title": (m.get("title") or [""])[0],
        "authors": authors,
        "year": (m.get("issued", {}).get("date-parts", [[None]])[0] or [None])[0],
        "venue": (m.get("container-title") or [""])[0],
        "volume": m.get("volume", ""), "issue": m.get("issue", ""), "page": m.get("page", ""),
        "type": m.get("type", ""),
        "abstract": re.sub(r"<[^>]+>", " ", m.get("abstract", "") or "").strip(),
    }


def extract_first_pages(path, n_pages=1):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        texts = [(pdf.pages[i].extract_text() or "") for i in range(min(n_pages, total))]
    return total, "\n".join(texts)


def build_card(info, source_note=""):
    """生成解析卡要写的文本行（(文本, 是否粗体/字号) 由调用方排版）。"""
    rows = [
        ("__CARD_HEADER__", "文献解析卡"),
        ("题目", info.get("title") or "（未能提取）"),
        ("作者", "；".join(info.get("authors") or []) or info.get("authors_line") or "（未能提取，建议人工补）"),
        ("单位", info.get("affiliation") or "（需人工确认，首页机构行不易解析）"),
        ("年份", str(info.get("year") or "（未提取）")),
        ("期刊", info.get("venue") or "（未提取）"),
        ("卷期页", " ".join(x for x in [info.get("volume"), info.get("issue"), info.get("page")] if x) or "—"),
        ("DOI", info.get("doi") or "（未提取）"),
        ("来源文件", os.path.basename(info.get("path", ""))),
        ("解析时间", time.strftime("%Y-%m-%d %H:%M")),
        ("字段来源", info.get("sources") or "pdf"),
    ]
    if source_note:
        rows.append(("备注", source_note))
    rows.append(("摘要", info.get("abstract") or "（未能自动提取，建议人工补）"))
    return rows


def make_card_pdf(rows, width=595, margin=42):
    """把解析卡渲染成一页 A4 PDF（bytes）。PyMuPDF 支持 CJK 内置字体，无需嵌字体文件。"""
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page(width=width, height=842)  # A4
    y = margin
    left = margin
    right = width - margin
    for label, value in rows:
        if y > 790:
            page = doc.new_page(width=width, height=842)
            y = margin
        if label == "__CARD_HEADER__":
            page.insert_textbox(fitz.Rect(left, y, right, y + 30), value,
                                fontname="china-s", fontsize=17, color=(0, 0, 0))
            y += 26
            page.draw_line(fitz.Point(left, y), fitz.Point(right, y), color=(0.6, 0.6, 0.6), width=0.8)
            y += 12
            continue
        head = f"{label}："
        page.insert_textbox(fitz.Rect(left, y, left + 62, y + 16), head,
                            fontname="china-s", fontsize=10, color=(0.25, 0.25, 0.25))
        body = value if isinstance(value, str) else str(value)
        size = 9 if label == "摘要" else 10
        box_w = right - (left + 66)
        # 先用足够大的高度试排，未截断时 rc>=0；把最后一行基线作为真实高度，
        # 这样长摘要不会被框裁掉，也不会留下大片空白。
        probe = fitz.Rect(left + 66, y, right, y + 4000)
        rc = page.insert_textbox(probe, body, fontname="china-s", fontsize=size, color=(0, 0, 0), align=0)
        if rc >= 0:
            h = 4000 - rc
        else:
            # 还是放不下：截断到框内并标注
            body = body[:1500] + " …（过长已截断，详见同名 __note.md）"
            page.insert_textbox(probe, body, fontname="china-s", fontsize=size, color=(0, 0, 0), align=0)
            h = 4000
        y += max(h + 4, 18)
        if y > 800:
            page = doc.new_page(width=width, height=842)
            y = margin
    out = doc.tobytes()
    doc.close()
    return out


def annotate_one(path, out_pdf, out_md, in_place=False, backup=False, use_crossref=True):
    if not os.path.exists(path):
        return {"path": path, "status": "missing"}
    total, first_text = extract_first_pages(path, n_pages=1)
    info = {"path": path, "pages": total}
    info["title"] = guess_title(first_text)
    info["authors_line"] = guess_authors(first_text)
    info["doi"] = guess_doi(first_text, path)
    info["abstract"] = guess_abstract(first_text)
    info["sources"] = "pdf(首页)"

    if use_crossref and info["doi"]:
        cr = crossref(info["doi"])
        if cr:
            info["cr"] = cr
            # 交叉核对：Crossref 与首页提取不一致时，两者都写进卡片
            if cr.get("title"):
                info["title"] = cr["title"]
            if cr.get("authors"):
                info["authors"] = cr["authors"]
            info["year"] = cr.get("year")
            info["venue"] = cr.get("venue")
            info["volume"], info["issue"], info["page"] = cr.get("volume"), cr.get("issue"), cr.get("page")
            info["sources"] = "pdf(首页) + crossref"
            # 摘要回退：很多出版商首页只有 Abstract 标题而没有正文段，此时用 Crossref 的 abstract
            if not info.get("abstract") and cr.get("abstract"):
                info["abstract"] = cr["abstract"]
                info["sources"] += "(摘要来自 crossref)"
            if cr.get("title") and info["authors_line"]:
                pass

    rows = build_card(info)
    card = make_card_pdf(rows)
    card_tmp = (out_pdf or path) + ".card.pdf"
    io.open(card_tmp, "wb").write(card)

    import pymupdf as fitz
    src = fitz.open(path)
    carddoc = fitz.open(card_tmp)
    if in_place:
        if backup:
            import shutil
            shutil.copy2(path, os.path.splitext(path)[0] + "__backup.pdf")
        out = fitz.open()
        out.insert_pdf(carddoc)
        out.insert_pdf(src)
        tmp_out = path + ".annotating"
        out.save(tmp_out, garbage=3, deflate=True)
        out.close()
        src.close(); carddoc.close()
        os.replace(tmp_out, path)
        result_pdf = path
    else:
        out = fitz.open()
        out.insert_pdf(carddoc)
        out.insert_pdf(src)
        out.save(out_pdf, garbage=3, deflate=True)
        out.close()
        src.close(); carddoc.close()
        result_pdf = out_pdf
    try:
        os.remove(card_tmp)
    except OSError:
        pass

    # 同名 md
    cr = info.get("cr") or {}
    md = ["---",
          f"title: {info['title'] or ''}",
          f"authors: {'；'.join(info.get('authors') or []) or info.get('authors_line') or ''}",
          f"year: {info.get('year') or ''}",
          f"venue: {info.get('venue') or ''}",
          f"volume: {info.get('volume') or ''}",
          f"issue: {info.get('issue') or ''}",
          f"page: {info.get('page') or ''}",
          f"doi: {info.get('doi') or ''}",
          f"pages_total: {total}",
          f"source_file: {os.path.basename(path)}",
          f"annotated_pdf: {os.path.basename(result_pdf)}",
          f"extracted_at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
          f"field_sources: {info['sources']}",
          "---", "",
          "## 摘要", "", info.get("abstract") or "（未能自动提取，建议人工补）", "",
          "## 首页原文片段（供人工核对）", "", "```text",
          first_text[:1500].strip() or "(空)", "```", "",
          "> 说明：题目/作者/年份/期刊优先取 Crossref；摘要与创新点来自 PDF 首页文本，",
          "> 均为机器提取，重要用途请回原文核对。", ""]
    if out_md:
        io.open(out_md, "w", encoding="utf-8", newline="\n").write("\n".join(md))

    return {"path": path, "status": "ok", "pages": total,
            "out_pdf": result_pdf, "out_md": out_md,
            "title": info["title"], "doi": info.get("doi", ""),
            "sources": info["sources"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[], help="要处理的 PDF 路径")
    ap.add_argument("--list", help="含路径清单的文本文件（每行一个）")
    ap.add_argument("--out-dir", default=None, help="输出目录；默认与源文件同目录")
    ap.add_argument("--in-place", action="store_true", help="原地插入解析卡（危险，建议配 --backup）")
    ap.add_argument("--backup", action="store_true", help="原地模式先写 <stem>__backup.pdf")
    ap.add_argument("--no-crossref", action="store_true", help="不查 Crossref")
    ap.add_argument("--md-only", action="store_true", help="只出 md，不动 PDF")
    ap.add_argument("--propose", help="propose.tsv：只处理 accept=y 的行")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = list(args.files)
    if args.list:
        files += [l.strip().strip('"') for l in io.open(args.list, encoding="utf-8").read().split("\n") if l.strip()]
    if args.propose:
        rows = [l.split("\t") for l in io.open(args.propose, encoding="utf-8").read().rstrip("\n").split("\n")[1:]]
        head = io.open(args.propose, encoding="utf-8").read().split("\n")[0].split("\t")
        i_acc, i_path = head.index("accept"), head.index("path")
        files += [r[i_path] for r in rows if len(r) > i_path and r[i_acc].strip().lower() in ("y", "yes", "1", "是")]
    files = [f for f in dict.fromkeys(files) if f]  # 去重保序
    if args.limit:
        files = files[:args.limit]
    if not files:
        print("没有要处理的文件（用 --files / --list / --propose）", file=sys.stderr)
        return 2

    print(f"待处理 {len(files)} 个文件；模式={'原地' if args.in_place else ('仅 md' if args.md_only else '另存 __note.pdf')}")
    results = []
    t0 = time.time()
    for i, f in enumerate(files, 1):
        stem, _ = os.path.splitext(os.path.basename(f))
        out_dir = args.out_dir or os.path.dirname(f) or "."
        out_pdf = None if (args.md_only or args.in_place) else os.path.join(out_dir, stem + "__note.pdf")
        out_md = os.path.join(out_dir, stem + "__note.md")
        try:
            r = annotate_one(f, out_pdf, out_md, in_place=args.in_place, backup=args.backup,
                             use_crossref=not args.no_crossref)
        except Exception as e:
            r = {"path": f, "status": "error", "error": f"{type(e).__name__}: {e}"}
        r["i"] = i
        results.append(r)
        flag = {"ok": "✓", "error": "✗", "missing": "?"}.get(r.get("status"), "?")
        extra = f" {r.get('pages')}页" if r.get("pages") else ""
        print(f"  {flag} [{i}/{len(files)}] {os.path.basename(f)}{extra}"
              + (f"  <- {r.get('error')}" if r.get("error") else ""))

    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n完成：成功 {ok} / {len(files)}，耗时 {time.time() - t0:.1f}s")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "archive", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, "annotate-log.json")
    io.open(log, "w", encoding="utf-8", newline="\n").write(json.dumps(
        {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": "in-place" if args.in_place else ("md-only" if args.md_only else "copy"),
         "results": results}, ensure_ascii=False, indent=1))
    print(f"日志：{os.path.abspath(log)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
