#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""parse_papers.py — 解析 PDF，产出结构化题录 + 文献类型判断（第 1 步，零 API）。

设计约束（来自讨论）：
  * 归档建议必须"先解析再建议"，所以本题录是下一步的输入；
  * 需要判断**文献类型**（综述 / 研究论文 / 展望 / 学位论文 / 会议 / 标准…），
    这在归位时经常是决定因素（你库里就有 `Review`、`1.Theories`、`A_Review` 这类目录）；
  * 全程本地：首页文本 + Crossref，不调用任何模型。

文献类型判断分两级：
  A) 首页启发式（本地、免费）：Review/综述/Review Article/tutorial/Perspective/Thesis 等标记；
  B) Crossref `type` 字段（权威但粒度粗）：journal-article / proceedings-article / book-chapter…
     并把 `subtype`/`subject` 一并记下。两者不一致时记为 `type_conflict`，留给人工。

用法:
  python -X utf8 parse_papers.py --files a.pdf b.pdf [--out-dir 解析] [--pages 2]
  python -X utf8 parse_papers.py --dir "<PDF目录>" [--limit 20] [--json out.json]
"""
import argparse
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

NOISE = re.compile(
    r"^(article|research|review|letter|open access|check for updates|https?:|doi|received|accepted|"
    r"published|abstract|www\.|©|copyright|downloaded from|see discussions|content downloaded|"
    r"citation|journal|volume|vol\.|pp\.|permission|issn|isbn)", re.I)

# ---- 文献类型：本地启发式（英文 + 中文） ----
TYPE_PATTERNS = [
    ("review",       r"\b(review article|invited review|systematic review|tutorial review|综述|文献综述|"
                     r"a review of|an overview of|review:)\b|\bReview\b(?=\s*$|\s*[:,])"),
    ("tutorial",     r"\b(tutorial|primer|introductory review)\b"),
    ("perspective",  r"\b(perspective|viewpoint|opinion|commentary|展望|评述)\b"),
    ("editorial",    r"\b(editorial|guest editorial|社论)\b"),
    ("thesis",       r"\b(thesis|dissertation|博士学位论文|硕士学位论文|学位论文)\b"),
    ("conference",   r"\b(conference|proceedings|symposium|workshop|cleo|ofc|ecoc)\b"),
    ("standard",     r"\b(specification|standard|implementation agreement|roadmap|whitepaper|white paper)\b"),
    ("book",         r"\b(handbook|edited by|chapter \d+|第\d+章)\b"),
]
TYPE_PATTERNS = [(k, re.compile(p, re.I)) for k, p in TYPE_PATTERNS]
# 只有出现在标题/副标题区（首页前若干行）才算强证据
HEAD_LINES = 12

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

VENUE_REVIEW_HINT = re.compile(r"\b(review|surveys|tutorials|advances in|progress in|reports on progress)\b", re.I)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def looks_like_authors(line):
    """判断一行是否是作者行（整行姓名+上标编号），据此停止标题收集。"""
    l = (line or "").strip()
    if len(l) < 12:
        return False
    if re.match(r"^\d+\s*(John|Division|Department|School|Institute|University|Laboratory)", l.split(",")[0], re.I):
        return False
    if re.search(r"univers|institut|depart|laborator|school|academy|college|大学|学院|研究所|实验室", l, re.I):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", l)
    caps_groups = len(re.findall(r"\b[A-Z]{3,}\b", l))          # DI ZHU / LINBO SHAO 这类全大写
    has_sup = bool(re.search(r"[A-Za-z]\s*,?\s*\d\s*[\*†‡]?\s*(,|$)", l))
    if caps_groups >= 2 and len(words) >= 4:
        return True
    if has_sup and l.count(",") >= 2:
        return True
    # 正常大小写的"名 姓, 名 姓"列表
    if re.match(r"^([A-Z][a-z]+\s+[A-Z][a-z]+\s*[0-9,*†‡]*\s*[,;]\s*){2,}", l):
        return True
    return False


def guess_title(text):
    """取首页标题：从最像标题的行开始收集，遇到作者行/摘要行即停。"""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    start = None
    for i, l in enumerate(lines[:14]):
        if NOISE.match(l) or re.fullmatch(r"[\d\s.,;:()\-–—]+", l):
            continue
        if len(l) >= 18:
            start = i
            break
    if start is None:
        return ""
    parts = []
    for l in lines[start:start + 4]:
        if parts and (looks_like_authors(l) or NOISE.match(l)
                      or re.match(r"^(abstract|摘要|keywords|关键词|index terms)\b", l, re.I)):
            break
        if looks_like_authors(l):
            break
        parts.append(l)
        if len(" ".join(parts)) > 160:
            break
    title = re.sub(r"\s+", " ", " ".join(parts)).strip()
    # 兜底：行内作者截断（"…lithium DI ZHU,1,* …"）
    m = re.search(r"\s(?:[A-Z]{2,}\s+)?[A-Z][A-Za-z'\-]{1,20}\s*,?\s*\d?\*?,?", title)
    if m and m.start() > 24:
        title = title[:m.start()]
    return title[:240]


def guess_authors(text):
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()][:HEAD_LINES]
    for l in lines:
        if NOISE.match(l):
            continue
        if re.search(r"univers|institut|depart|laborator|academy|college|school|大学|学院|研究所|实验室", l, re.I):
            continue
        if 8 < len(l) < 200 and (l.count(",") >= 1 or re.search(r"\d", l)):
            return norm(l)[:200]
    return ""


def guess_doi(text, path):
    t = re.sub(r"[-\u2010\u2011\u2012\u2013\u2014]\s*\n\s*", "-", text or "")
    t = re.sub(r"\s+", " ", t)
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", t)
    if m:
        return m.group(0).rstrip(".,;)")
    m = re.search(r"(10\.\d{4,9}[-._;()/:A-Za-z0-9]+)", os.path.basename(path))
    return m.group(1) if m else ""


def guess_venue_from_page(text):
    """从首页页眉提取期刊名，例如 "Vol.13,No.2/June2021/AdvancesinOpticsandPhotonics"。"""
    t = re.sub(r"\s+", "", text or "")
    for pat in [r"/([A-Za-z&]{6,})\d{4}$", r"([A-Za-z&]{8,})(?:Vol|VOL|No|NO)\.",
                r"(?:JOURNAL|Journal)(?:OF|of)([A-Za-z]{6,})"]:
        m = re.search(pat, t)
        if m:
            return m.group(1)
    m = re.search(r"([A-Z][A-Za-z&]{7,}(?:\s+[A-Z][A-Za-z&]{3,})?)\s*,?\s*VOL", text or "")
    return m.group(1).strip() if m else ""


def guess_first_author(line):
    m = re.match(r"^([A-Z][A-Za-z'\-]+)\s+([A-Z][A-Za-z'\-]+)", (line or "").strip())
    if m:
        return m.group(2)
    m = re.search(r"\b([A-Z][A-Za-z'\-]{2,})\b", line or "")
    return m.group(1) if m else ""


def guess_abstract(text):
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text)
    m = re.search(r"(?:ABSTRACT|Abstract|摘要|摘 要)[\s:：]*(.{60,2000}?)(?:Index Terms|Keywords|关键词|"
                  r"1\.\s*INTRODUCTION|I\.\s*INTRODUCTION|INTRODUCTION|引言)", t)
    if m:
        return norm(m.group(1))[:1500]
    m = re.search(r"(?:ABSTRACT|Abstract|摘要|摘 要)[\s:：]*(.{60,1500})", t)
    return norm(m.group(1))[:1500] if m else ""


def guess_doc_type(text, venue="", cr_type=""):
    """返回 (type, evidence, conflict)。本地启发式 + Crossref 类型交叉。"""
    head = "\n".join([l.strip() for l in (text or "").split("\n") if l.strip()][:HEAD_LINES])
    hits = []
    for name, pat in TYPE_PATTERNS:
        m = pat.search(head)
        if m:
            hits.append((name, m.group(0)))
    local = hits[0][0] if hits else ""
    ev = "; ".join(f"{n}<-{v}" for n, v in hits[:3])
    if not local and VENUE_REVIEW_HINT.search(venue or ""):
        local, ev = "review", f"venue<-{venue}"
    # Crossref 类型映射
    cr_map = {"journal-article": "research-article", "proceedings-article": "conference",
              "book-chapter": "book", "book": "book", "posted-content": "preprint",
              "report": "report", "standard": "standard", "dissertation": "thesis"}
    cr_norm = cr_map.get(cr_type, cr_type or "")
    conflict = ""
    if local and cr_norm and local not in (cr_norm, "review") and not (
            local == "review" and cr_norm == "research-article"):
        conflict = f"local={local} vs crossref={cr_norm}"
    return (local or cr_norm or "research-article"), ev, conflict


def crossref(doi, timeout=10):
    if not doi:
        return None
    m = None
    for attempt in range(3):
        try:
            req = urllib.request.Request("https://api.crossref.org/works/" + urllib.parse.quote(doi),
                                         headers={"User-Agent": "zju-lit/1.0"})
            with urllib.request.urlopen(req, timeout=timeout + attempt * 10) as r:
                m = json.loads(r.read().decode("utf-8", "replace")).get("message", {})
            break
        except Exception as e:
            globals().setdefault("_SEARCH_ERRORS", []).append(f"doi-lookup {type(e).__name__}: {e}")
            time.sleep(1.5 * (attempt + 1))
    if m is None:
        return None
    return {
        "title": (m.get("title") or [""])[0],
        "authors": [(a.get("family", "") or a.get("name", "")) for a in (m.get("author") or [])],
        "year": ((m.get("issued", {}).get("date-parts") or [[None]])[0] or [None])[0],
        "venue": (m.get("container-title") or [""])[0],
        "type": m.get("type", ""),
        "subtype": m.get("subtype", ""),
        "abstract": re.sub(r"<[^>]+>", " ", m.get("abstract", "") or "").strip(),
        "is_referenced_by_count": m.get("is-referenced-by-count", ""),
        "references_count": m.get("references-count", ""),
    }


def extract(path, pages=1):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        texts = [(pdf.pages[i].extract_text() or "") for i in range(min(pages, total))]
        # 再单独抓首页前 1200 字符，判断文献类型用（首页文本可能很长）
    return total, texts


def local_dir_candidates(title, abstract, dirs, k=6):
    """本地把 561 个目录缩到 k 个候选（零 token）。

    用 IDF 加权：常见词（photonic/silicon/integrated）权重低，罕见词（phosphorene/
    bolometer/grating）权重高。这样短目录名（如 IM-DD）不会因为词少而虚高。
    """
    import math
    def toks(x):
        return [w.lower() for w in re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", x or "")]

    docs = []
    for rel in dirs:
        dt = set(toks(rel.replace("\\", " ").replace("/", " ")))
        if dt:
            docs.append((rel, dt))
    if not docs:
        return []
    df = {}
    for _rel, dt in docs:
        for w in dt:
            df[w] = df.get(w, 0) + 1
    N = len(docs)
    idf = {w: math.log((N + 1) / (c + 0.5)) for w, c in df.items()}
    q = set(toks(title)) | set(toks(abstract))
    scored = []
    for rel, dt in docs:
        shared = q & dt
        if not shared:
            continue
        wsum = sum(idf.get(w, 0) for w in shared)
        leaf = [x for x in re.split(r"[\\/]", rel) if x][-1]
        leaf_hit = 1 if (q & set(toks(leaf))) else 0
        scored.append((round(wsum + 0.5 * leaf_hit, 2), len(shared), rel))
    scored.sort(reverse=True)
    return [{"dir": r, "weight": s_, "shared": n} for s_, n, r in scored[:k]]


def crossref_search_doi(title, extra="", venue_hint="", rows=5, timeout=15):
    """按书目检索返回候选，并给出"能否自动采用"的判定。

    返回 dict：
      {"auto": {...} | None, "candidates": [...], "reason": "..."}
    只有满足严格条件才给 auto；否则 auto=None，由上层标 needs_manual_doi。
    """
    if not title:
        return None
    def toks(x):
        return {w for w in re.findall(r"[a-z0-9]{3,}", (x or "").lower())}

    q = " ".join(x for x in (title, extra) if x)
    u = "https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query.bibliographic": q, "rows": rows})
    items = None
    last = ""
    for attempt in range(3):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "zju-lit/1.0"})
            with urllib.request.urlopen(req, timeout=timeout + attempt * 10) as r:
                items = json.loads(r.read().decode("utf-8", "replace"))["message"]["items"]
            break
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    if items is None:
        globals().setdefault("_SEARCH_ERRORS", []).append(last)
        return None

    A = toks(title)
    cands = []
    for it in items:
        t = (it.get("title") or [""])[0]
        B = toks(t)
        if not A or not B:
            continue
        contain = len(A & B) / len(A)
        length_ratio = min(len(A), len(B)) / max(len(A), len(B))
        f1 = overlap(title, t)
        if contain < 0.75 or length_ratio < 0.8 or f1 < 0.6:
            continue
        cands.append({
            "doi": it.get("DOI", ""), "title": t, "match": round(f1, 2),
            "containment": round(contain, 2), "len_ratio": round(length_ratio, 2),
            "year": ((it.get("issued", {}).get("date-parts") or [[None]])[0] or [None])[0],
            "venue": (it.get("container-title") or [""])[0],
            "type": it.get("type", ""), "cited": it.get("is-referenced-by-count", 0) or 0,
        })
    if not cands:
        return {"auto": None, "candidates": [], "reason": "检索无可用候选"}
    cands.sort(key=lambda c: (-(c["match"] + c["containment"] + c["len_ratio"]), -(c["cited"] or 0)))
    top = cands[0]

    # 判定一：多条同样可信 -> 不猜（会议/期刊同名论文实测标题完全一致）
    equally = [c for c in cands if abs(c["match"] - top["match"]) < 0.05
               and abs(c["containment"] - top["containment"]) < 0.05]
    if len(equally) > 1:
        return {"auto": None, "candidates": equally[:5],
                "reason": f"{len(equally)} 条同名候选（含 {', '.join(sorted({c['type'] for c in equally}))}），标题无法区分"}

    # 判定二：必须有期刊名佐证（否则可能是同名会议论文/预印本）
    if venue_hint:
        vh = toks(venue_hint)
        cv = toks(top.get("venue", ""))
        if not vh or not cv or not (vh & cv):
            return {"auto": None, "candidates": cands[:3],
                    "reason": f"刊名无法佐证（PDF 页眉={venue_hint!r} vs 候选={top.get('venue')!r}）"}

    # 判定三：候选必须是论文类型，且不能被更权威的同名条目压过
    if top.get("type") not in ("journal-article",):
        return {"auto": None, "candidates": cands[:3],
                "reason": f"最佳候选类型为 {top.get('type')}，非期刊论文（可能是会议论文/预印本）"}

    return {"auto": top, "candidates": cands[:3],
            "reason": f"唯一候选且刊名吻合（{top.get('venue')}）"}


def overlap(a, b):
    """F1 式相似度：兼顾"查询词被覆盖"与"目标词被覆盖"。

    旧版只算 交集/查询词数，使得 *Nonlinear* integrated photonics in thin-film lithium niobate
    与真标题同得 1.0，无法区分。
    """
    A = {w for w in re.findall(r"[a-z0-9]{3,}", (a or "").lower())}
    B = {w for w in re.findall(r"[a-z0-9]{3,}", (b or "").lower())}
    if not A or not B:
        return 0.0
    inter = len(A & B)
    if inter == 0:
        return 0.0
    p = inter / len(A)
    r = inter / len(B)
    f1 = 2 * p * r / (p + r)
    return round(f1, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--dir", help="目录：处理其中所有 PDF")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pages", type=int, default=1, help="读取几页（默认 1）")
    ap.add_argument("--json", default=None, help="汇总 JSON 输出路径")
    ap.add_argument("--dirs-from", default=None, help="目录清单（库目录清单.tsv 或 .json），用于本地候选筛选")
    ap.add_argument("--no-crossref", action="store_true")
    args = ap.parse_args()

    files = list(args.files)
    if args.dir:
        for dirpath, _d, fl in os.walk(args.dir):
            files += [os.path.join(dirpath, f) for f in fl if f.lower().endswith(".pdf")]
    files = [f for f in dict.fromkeys(files) if f]
    if args.limit:
        files = files[:args.limit]
    if not files:
        print("没有输入（--files 或 --dir）", file=sys.stderr)
        return 2

    # 候选目录清单（本轮重扫或读已有）
    dir_list = []
    if args.dirs_from and os.path.exists(args.dirs_from):
        if args.dirs_from.endswith(".json"):
            dir_list = [d["rel"] for d in json.load(io.open(args.dirs_from, encoding="utf-8"))["dirs"]]
        else:
            lines = io.open(args.dirs_from, encoding="utf-8").read().rstrip("\n").split("\n")
            dir_list = [l.split("\t")[0] for l in lines[1:] if l.strip()]
        print(f"候选目录清单: {len(dir_list)} 个（来自 {os.path.basename(args.dirs_from)}）")

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "解析")
    os.makedirs(out_dir, exist_ok=True)
    out_json = args.json
    results = []
    t0 = time.time()
    for i, p in enumerate(files, 1):
        rec = {"path": p, "file": os.path.basename(p)}
        try:
            # 摘要常跨页（Nature 的摘要就压在第 1-2 页交界），所以固定多读 1 页做摘要提取；
            # 页数本身仍以文本长度判定，不受影响。
            total, texts = extract(p, max(args.pages, 2))
            head_all = "\n".join(texts)
            first = texts[0] if texts else ""
            doi = guess_doi(head_all, p)
            cr = None
            if doi and not args.no_crossref:
                cr = crossref(doi)
            title_from_pdf = guess_title(first)
            authors_line_pdf = guess_authors(first)
            stem_words = re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(p))[0])).strip()
            first_author = guess_first_author(authors_line_pdf)
            venue_pdf = guess_venue_from_page(first)
            if not cr and not args.no_crossref:
                # 首页没有 DOI 时**不自动猜**：只在"唯一候选 + 刊名吻合 + 期刊论文"时才采用，
                # 否则留空并标 needs_manual_doi（错 DOI 会污染题录与归档，比空着更糟）
                res = crossref_search_doi(title_from_pdf,
                                          extra=" ".join(x for x in (first_author, venue_pdf) if x),
                                          venue_hint=venue_pdf)
                if not res or not res.get("candidates"):
                    # 丰富查询可能反而召不回（作者名写法不一致等）——退回纯标题再试，
                    # 判定规则不变（仍要求唯一 + 刊名吻合 + 期刊论文）
                    r2 = crossref_search_doi(title_from_pdf, venue_hint=venue_pdf)
                    if r2 and r2.get("candidates"):
                        r2["reason"] = "纯标题查询：" + str(r2.get("reason", ""))
                        res = r2
                if res is None:
                    rec["doi_status"] = "lookup_failed"
                elif res.get("auto"):
                    hit = res["auto"]
                    cr = crossref(hit["doi"])
                    if cr:
                        doi = hit["doi"]
                        rec["doi_status"] = "auto_from_search"
                        rec["doi_lookup"] = f"自动采用（{res['reason']}）f1={hit['match']} year={hit['year']}"
                else:
                    rec["doi_status"] = "needs_manual_doi"
                    rec["doi_candidates"] = res.get("candidates") or []
                    rec["doi_lookup"] = f"未自动采用：{res.get('reason')}"
            rec.update({
                "pages": total,
                "sha256": sha256(p),
                "title_pdf": title_from_pdf,
                "authors_line": authors_line_pdf,
                "abstract_pdf": guess_abstract(head_all),
                "pdf_head_excerpt": re.sub(r"\s+", " ", first)[:400],
            })
            if cr:
                rec["doi"] = cr.get("DOI", doi) or doi
                rec["title"] = cr.get("title") or title_from_pdf
                rec["authors"] = cr.get("authors") or []
                rec["year"] = cr.get("year") or ""
                rec["venue"] = cr.get("venue") or ""
                rec["crossref_type"] = cr.get("type")
                rec["subtype"] = cr.get("subtype")
                rec["abstract"] = (cr.get("abstract") or rec["abstract_pdf"] or "")
                rec["abstract_source"] = "crossref" if cr.get("abstract") else ("pdf" if rec["abstract_pdf"] else "none")
                rec["cited_by"] = cr.get("is_referenced_by_count", "")
                rec["refs"] = cr.get("references_count", "")
                rec["source"] = "crossref+pdf"
            else:
                rec["doi"] = doi
                rec["title"] = title_from_pdf
                rec["year"] = rec["venue"] = ""
                rec["abstract"] = rec["abstract_pdf"]
                rec["abstract_source"] = "pdf" if rec["abstract_pdf"] else "none"
                rec["source"] = "pdf-only"
            # 本地只给线索；最终类型由模型判断（Crossref 对 review/research 无区分能力）
            # 只看标题与摘要（首页正文里出现 "review" 太常见，会大量误判）
            type_scope = (rec.get("title") or "") + " " + (rec.get("abstract") or "")[:600]
            dtype, ev, conflict = guess_doc_type(type_scope, rec.get("venue", ""), rec.get("crossref_type", ""))
            rec["doc_type_hint"] = dtype
            rec["doc_type_evidence"] = ev
            rec["type_conflict"] = conflict
            rec["is_review_hint"] = dtype in ("review", "tutorial")
            if dir_list:
                rec["local_candidates"] = local_dir_candidates(rec.get("title", ""), rec.get("abstract", ""), dir_list)
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        results.append(rec)
        flag = "✓" if not rec.get("error") else "✗"
        rt = ("综述?" if rec.get("is_review_hint") else rec.get("doc_type_hint", "?"))
        print(f"  {flag} [{i}/{len(files)}] {rec['file'][:46]}")
        print(f"       类型={rt:<16} 页={rec.get('pages','?')} 年={rec.get('year','')} 刊={(rec.get('venue') or '')[:34]}")
        print(f"       题目={(rec.get('title') or '')[:76]}")
        if rec.get("type_conflict"):
            print(f"       ! 类型冲突: {rec['type_conflict']}")
        if rec.get("local_candidates"):
            cs = "  ".join("{0}×{1}".format(c.get('weight', c.get('score', '?')), c['dir'].split(chr(92))[-1][:22]) for c in rec["local_candidates"][:3])
            print(f"       本地候选: {cs}")

    # 每篇一份 md + 一份汇总 json
    for rec in results:
        if rec.get("error"):
            continue
        stem = os.path.splitext(rec["file"])[0]
        md = ["---",
              f"title: {rec.get('title','')}",
              f"authors: {'；'.join(rec.get('authors') or []) or rec.get('authors_line','')}",
              f"year: {rec.get('year','')}", f"venue: {rec.get('venue','')}",
              f"doi: {rec.get('doi','')}", f"pages_total: {rec.get('pages','')}",
              f"doc_type_hint: {rec.get('doc_type_hint','')}", f"is_review_hint: {rec.get('is_review_hint')}",
              f"crossref_type: {rec.get('crossref_type','')}",
              f"cited_by: {rec.get('cited_by','')}", f"references: {rec.get('refs','')}",
              f"sha256: {rec.get('sha256','')}",
              f"parsed_at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
              f"field_sources: {rec.get('source','')}", "---", "",
              "## 摘要", "", rec.get("abstract") or "（未能提取）", ""]
        if rec.get("doi_ambiguous"):
            md += ["## 需人工确认 DOI", "",
                   "检索到多条同名候选（会议论文与期刊论文标题常常完全一致），已放弃自动采用：", ""]
            for c in rec["doi_ambiguous"]:
                md.append(f"- `{c['doi']}`  {c.get('year')}  {c.get('venue')}  ({c.get('type')})")
            md.append("")
        if rec.get("type_conflict"):
            md += ["## 需人工确认", "", f"- 文献类型冲突：{rec['type_conflict']}", ""]
        if rec.get("local_candidates"):
            md += ["## 本地候选目录（仅供参考，非结论）", ""]
            for c in rec["local_candidates"]:
                md.append("- `{0}`  权重 {1}  共同词 {2}".format(c['dir'], c.get('weight', c.get('score', '?')), c['shared']))
            md.append("")
        io.open(os.path.join(out_dir, stem + "__parse.md"), "w", encoding="utf-8", newline="\n").write("\n".join(md))

    if out_json:
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        io.open(out_json, "w", encoding="utf-8", newline="\n").write(
            json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "count": len(results),
                        "scanned: note": "首页文本 + Crossref，未调用模型", "items": results},
                       ensure_ascii=False, indent=1))
    ok = sum(1 for r in results if not r.get("error"))
    rev = sum(1 for r in results if r.get("is_review_hint"))
    errs = globals().get("_SEARCH_ERRORS") or []
    if errs:
        from collections import Counter as _C
        print("\nCrossref 查询异常（去重后）:")
        for msg, n in _C(errs).most_common(5):
            print(f"  x{n}  {msg[:140]}")
    print(f"\n完成 {ok}/{len(files)}；本地线索判定综述? {rev} 篇；耗时 {time.time() - t0:.1f}s")
    print(f"每篇 md 在: {out_dir}")
    if out_json:
        print(f"汇总 json: {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
