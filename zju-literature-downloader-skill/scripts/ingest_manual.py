#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ingest_manual.py — 处理"人工下载"的 PDF：识别 → 重命名 → 查重 → 解析 → 转存。

与自动下载并行的人工路径需要额外三步：文件名规范化、与既有合集查重、批量改名。
本脚本一次做完这些，并保持"默认 dry-run、默认不改原件内容"的一贯安全姿势。

流程:
  1. inventory   扫描来源目录（默认只读元数据 + 首页文本）
  2. identify    首页文本 -> 题目/DOI；无 DOI 则用 Crossref 书目检索反查 DOI 并核对标题
  3. name        按 <首作者姓>_<年>_<期刊缩写>_<标题词>.pdf 生成规范名
  4. dedupe      与 --existing（项目 PDFs 目录）比 SHA256、与 --manifest 比 DOI
  5. plan        写 rename-plan.tsv，人工可改 new_name / accept 列
  6. apply       --execute 才执行：先把文件复制/移动到 --dest，再按计划改名（两阶段，防同名互撞）

用法:
  python -X utf8 ingest_manual.py --from "<下载目录>" --dest "<项目\\PDFs>" --plan "<项目\\解析\\manual-plan.tsv>"
  python -X utf8 ingest_manual.py --from "<目录>" --dest "<目录>" --plan "<...>" --execute
  python -X utf8 ingest_manual.py --from "<目录>" --plan "<...>" --in-place-rename   # 只改名，不搬运
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

NOISE = re.compile(
    r"^(article|research|review|letter|open access|check for updates|https?:|doi|received|accepted|"
    r"published|abstract|www\.|©|copyright|downloaded from|see discussions|content downloaded|"
    r"citation|journal|volume|vol\.|pp\.|permission)", re.I)

VENUE_ABBR = {
    "nature": "Nature", "science": "Science", "nature photonics": "NatPhoton",
    "nature communications": "NatCommun", "nature reviews electrical engineering": "NatRevEE",
    "applied physics letters": "APL", "apl photonics": "APLPhoton",
    "optics express": "OE", "optics letters": "OL", "optica": "Optica",
    "advances in optics and photonics": "AOP",
    "journal of lightwave technology": "JLT", "ieee photonics technology letters": "PTL",
    "ieee journal of selected topics in quantum electronics": "IEEEJSTQE",
    "ieee nanotechnology magazine": "IEEENanoMag",
    "progress in quantum electronics": "PQE", "physical review letters": "PRL",
    "physical review applied": "PRApplied", "light: science & applications": "LSA",
    "laser & photonics reviews": "LPR", "acs photonics": "ACSPhotonics",
    "advanced materials": "AdvMater", "nano letters": "NanoLett",
    "中国工程科学": "CAE", "光学学报": "ActaOptSin",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(c)
    return h.hexdigest()


def sanitize(s, maxlen=70):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "", s)
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_.")
    return s[:maxlen].rstrip("_.")


def first_page_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        total = len(pdf.pages)
        text = pdf.pages[0].extract_text() or ""
    return total, text


def guess_title(text):
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    cands = [l for l in lines if 18 < len(l) < 200 and not NOISE.match(l)
             and not re.fullmatch(r"[\d\s.,;:()\-–—]+", l)]
    return re.sub(r"\s+", " ", " ".join(cands[:2] or lines[:2])).strip()[:250]


def guess_doi(text, path):
    # PDF 里 DOI 常被换行/断字拆开（"10.1038/s41586-" + "018-0551-y"），先归一化再找
    t = re.sub(r"[-\u2010\u2011\u2012\u2013\u2014]\s*\n\s*", "-", text or "")
    t = re.sub(r"\s+", " ", t)
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", t)
    if m:
        return m.group(0).rstrip(".,;)")
    m = re.search(r"(10\.\d{4,9}[-._;()/:A-Za-z0-9]+)", os.path.basename(path))
    return m.group(1) if m else ""


def norm_key(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def overlap(a, b):
    A = {w for w in re.findall(r"[a-z0-9]{3,}", (a or "").lower())}
    B = {w for w in re.findall(r"[a-z0-9]{3,}", (b or "").lower())}
    return 0.0 if not A or not B else sum(1 for w in A if w in B) / len(A)


def crossref_doi(doi, timeout=10):
    if not doi:
        return None
    try:
        req = urllib.request.Request("https://api.crossref.org/works/" + urllib.parse.quote(doi),
                                     headers={"User-Agent": "zju-lit/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            m = json.loads(r.read().decode("utf-8", "replace")).get("message", {})
        return {
            "doi": m.get("DOI", doi),
            "title": (m.get("title") or [""])[0],
            "authors": [(a.get("family", "") or a.get("name", "")) for a in (m.get("author") or [])],
            "year": ((m.get("issued", {}).get("date-parts") or [[None]])[0] or [None])[0],
            "venue": (m.get("container-title") or [""])[0],
            "type": m.get("type", ""), "source": "crossref-doi",
        }
    except Exception:
        return None


def crossref_search(title, timeout=12, rows=4):
    """无 DOI 时按书目检索反查。"""
    if not title:
        return None
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": rows,
                                "select": "DOI,title,author,issued,container-title,type"})
    try:
        req = urllib.request.Request("https://api.crossref.org/works?" + q,
                                     headers={"User-Agent": "zju-lit/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            items = json.loads(r.read().decode("utf-8", "replace")).get("message", {}).get("items", [])
    except Exception:
        return None
    best = None
    for it in items:
        t = (it.get("title") or [""])[0]
        sc = overlap(title, t)
        cand = {
            "doi": it.get("DOI", ""), "title": t,
            "authors": [(a.get("family", "") or a.get("name", "")) for a in (it.get("author") or [])],
            "year": ((it.get("issued", {}).get("date-parts") or [[None]])[0] or [None])[0],
            "venue": (it.get("container-title") or [""])[0],
            "type": it.get("type", ""), "match": round(sc, 2), "source": "crossref-search",
        }
        if best is None or cand["match"] > best["match"]:
            best = cand
    return best


def build_name(meta, fallback_stem):
    # 注意两点：
    #  a) 传入的必须是 crossref 原始 meta（authors 是列表），不是拼接后的字符串；
    #  b) Crossref 的 family 已经是"姓"，不要再 split()[-1]，否则 Wang -> W。
    au = meta.get("authors") or []
    if isinstance(au, (list, tuple)):
        fam_raw = str(au[0]) if au else ""
    else:
        fam_raw = str(au)
    # crossref 侧可能是"Wang；Zhang；Chen"这样的拼接串，取第一段
    fam_raw = re.split(r"[;；,，]", fam_raw)[0].strip()
    fam = sanitize(fam_raw, 24)
    year = str(meta.get("year") or "")
    venue = (meta.get("venue") or "").strip()
    # 元数据不全时，硬造 "Unknown_n.d._Journal_…" 没有任何信息量，直接用原文件名
    if len(fam) < 2 or not year or not venue:
        return sanitize(os.path.splitext(os.path.basename(fallback_stem))[0], 90) + ".pdf"
    abbr = VENUE_ABBR.get(venue.strip().lower())
    if not abbr:
        abbr = sanitize(re.sub(r"[^A-Za-z0-9\u4e00-\u9fff ]", " ", venue), 20).replace("_", "") or "Journal"
    title = meta.get("title") or fallback_stem
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", title)
    stop = {"a", "an", "the", "of", "for", "on", "in", "and", "to", "with", "by", "using", "via", "at", "from"}
    kept = [w for w in words if w.lower() not in stop][:9]
    short = "".join(kept) if any(re.search(r"[\u4e00-\u9fff]", w) for w in kept) else "_".join(kept)
    short = sanitize(short, 60)
    return f"{fam}_{year}_{abbr}_{short}.pdf"


def load_existing(existing_dirs, manifest):
    seen = {"sha": {}, "doi": {}}
    for d in existing_dirs or []:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.lower().endswith(".pdf"):
                p = os.path.join(d, fn)
                try:
                    seen["sha"][sha256(p)] = p
                except OSError:
                    pass
    if manifest and os.path.exists(manifest):
        for line in io.open(manifest, encoding="utf-8").read().split("\n"):
            if not line or line.startswith("#") or line.startswith("id\t"):
                continue
            c = line.split("\t")
            if len(c) > 2 and c[2].strip() and c[2].strip() != "n/a":
                seen["doi"][c[2].strip().lower()] = c[0]
    return seen


def read_plan(path):
    rows = io.open(path, encoding="utf-8").read().rstrip("\n").split("\n")
    head = rows[0].split("\t")
    out = []
    for line in rows[1:]:
        if not line.strip():
            continue
        c = line.split("\t")
        c += [""] * (len(head) - len(c))
        out.append({head[i]: c[i].strip() for i in range(len(head))})
    return head, out


def execute_plan(args):
    """按计划执行改名/搬运。计划文件只读，不改写；执行结果另存 <plan>.executed.tsv。"""
    head, rows = read_plan(args.from_plan)
    for col in ("path", "new_name"):
        if col not in head:
            print(f"计划缺少列 {col}", file=sys.stderr)
            return 2
    todo = [r for r in rows if r.get("accept", "").lower() in ("y", "yes", "1", "是") and r.get("new_name")]
    skipped = len(rows) - len(todo)
    print(f"计划共 {len(rows)} 条；accept=y 的 {len(todo)} 条；跳过 {skipped} 条")
    if not args.execute:
        for r in todo:
            print(f"  [dry] {os.path.basename(r['path'])}  ->  {r['new_name']}"
                  + (f"   (到 {args.dest})" if args.dest else ""))
        print("\n（未加 --execute，仅预演）")
        return 0
    if not args.dest and not args.in_place_rename:
        print("需要 --dest（搬运目标）或 --in-place-rename（原地改名）", file=sys.stderr)
        return 2
    if args.dest:
        os.makedirs(args.dest, exist_ok=True)

    log, moved = [], 0
    for r in todo:
        src = r["path"]
        if not os.path.exists(src):
            print(f"  ? 源已不在: {os.path.basename(src)}")
            log.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t\t\tMISSING")
            continue
        target_dir = args.dest or os.path.dirname(src)
        tmp = os.path.join(target_dir, f".__ingest_{int(time.time()*1000)}_{os.getpid()}.pdf")
        try:
            if args.in_place_rename:
                os.rename(src, tmp)
            elif args.move:
                shutil.move(src, tmp)
            else:
                shutil.copy2(src, tmp)
            final = os.path.join(target_dir, r["new_name"])
            if os.path.exists(final):
                try:
                    same = sha256(final) == sha256(tmp)
                except OSError:
                    same = False
                if same:
                    os.remove(tmp)
                    print(f"  = 目标已有相同文件，跳过: {r['new_name']}")
                    log.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{final}\t\tduplicate")
                    continue
                base, ext = os.path.splitext(r["new_name"])
                n = 2
                while os.path.exists(os.path.join(target_dir, f"{base}__dup{n}{ext}")):
                    n += 1
                final = os.path.join(target_dir, f"{base}__dup{n}{ext}")
                print(f"  ! 同名不同内容，改存 {os.path.basename(final)}")
            os.replace(tmp, final)
            if not args.in_place_rename and not args.move:
                os.remove(src)          # 复制模式下删除来源，等效搬运
            moved += 1
            print(f"  ✓ {os.path.basename(src)[:44]} -> {os.path.basename(final)}")
            log.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{final}\t{r.get('sha256','')[:16]}\tingested")
        except OSError as e:
            print(f"  ✗ {os.path.basename(src)}: {e}")
            log.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t\t\tFAILED:{e}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "archive", "logs")
    os.makedirs(log_dir, exist_ok=True)
    lp = os.path.join(log_dir, "ingest-log.tsv")
    with io.open(lp, "a", encoding="utf-8", newline="\n") as fh:
        if (not os.path.exists(lp)) or os.path.getsize(lp) == 0:
            fh.write("time\tsrc\tdst\tsha16\tstatus\n")
        fh.write("\n".join(log) + ("\n" if log else ""))
    # 执行痕迹另存，保留人工确认的原始计划
    exe = args.from_plan + ".executed.tsv"
    with io.open(exe, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(head) + "\n")
        for r in rows:
            fh.write("\t".join(r.get(k, "") for k in head) + "\n")
    print(f"\n实际执行 {moved} 条；日志 {lp}")
    print(f"计划副本（含确认痕迹）: {exe}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=None, help="人工下载的目录（或单个 PDF）")
    ap.add_argument("--from-plan", dest="from_plan", default=None,
                    help="按已人工确认的 plan.tsv 直接执行（不重新扫描、不覆盖计划）")
    ap.add_argument("--dest", help="转存目标目录；不填则只出计划")
    ap.add_argument("--plan", default=None, help="rename-plan.tsv 输出路径（--from 模式必需）")
    ap.add_argument("--existing", nargs="*", default=[], help="既有合集目录（查重用）")
    ap.add_argument("--manifest", default=None, help="manifest.tsv（按 DOI 查重）")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--in-place-rename", action="store_true", help="只在原目录改名，不搬运")
    ap.add_argument("--move", action="store_true", help="搬运时用移动而非复制")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    # ---------- 模式 A：按已确认的计划执行（不重新推导 accept） ----------
    if args.from_plan:
        return execute_plan(args)

    if not args.src:
        print("需要 --from（扫描并出计划）或 --from-plan（执行已确认的计划）", file=sys.stderr)
        return 2
    if not args.plan:
        print("--from 模式需要 --plan <输出路径>", file=sys.stderr)
        return 2
    if args.execute and os.path.exists(args.plan):
        print(f"拒绝覆盖已存在的计划文件: {args.plan}", file=sys.stderr)
        print('  计划已确认请改用:  --from-plan "<该文件>" --execute', file=sys.stderr)
        print("  要重新生成计划，请先删除或改名该文件。", file=sys.stderr)
        return 2

    srcs = []
    if os.path.isfile(args.src) and args.src.lower().endswith(".pdf"):
        srcs = [args.src]
    else:
        for dirpath, _d, files in os.walk(args.src):
            for f in files:
                if f.lower().endswith(".pdf"):
                    srcs.append(os.path.join(dirpath, f))
    srcs.sort()
    if args.limit:
        srcs = srcs[:args.limit]
    if not srcs:
        print("没有找到 PDF", file=sys.stderr)
        return 2

    seen = load_existing(args.existing, args.manifest)
    print(f"来源 {len(srcs)} 个 PDF；既有合集 {len(seen['sha'])} 个哈希 / {len(seen['doi'])} 个 DOI 用于查重")

    rows = []
    t0 = time.time()
    for i, p in enumerate(srcs, 1):
        stem = os.path.splitext(os.path.basename(p))[0]
        rec = {"path": p, "old_name": os.path.basename(p), "status": "", "note": ""}
        try:
            total, text = first_page_text(p)
            rec["pages"] = total
            title_pdf = guess_title(text)
            # 手工下载的文件名常常就是论文标题（中文刊尤其明显，首页反而只有页眉），
            # 所以把文件名也作为一个候选，谁与 Crossref 更吻合就用谁。
            stem_words = re.sub(r"[_\-]+", " ", stem)
            stem_words = re.sub(r"\s+", " ", stem_words).strip()
            title = title_pdf
            doi = guess_doi(text, p)
            rec["title_pdf"] = title_pdf
            rec["title_from_filename"] = stem_words
            rec["doi_from_pdf"] = doi
            meta = crossref_doi(doi) if doi else None
            if not meta:
                meta = crossref_search(title_pdf)
            if not meta and stem_words and stem_words != title_pdf:
                meta = crossref_search(stem_words)
            if meta:
                # 候选标题：首页提取 vs 文件名。谁与 Crossref 的标题更吻合用谁——
                # 中文刊首页常被页眉占据，而文件名往往正是标题。
                m_pdf = overlap(title_pdf, meta.get("title", ""))
                m_fn = overlap(stem_words, meta.get("title", ""))
                if m_fn > m_pdf:
                    title, rec["title_source"] = stem_words, "filename"
                    mo_selected = m_fn
                else:
                    title, rec["title_source"] = title_pdf, "pdf-first-page"
                    mo_selected = m_pdf
                rec["match_candidates"] = {"pdf": round(m_pdf, 2), "filename": round(m_fn, 2)}
            if meta:
                rec["doi"] = meta.get("doi", "")
                rec["title"] = meta.get("title") or title
                rec["authors"] = "；".join([a for a in meta.get("authors", []) if a][:6])
                rec["year"] = meta.get("year") or ""
                rec["venue"] = meta.get("venue") or ""
                rec["source"] = meta.get("source", "")
                rec["match"] = meta.get("match", "")
                # 身份核对：PDF 首页标题 vs Crossref 标题
                mo = overlap(title, rec["title"])
                rec["match_pdf_title"] = round(overlap(title_pdf, rec["title"]), 2)
                rec["title_match"] = round(mo, 2)
                has_cjk = bool(re.search(r"[\u4e00-\u9fff]", title))
                meta_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", rec["title"] or ""))
                if has_cjk and not meta_has_cjk:
                    # 中文标题不可能对应一篇纯英文论文：这是检索噪声，不是同一篇
                    rec["status"] = "SUSPECT"
                    rec["note"] = "中文标题被英文检索结果命中，拒绝采用该 DOI"
                    rec["doi"] = rec.get("doi_from_pdf", "")     # 退回首页提取的 DOI（可能为空）
                    rec["title"] = title
                    rec["year"] = rec["venue"] = rec["authors"] = ""
                    rec["source"] = "rejected-crossref"
                elif mo < 0.5:
                    rec["status"] = "SUSPECT"
                    rec["note"] = "首页标题与 Crossref 不一致，需人工确认"
            else:
                rec["doi"] = doi
                rec["title"] = title
                rec["status"] = "NO_METADATA"
                rec["note"] = "Crossref 查不到，按首页文本命名"
            rec["new_name"] = build_name(rec, stem)
            rec["accept"] = "n" if rec["status"] == "SUSPECT" else "y"
        except Exception as e:
            rec["status"] = "ERROR"
            rec["note"] = f"{type(e).__name__}: {e}"
            rec["new_name"] = ""
            rec["accept"] = "n"

        # 查重
        try:
            h = sha256(p)
            rec["sha256"] = h
            if h in seen["sha"]:
                rec["status"] = "DUPLICATE_SHA"
                rec["note"] = "与合集内文件字节完全相同: " + os.path.basename(seen["sha"][h])
                rec["accept"] = "n"
            elif rec.get("doi") and rec["doi"].lower() in seen["doi"]:
                rec["status"] = "DUPLICATE_DOI"
                rec["note"] = f"DOI 已在清单中（#{seen['doi'][rec['doi'].lower()]}）"
                rec["accept"] = "n"
        except OSError as e:
            rec["note"] += f" | hash失败:{e}"

        rows.append(rec)
        flag = {"": "✓", "SUSPECT": "!", "NO_METADATA": "?", "ERROR": "✗",
                "DUPLICATE_SHA": "=", "DUPLICATE_DOI": "="}.get(rec["status"], "?")
        print(f"  {flag} [{i}/{len(srcs)}] {rec['old_name'][:52]}")
        print(f"      -> {rec.get('new_name') or '(无)'}   {rec.get('status')} {rec.get('note','')[:60]}")

    head = ["id", "path", "old_name", "new_name", "accept", "status", "title_match", "doi",
            "title", "authors", "year", "venue", "pages", "sha256", "note"]
    os.makedirs(os.path.dirname(os.path.abspath(args.plan)), exist_ok=True)
    with io.open(args.plan, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(head) + "\n")
        for i, r in enumerate(rows, 1):
            r2 = dict(r)
            r2["id"] = i
            fh.write("\t".join(str(r2.get(k, "")) for k in head) + "\n")
    print(f"\n计划已写出: {args.plan}")
    print(f"耗时 {time.time() - t0:.1f}s")

    if not args.execute:
        print("\n（dry-run：未改名、未搬运。确认 plan 的 accept/new_name 后加 --execute）")
        return 0

    # ---------- 执行：两阶段改名 + 可选搬运 ----------
    todo = [r for r in rows if r.get("accept", "").lower() in ("y", "yes", "1", "是") and r.get("new_name")]
    print(f"\n执行 {len(todo)} 条")
    stages = []
    if args.in_place_rename:
        stages.append((None, None))          # 原目录内改名
    else:
        if not args.dest:
            print("需要 --dest 才能搬运（或用 --in-place-rename）", file=sys.stderr)
            return 2
        dest = args.dest
        os.makedirs(dest, exist_ok=True)
        stages.append((dest, args.move))

    log = []
    for dest, use_move in stages:
        for r in todo:
            src = r["path"]
            if not os.path.exists(src):
                print(f"  ? 源已不在: {r['old_name']}")
                continue
            target_dir = dest or os.path.dirname(src)
            tmp = os.path.join(target_dir, f".__ingest_{int(time.time()*1000)}_{os.getpid()}.pdf")
            try:
                if use_move:
                    shutil.move(src, tmp)
                else:
                    shutil.copy2(src, tmp)
                final = os.path.join(target_dir, r["new_name"])
                if os.path.exists(final) and sha256(final) != r.get("sha256", ""):
                    base, ext = os.path.splitext(r["new_name"])
                    n = 2
                    while os.path.exists(os.path.join(target_dir, f"{base}__dup{n}{ext}")):
                        n += 1
                    final = os.path.join(target_dir, f"{base}__dup{n}{ext}")
                    print(f"  ! 目标同名，改存 {os.path.basename(final)}")
                os.replace(tmp, final)
                if not use_move and not args.in_place_rename:
                    os.remove(src)          # 复制模式下删除来源，等效"搬运"
                print(f"  ✓ {r['old_name'][:44]} -> {os.path.basename(final)}")
                log.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t{final}\t{r.get('sha256','')[:16]}\tingested")
            except OSError as e:
                print(f"  ✗ {r['old_name']}: {e}")
                log.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{src}\t\t\tFAILED:{e}")
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "archive", "logs")
    os.makedirs(log_dir, exist_ok=True)
    lp = os.path.join(log_dir, "ingest-log.tsv")
    with io.open(lp, "a", encoding="utf-8", newline="\n") as fh:
        if os.path.getsize(lp) == 0 if os.path.exists(lp) else True:
            fh.write("time\tsrc\tdst\tsha16\tstatus\n")
        fh.write("\n".join(log) + ("\n" if log else ""))
    print(f"日志: {lp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
