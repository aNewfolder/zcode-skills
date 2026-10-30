#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""make_vocab.py — 从库目录树抽取"主题词表"，供人工填写含义。

用途：每次爬取目录后，用这张表理解文件夹名在说什么（缩写、专有名词、方向词）。
      script 侧只做抽取与统计；**含义由人填**，填好后归位建议的准确率会明显提升。

输出：
  lib-map/词表-vocab.tsv     机器可用（脚本读它做同义词扩展）
  lib-map/缩写词对照表.md   人看的（按出现次数排序，带示例路径与待填列）

用法: python -X utf8 make_vocab.py --dirs "<项目>\\lib-map\\dirs.tsv" --out-dir "<项目>\\lib-map" [--only-empty]
      --only-empty  只输出"含义"列为空的行（用于后续增量补填）
"""
import argparse
import io
import os
import re
import sys
from collections import Counter, defaultdict

# 常见英文虚词/泛词，不列入（避免表太长、也避免给弱信号）
STOP = {
    "the", "and", "for", "with", "from", "into", "based", "using", "via", "its", "new",
    "analysis", "study", "studies", "recent", "progress", "advances", "review", "reviews",
    "paper", "papers", "other", "misc", "temp", "tmp", "old", "new", "copy", "final",
    "part", "chapter", "figure", "table", "data", "file", "files", "doc", "docs",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", required=True, help="库目录清单.tsv 或 库扫描原始数据.json（来自 lib_scan.py）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--only-empty", action="store_true")
    ap.add_argument("--min-count", type=int, default=1)
    args = ap.parse_args()

    paths = []
    if args.dirs.lower().endswith(".json"):
        import json as _json
        data = _json.load(io.open(args.dirs, encoding="utf-8"))
        for d in data.get("dirs", []):
            paths.append((d.get("rel", ""), int(d.get("n_pdf") or 0)))
    else:
        rows = io.open(args.dirs, encoding="utf-8").read().rstrip("\n").split("\n")
        head = rows[0].split("\t")
        i_rel, i_n = head.index("rel"), head.index("n_pdf")
        for l in rows[1:]:
            if not l.strip():
                continue
            c = l.split("\t")
            paths.append((c[i_rel], int(c[i_n]) if c[i_n].isdigit() else 0))
    paths = [p for p in paths if p[0]]

    # 词元统计：同时看"整条路径"与"单个目录段"（缩写常出现在段里，如 A_Photodetection）
    tok_path = Counter()
    examples = defaultdict(list)
    ctx = defaultdict(Counter)
    seg_count = Counter()

    def toks(x):
        return [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,}", x)]

    for rel, npdf in paths:
        segs = [s for s in re.split(r"[\\/]", rel) if s]
        seg_count[rel.split("\\")[0].split("/")[0]] += 1
        seen = set()
        for s in segs:
            for w in toks(s):
                lw = w.lower()
                for suf in ("s",):
                    if len(lw) > 4 and lw.endswith(suf) and (lw[:-1] in ("ldm", "pd", "pds", "cqd", "msm", "vcsels")):
                        lw = lw[:-1]
                lw = {"pds": "pd", "ldms": "ldm", "cqs": "cqd"}.get(lw, lw)
                if lw in STOP or len(w) < 2:
                    continue
                if lw not in seen:
                    tok_path[lw] += 1
                    seen.add(lw)
                # 该词的常见搭配（同段的其它词）
                for w2 in toks(s):
                    if w2.lower() != lw and w2.lower() not in STOP:
                        ctx[lw][w2] += 1
        if npdf:
            for w in seen:
                if len(examples[w]) < 3:
                    examples[w].append(f"{rel} ({npdf}篇)")

    order = [w for w, n in tok_path.most_common() if n >= args.min_count]
    out_tsv = os.path.join(args.out_dir, "词表-vocab.tsv")
    existing = {}
    if os.path.exists(out_tsv):
        for l in io.open(out_tsv, encoding="utf-8").read().rstrip("\n").split("\n")[1:]:
            c = l.split("\t")
            if len(c) >= 3:
                existing[c[0]] = (c[1], c[2], c[3] if len(c) > 3 else "")

    lines = ["token\tcount\t含义（请填）\t同义/别名（请填，逗号分隔）\t示例路径"]
    filled = 0
    for w in order:
        mean, alias, _ = existing.get(w, ("", "", ""))
        if mean:
            filled += 1
        if args.only_empty and mean:
            continue
        top_ctx = "、".join(x for x, _ in ctx[w].most_common(4))
        ex = " ｜ ".join(examples[w][:2])
        if top_ctx:
            ex = f"{ex}   [常见搭配: {top_ctx}]"
        lines.append(f"{w}\t{tok_path[w]}\t{mean}\t{alias}\t{ex}")

    io.open(out_tsv, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print(f"已写出 {out_tsv}")
    print(f"  词条 {len(order)} 个（total 不同词 {len(tok_path)}），其中已填含义 {filled} 个")

    # 人看的 markdown
    md = ["# 缩写 / 主题词对照表", "",
          f"- 来源：`{os.path.basename(args.dirs)}`（{len(paths)} 个目录）",
          f"- 词条数：**{len(order)}**（已排除 {len(STOP)} 个泛词）",
          "- 填写方式：编辑 `词表-vocab.tsv` 的「含义」「同义/别名」两列即可；",
          "  填好后 `propose_papers.py` 会用别名扩展匹配，明显提高归位准确率。", "",
          "## 高频词（≥3 次）", "",
          "| 词 | 出现目录数 | 含义（待填） | 常见搭配 | 示例目录 |",
          "|---|---|---|---|---|"]
    for w in order:
        if tok_path[w] < 3:
            continue
        mean, alias, _ = existing.get(w, ("", "", ""))
        top_ctx = "、".join(x for x, _ in ctx[w].most_common(4))
        md.append(f"| `{w}` | {tok_path[w]} | {mean or '**?**'} | {top_ctx[:60]} | {examples[w][0][:70] if examples[w] else ''} |")
    md += ["", "## 低频词（1–2 次）", "",
           "| 词 | 次数 | 含义（待填） | 示例目录 |", "|---|---|---|---|"]
    for w in order:
        if tok_path[w] >= 3:
            continue
        mean, _, _ = existing.get(w, ("", "", ""))
        md.append(f"| `{w}` | {tok_path[w]} | {mean or '?'} | {examples[w][0][:74] if examples[w] else ''} |")
    md += ["", "> 说明：这张表是「论文用词」与「目录用词」之间的桥梁。目录名本身好读（不同主题词只有 "
           + str(len(order)) + " 个），难的是论文标题里的说法与目录里的说法不一致，"
           "所以**别名列比含义列更值钱**。", ""]
    p_md = os.path.join(args.out_dir, "缩写词对照表.md")
    io.open(p_md, "w", encoding="utf-8", newline="\n").write("\n".join(md))
    print(f"已写出 {p_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
