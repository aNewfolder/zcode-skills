#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pdf_identity_check.py — 批量身份校验：确认每个 PDF 第一页标题与清单标题一致。

这是 2026-09-11T1040 漏掉 #14（把一篇神经发育论文当成钽酸锂 Nature 论文入库）的那一步。
只依赖 pdfplumber（已随 skill 的 requirements.txt 安装）。

用法:
  python -X utf8 pdf_identity_check.py --dir <PDF目录> [--manifest manifest.tsv] [--threshold 0.5] [--json out.json]

输出: 每个 PDF 的页数 + 首页推测标题 + 与清单标题的词重合度 + 判定
判定: OK / SUSPECT(标题不符) / ERROR
"""
import argparse
import json
import os
import re
import sys
import unicodedata

NOISE = re.compile(
    r"^(article|research|review|letter|open access|check for updates|https?:|doi|received|accepted|published|abstract|nature|science|vol\.?\s|no\.?\s)",
    re.IGNORECASE,
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def overlap(a: str, b: str) -> float:
    A = {w for w in norm(a).split() if len(w) > 2}
    B = {w for w in norm(b).split() if len(w) > 2}
    if not A or not B:
        return 0.0
    return sum(1 for w in A if w in B) / len(A)


def guess_title(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    cands = [l for l in lines if 18 < len(l) < 160 and not NOISE.match(l) and not re.fullmatch(r"[\d\s.,;:()\-–—]+", l)]
    pick = cands[:2] or lines[:2]
    return " ".join(pick)[:300]


def load_manifest(path):
    """返回 {basename: {id,title,status}}，兼容 13/14 列及缺表头的 manifest。"""
    rows = {}
    if not path or not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = [l.rstrip("\n") for l in fh if l.strip()]
    if not lines:
        return rows
    head = lines[0].split("\t")
    # 有的副本表头缺列，用数据行长度对齐：只在表头像表头时才当表头
    if "title" in head and "id" in head:
        data = lines[1:]
    else:
        head = ["id", "title", "doi", "year", "venue", "status", "pdf_path"]
        data = lines
    def col(c, name):
        try:
            return c[head.index(name)]
        except (ValueError, IndexError):
            return ""
    for l in data:
        c = l.split("\t")
        rel = col(c, "pdf_path")
        if not rel:
            continue
        rows[os.path.basename(rel.replace("/", os.sep))] = {
            "id": col(c, "id"), "title": col(c, "title"), "status": col(c, "status"),
        }
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    try:
        import pdfplumber
    except ImportError:
        print("缺少 pdfplumber：请先 pip install -r requirements.txt", file=sys.stderr)
        return 2

    rows = load_manifest(args.manifest)
    files = sorted(f for f in os.listdir(args.dir) if f.lower().endswith(".pdf"))
    # 附属材料（X__SI01.pdf）不是正文，不进标题比对，单独归类统计。
    SUPP = re.compile(r"__SI\d+\.pdf$", re.IGNORECASE)
    report = []
    for name in files:
        full = os.path.join(args.dir, name)
        entry = rows.get(name)
        if SUPP.search(name):
            stem = SUPP.sub("", name)
            report.append({
                "file": name, "kind": "supplement",
                "main_stem": stem,
                "main_present": os.path.exists(os.path.join(args.dir, stem + ".pdf")),
                "verdict": "SUPPLEMENT",
            })
            continue
        rec = {"file": name, "id": (entry or {}).get("id"), "expected": (entry or {}).get("title"),
               "manifest_status": (entry or {}).get("status")}
        try:
            with pdfplumber.open(full) as pdf:
                rec["pages"] = len(pdf.pages)
                text = pdf.pages[0].extract_text() or ""
            rec["title"] = guess_title(text)
            rec["chars"] = len(text)
            if entry:
                # 双判据：单行标题猜测容易被跨行标题 / 期刊页眉破坏，因此同时看整页文本。
                m_title = overlap(entry["title"], rec["title"])
                m_page = overlap(entry["title"], text)
                rec["match_title"] = round(m_title, 2)
                rec["match_page"] = round(m_page, 3)
                rec["match"] = round(max(m_title, m_page), 2)
                rec["verdict"] = "OK" if rec["match"] >= args.threshold else "SUSPECT"
            else:
                rec["match"] = None
                rec["verdict"] = "NO_MANIFEST_ROW"
        except Exception as e:  # 损坏/加密/占位文件
            rec["verdict"] = "ERROR"
            rec["error"] = f"{type(e).__name__}: {e}"
        report.append(rec)

    bad = [r for r in report if r["verdict"] not in ("OK", "NO_MANIFEST_ROW", "SUPPLEMENT")]
    supps = [r for r in report if r["verdict"] == "SUPPLEMENT"]
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n==== 共 {len(report)} 个 PDF | 正文 OK {sum(1 for r in report if r['verdict'] == 'OK')} | "
          f"可疑 {len(bad)} | 附属材料 {len(supps)} | 无清单行 {sum(1 for r in report if r['verdict'] == 'NO_MANIFEST_ROW')} ====")
    for sup in supps:
        flag = "其正文在" if sup["main_present"] else "!! 找不到对应正文"
        print(f"  [附属] {sup['file']}  <-  {sup['main_stem']}.pdf  {flag}")
    orphan = [r for r in report if r["verdict"] == "NO_MANIFEST_ROW"]
    for o in orphan:
        print(f"  [无清单行] {o['file']}")
    for b in bad:
        print(f"  [{b['verdict']}] {b['file']}")
        print(f"      清单标题: {(b.get('expected') or '')[:100]}")
        print(f"      实际首页: {(b.get('title') or b.get('error') or '')[:100]}  匹配度={b.get('match')}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print(f"\n已写出: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
