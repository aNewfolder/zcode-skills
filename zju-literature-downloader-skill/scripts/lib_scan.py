#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lib_scan.py — 扫描任意外部文献库，产出「结构事实」，供后续归位建议使用。

设计要点：
  * 默认**只读元数据**（路径/大小/时间），不打开 PDF —— 对 OneDrive 占位文件不会触发下载。
  * `--hash` / `--pages` 才会读内容，且各自有抽样上限，避免一次性把整个云盘拉下来。
  * 输出 inventory.json + dirs.tsv + 结构报告.md（给模型看的目录级聚合）。
  * 目录聚合表是"喂给模型的最小信息"：每目录的文件数、总大小、深度、代表文件名。
    1000 篇 80 个同主题目录时，模型看这一张表就能整目录归类，不必逐篇问。
  * `--scale-tsv` 另出一份「目录规模表」：一行一目录，**同时给直属口径与含子目录口径**。
    大库里绝大多数文件沉在深子目录，只看直属数会严重低估一个分支的重量，所以两套口径并列。
    该表带 `#` 注释头（口径、生成时间、根目录），是给人看/存档的快照，**每次扫描整体覆盖**。

用法:
  python -X utf8 lib_scan.py --root "D:\\文献库" [--out-dir 解析] [--max-depth N] [--hash 30] [--pages 30]
  python -X utf8 lib_scan.py --root "D:\\文献库" --scale-tsv "D:\\文献库\\_库地图\\目录规模.tsv"

关于「库地图」：若库根下存在 `_库地图\\`，它是**这个库自己的**分类依据（语义表 + 规模快照），
不随 skill 分发——不同人的库各有各的。扫描只负责刷新其中的 `目录规模.tsv`，不碰语义表。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import time
from collections import defaultdict

SKIP_DIRS = {".git", ".svn", "$RECYCLE.BIN", "System Volume Information", ".sync", "@eaDir"}

SCALE_COLS = ["rel", "depth", "n_pdf_direct", "n_pdf_all", "n_caj_all", "n_files_all", "mb_all", "sample"]


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}PB"


# Windows 的 MAX_PATH 是 260；超过后 os.stat/open 会直接失败。
# 本文件库实测有 27 个 PDF 的路径长于 259 字符，它们此前被 walk() 静默丢弃（4053 → 4026），
# 导致结构报告长期低估规模。给长路径加 \\?\ 前缀即可绕过，不需要改注册表。
_LONG_PATH_THRESHOLD = 240


def fs_path(p):
    """Windows 下对超长绝对路径加 \\\\?\\ 前缀；其它情况原样返回。"""
    if os.name != "nt":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):                      # UNC: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + p[2:]
    if len(p) >= _LONG_PATH_THRESHOLD:
        return "\\\\?\\" + p
    return p


def sha256(path, limit_mb=200):
    h = hashlib.sha256()
    read = 0
    cap = limit_mb * 1024 * 1024
    with open(fs_path(path), "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if read >= cap:
                return h.hexdigest() + f"(前{limit_mb}MB)"
    return h.hexdigest()


def walk(root, max_depth=None):
    """单次遍历：返回 (目录列表, 文件列表)。只读元数据。"""
    dirs, files = [], []
    root_depth = root.rstrip("\\/").count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        depth = dirpath.count(os.sep) - root_depth
        if max_depth is not None and depth >= max_depth:
            dirnames[:] = []
        n_pdf = 0
        n_stat_fail = 0
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(fs_path(full))
            except OSError:
                # 不再静默丢弃：记账，最后在报告里显式给出，避免"少算了"看不出来
                n_stat_fail += 1
                continue
            rec = {
                "path": full,
                "rel": os.path.relpath(full, root),
                "name": fn,
                "ext": os.path.splitext(fn)[1].lower(),
                "bytes": st.st_size,
                "mtime": int(st.st_mtime),
                "dir": dirpath,
                "depth": depth,
                # 云盘（OneDrive/群晖/百度）用私有属性位标记"仅云端"文件；标准位不可靠，
                # 实测 OneDrive 占位文件 st_file_attributes = 0x80020（既非 OFFLINE 也非 REPARSE）。
                # 因此同时记录原始值与若干常见位的判断结果，交由报告层给出口径说明。
                "attrs_raw": getattr(st, "st_file_attributes", None),
                "reparse": bool(getattr(st, "st_file_attributes", 0) & 0x400),
                "offline": bool(getattr(st, "st_file_attributes", 0) & 0x1000),
            }
            files.append(rec)
            if rec["ext"] == ".pdf":
                n_pdf += 1
        dirs.append({"path": dirpath, "rel": os.path.relpath(dirpath, root), "depth": depth,
                     "n_files": len(filenames), "n_pdf": n_pdf, "n_stat_fail": n_stat_fail})
    return dirs, files


def rel_parts(rel):
    """把相对路径切成目录名序列（兼容 \\ 与 /）。"""
    return [p for p in rel.replace("/", os.sep).split(os.sep) if p and p != "."]


def recursive_totals(files):
    """{目录rel: {pdf,caj,files,bytes}} —— 每个目录含全部子孙的合计。

    只做一遍：对每个文件，把它计入自己的每一级祖先目录。
    """
    rec = defaultdict(lambda: {"pdf": 0, "caj": 0, "files": 0, "bytes": 0})
    for f in files:
        parts = rel_parts(f["rel"])
        for i in range(1, len(parts)):          # 末段是文件名，不算目录
            a = rec[os.sep.join(parts[:i])]
            a["files"] += 1
            a["bytes"] += f["bytes"]
            if f["ext"] == ".pdf":
                a["pdf"] += 1
            elif f["ext"] == ".caj":
                a["caj"] += 1
    return rec


def write_scale_tsv(path, root, dirs, files, rec, sample_n, scanned_at, truncated=False):
    """写「目录规模表」：一行一目录，直属与递归两套口径并列。

    与 dirs.tsv 的分工：dirs.tsv 是**喂给模型的**最小信息（只保留 6 列，且不含空目录，
    会被 lib_move_v2.py 的 --rescan 重写，故列结构不宜变）；本表是**给人看/存档的快照**，
    列更全、含空目录、带注释头，整体覆盖写，不允许手改。
    """
    direct = defaultdict(list)
    for f in files:
        direct[f["dir"]].append(f)

    head = [
        "# 生成文件：请勿手工编辑（每次扫描整体覆盖）",
        "# 来源：lib_scan.py（一次目录遍历，只读元数据，未读文件内容）",
        f"# 生成时间：{scanned_at}",
        f"# 根目录：{root}",
        "# 列：rel=相对路径 depth=层级 n_pdf_direct=直属PDF n_pdf_all=含子目录PDF "
        "n_caj_all=含子目录CAJ n_files_all=含子目录全部文件 mb_all=含子目录MB "
        f"sample=直属前{sample_n}个文件名",
        "# 口径：all 列为该目录含全部子孙的合计；直属为 0 不等于空目录（内容可能全在子目录）。",
    ]
    if truncated:
        head.append("# 注意：本次带 --max-depth，递归口径不完整，勿据此判断整库重量分布。")
    lines = head + ["\t".join(SCALE_COLS)]

    for d in sorted(dirs, key=lambda x: (x["depth"], x["rel"])):
        rel = "" if d["rel"] == "." else d["rel"]
        if not rel:
            continue                            # 根目录自身不作为一行，避免与各顶层重复计数
        own = sorted(direct.get(d["path"], []), key=lambda x: x["name"])
        a = rec.get(rel, {"pdf": 0, "caj": 0, "files": 0, "bytes": 0})
        sample = " | ".join(i["name"] for i in own[:sample_n])
        lines.append("\t".join([
            rel, str(d["depth"]), str(d["n_pdf"]), str(a["pdf"]), str(a["caj"]),
            str(a["files"]), f"{a['bytes'] / 1048576:.1f}", sample,
        ]))

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    return len(dirs) - 1                        # 减掉被跳过的根目录行


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="文献库根目录（外部路径，任何时候都可换）")
    ap.add_argument("--out-dir", default=None, help="输出目录，默认 <当前项目>/解析")
    ap.add_argument("--max-depth", type=int, default=None)
    ap.add_argument("--hash", type=int, default=0, metavar="N", help="对前 N 个 PDF 计算哈希（会读文件）")
    ap.add_argument("--pages", type=int, default=0, metavar="N", help="对前 N 个 PDF 统计页数（会读文件、触发云下载）")
    ap.add_argument("--sample-每目录", dest="sample", type=int, default=3, help="目录聚合表里每个目录取几个代表文件名")
    ap.add_argument("--scale-tsv", default=None, metavar="PATH",
                    help="另写一份「目录规模表」（直属+含子目录两套口径，含空目录，带注释头），"
                         "例：--scale-tsv \"<库根>\\_库地图\\目录规模.tsv\"")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "解析")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    t0 = time.time()
    dirs, files = walk(root, args.max_depth)
    pdfs = [f for f in files if f["ext"] == ".pdf"]
    cajs = [f for f in files if f["ext"] == ".caj"]
    others = [f for f in files if f["ext"] not in (".pdf", ".caj")]
    total = sum(f["bytes"] for f in files)
    scanned_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    print(f"扫描完成（{time.time() - t0:.1f}s，仅元数据）")
    print(f"  根目录   : {root}")
    print(f"  子目录   : {len(dirs)}")
    print(f"  PDF      : {len(pdfs)}  {human(sum(f['bytes'] for f in pdfs))}")
    if cajs:
        print(f"  CAJ      : {len(cajs)}  （CNKI 格式，pdfplumber/pypdf 读不了，只能先登记题目）")
    print(f"  其他文件 : {len(others)}  {human(sum(f['bytes'] for f in others))}")
    depth_max = max((d["depth"] for d in dirs), default=0)
    print(f"  最大深度 : {depth_max}")
    n_stat_fail = sum(d["n_stat_fail"] for d in dirs)
    if n_stat_fail:
        print(f"  !! 元数据读取失败 : {n_stat_fail} 个（路径/权限问题，未计入上面的统计）")

    # ---- 目录聚合（给模型的最小信息） ----
    by_dir = defaultdict(list)
    for f in pdfs:
        by_dir[f["dir"]].append(f)
    dir_rows = []
    for d in sorted(dirs, key=lambda x: (x["depth"], x["rel"])):
        items = sorted(by_dir.get(d["path"], []), key=lambda x: x["name"])
        if d["n_pdf"] == 0 and d["n_files"] == 0:
            continue  # 空目录不进聚合表
        dir_rows.append({
            "rel": d["rel"] or ".",
            "depth": d["depth"],
            "n_pdf": d["n_pdf"],
            "n_files": d["n_files"],
            "mb": round(sum(i["bytes"] for i in items) / 1048576, 1),
            "sample": [i["name"] for i in items[:args.sample]],
        })

    # ---- 递归口径：这次扫描顺便算出，供规模表与报告使用 ----
    rec = recursive_totals(files)

    # ---- 可选：哈希 / 页数（会读文件，务必限量） ----
    if args.hash:
        print(f"计算前 {args.hash} 个 PDF 的 SHA256（会读文件）...")
        for f in sorted(pdfs, key=lambda x: x["path"])[:args.hash]:
            try:
                f["sha256"] = sha256(f["path"])
            except OSError as e:
                f["sha256_error"] = str(e)
    if args.pages:
        try:
            import pdfplumber
        except ImportError:
            pdfplumber = None
            print("  pdfplumber 不可用，跳过页数统计")
        if pdfplumber:
            print(f"统计前 {args.pages} 个 PDF 的页数（会触发 OneDrive 云下载）...")
            for i, f in enumerate(sorted(pdfs, key=lambda x: x["path"])[:args.pages], 1):
                try:
                    with pdfplumber.open(fs_path(f["path"])) as pdf:
                        f["pages"] = len(pdf.pages)
                except Exception as e:
                    f["pages_error"] = f"{type(e).__name__}: {e}"
                if i % 10 == 0:
                    print(f"  {i}/{args.pages}")

    # 口径：只要属性值不是常规本地文件的 0x20（Archive），就视为"可能是云占位"。
    def looks_cloud(f):
        raw = f.get("attrs_raw")
        return raw is not None and (raw & ~0x20) != 0
    n_reparse = sum(1 for f in pdfs if looks_cloud(f))
    n_std_reparse = sum(1 for f in pdfs if f.get("reparse"))
    inventory = {
        "scanned_at": scanned_at,
        "root": root,
        "elapsed_sec": round(time.time() - t0, 1),
        "max_depth": depth_max,
        "counts": {"dirs": len(dirs), "files": len(files), "pdfs": len(pdfs), "cajs": len(cajs),
                   "others": len(others), "stat_failed": n_stat_fail},
        "bytes": {"total": total, "pdfs": sum(f["bytes"] for f in pdfs)},
        "cloud_placeholder_pdfs": n_reparse,
        "cloud_placeholder_criterion": "st_file_attributes 除 Archive(0x20) 外还有其它位（云盘同步用的私有位）",
        "std_reparse_bit_pdfs": n_std_reparse,
        "hash_sample": args.hash,
        "pages_sample": args.pages,
        "dirs": dir_rows,
        "pdfs": pdfs,
        "others_sample": others[:200],
    }
    inv_path = os.path.join(out_dir, "inventory.json")
    io.open(inv_path, "w", encoding="utf-8", newline="\n").write(json.dumps(inventory, ensure_ascii=False, indent=1))

    # ---- dirs.tsv：给模型看的目录级聚合（列结构固定，勿加列：lib_move_v2.py --rescan 会按位置重写） ----
    tsv = os.path.join(out_dir, "dirs.tsv")
    with io.open(tsv, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("rel\tdepth\tn_pdf\tn_files\tmb\tsample\n")
        for r in dir_rows:
            fh.write(f"{r['rel']}\t{r['depth']}\t{r['n_pdf']}\t{r['n_files']}\t{r['mb']}\t"
                     f"{' | '.join(r['sample'])}\n")

    # ---- 可选：目录规模表（给人和存档，跟 dirs.tsv 分开，互不干扰） ----
    scale_path = None
    if args.scale_tsv:
        scale_path = os.path.abspath(args.scale_tsv)
        n_rows = write_scale_tsv(scale_path, root, dirs, files, rec, args.sample, scanned_at,
                                 truncated=args.max_depth is not None)
        print(f"\n目录规模表已写出（一行一目录，含直属与递归两套口径）：{scale_path}  （{n_rows} 行）")

    # ---- 结构报告 ----
    # 顶层：按 rel 的第一段聚合（含该子树合计），比只看 depth==0 更有信息量
    top_agg = {}
    for r in dir_rows:
        seg = r["rel"].split(os.sep)[0].split("/")[0]
        a = top_agg.setdefault(seg, {"rel": seg, "n_pdf": 0, "n_files": 0, "mb": 0.0, "sample": []})
        a["n_pdf"] += r["n_pdf"]
        a["n_files"] += r["n_files"]
        a["mb"] += r["mb"]
        if not a["sample"] and r["sample"]:
            a["sample"] = r["sample"]
    top = list(top_agg.values())

    # 重量分布：多少文件沉在第 2 层以下 —— 决定「只写顶层语义」够不够
    deep_pdf = sum(1 for f in pdfs if f["depth"] > 2)
    top_pdf = sum(a["n_pdf"] for a in top)
    n_empty = sum(1 for d in dirs
                  if d["rel"] != "." and d["n_pdf"] == 0 and d["n_files"] == 0 and rec.get(d["rel"]) is None)

    md = [f"# 文献库结构报告", "",
          f"- 扫描时间：{scanned_at}",
          f"- 根目录：`{root}`",
          f"- 规模：**{len(pdfs)}** 篇 PDF ／ {human(inventory['bytes']['pdfs'])}；"
          f"{len(dirs)} 个目录，最深 {depth_max} 层；其他文件 {len(others)} 个"
          + (f"（其中 .caj **{len(cajs)}** 个，CNKI 格式，工具读不了，只能先登记题目）" if cajs else ""),
          f"- 云端占位迹象：**{n_reparse}** / {len(pdfs)} 篇 PDF 的属性含非 Archive 位"
          f"（标准 ReparsePoint 位命中 {n_std_reparse} 篇）——这类文件**首次读取会触发 OneDrive 下载**，",
          "  所以逐篇解析必须限量、分批，不要一次性读整个库。",
          f"- 文件位置：**{deep_pdf}** / {len(pdfs)} 篇（{100.0 * deep_pdf / max(len(pdfs), 1):.0f}%）在第 3 层及以下。"
          f"→ 语义表写到第 2 层只能管「范围」，判断一个分支的重量必须用递归口径（见 `--scale-tsv`）。",
          f"- 真空白目录：**{n_empty}** 个（直属无文件且子孙也无文件）；"
          f"另有纯容器目录（直属为空但子孙有文件）若干，二者不可混为一谈。",
          ]
    if n_stat_fail:
        md.append(f"- **元数据读取失败 {n_stat_fail} 个**（路径超长/权限）：未计入以上统计。"
                  f"Windows MAX_PATH 已由 `fs_path()` 处理，若此项不为 0 请查看 `--out-dir` 的日志。")
    md += [f"- 扫描耗时：{inventory['elapsed_sec']}s（仅元数据，未读文件内容）", "",
          "## 顶层分布", "", "| 目录 | PDF | 文件 | 大小 | 代表文件 |", "|---|---|---|---|---|"]
    for r in sorted(top, key=lambda x: -x["n_pdf"])[:40]:
        md.append(f"| `{r['rel']}` | {r['n_pdf']} | {r['n_files']} | {r['mb']}MB | "
                  f"{'；'.join(s[:52] for s in r['sample'])} |")
    md += ["", "## 目录清单（按 PDF 数排序，前 120）", "", "| 目录 | 深度 | PDF | 大小 |", "|---|---|---|---|"]
    for r in sorted(dir_rows, key=lambda x: -x["n_pdf"])[:120]:
        md.append(f"| `{r['rel']}` | {r['depth']} | {r['n_pdf']} | {r['mb']}MB |")
    md += ["", "## 下一步", "",
           f"1. 这张目录聚合表（`dirs.tsv`）就是喂给模型的最小信息：{len(dir_rows)} 行，",
           "   模型据此对**整个目录**判主题，不必逐篇阅读。",
           "2. 逐篇确认时才需要读首页文本（会触发云下载），建议从 `--limit` 小的批次开始。"]
    if scale_path:
        md += [f"3. 规模快照另见 `{scale_path}`（含子目录口径，由本脚本整体覆盖写出）。"]
    md += [f"{4 if scale_path else 3}. 若库根下有 `_库地图\\`，其中的 `目录语义.tsv` 是这个库的判读依据；"
           f"扫描只刷新其中的 `目录规模.tsv`，不要动语义表。",
           ""]
    md_path = os.path.join(out_dir, "结构报告.md")
    io.open(md_path, "w", encoding="utf-8", newline="\n").write("\n".join(md))

    print(f"\n已写出：")
    print(f"  {inv_path}")
    print(f"  {tsv}   （{len(dir_rows)} 行目录聚合，给模型看）")
    print(f"  {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
