#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""suggest_dirs.py — 从解析结果产出"归档建议清单"（供人工确认）。

依据（按你要求）：用**解析得到的题目 + 摘要**判断，不只看题目；
只用库里**已存在的目录**做候选，绝不造路径；
给多个建议（默认 3 个）+ 置信度 + 理由，并留一列给人填数字。

打分：token 匹配（论文侧用词表别名扩展）× IDF（罕见词权重高）
      + 叶目录名命中加权（目录树本身就是你的分类体系，越深越具体）

输出 propose-<批名>.tsv，列：
  id  path  title  year  venue  doi  cand1_path  cand1_score  cand2_path  cand2_score
  cand3_path  cand3_score  shared_terms  choose(人填)  manual_dir(人填)  note(人填)
"""
import argparse
import io
import json
import math
import os
import re
import sys
import time

STOP = {'the', 'and', 'for', 'with', 'from', 'using', 'based', 'via', 'are', 'this', 'that',
        'study', 'paper', 'results', 'show', 'have', 'has', 'been', 'its', 'can', 'which',
        'high', 'low', 'new', 'also', 'into', 'than', 'more', 'most', 'such', 'these'}


def toks(x):
    return [w.lower() for w in re.findall(r'[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}', x or '')]


def load_vocab(path):
    """返回 {token: set(别名 token)}，含自身。"""
    alias = {}
    if not path or not os.path.exists(path):
        return alias
    rows = io.open(path, encoding='utf-8').read().rstrip('\n').split('\n')
    head = rows[0].split('\t')
    i_tok, i_alias = head.index('token'), head.index('同义/别名（请填，逗号分隔）')
    for l in rows[1:]:
        c = l.split('\t')
        if len(c) <= i_alias:
            continue
        tok = c[i_tok].strip().lower()
        if not tok:
            continue
        s = alias.setdefault(tok, set())
        s.add(tok)
        for a in re.split(r'[,，;；]', c[i_alias]):
            for w in toks(a):
                if w != tok:
                    s.add(w)
    return alias


def expand(words, alias):
    out = set(words)
    for w in words:
        out |= alias.get(w, set())
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--parse', required=True, help='parse-*.json（步骤 2 产物）')
    ap.add_argument('--dirs', required=True, help='lib-map/库目录清单.tsv（旧名 dirs.tsv 亦可）')
    ap.add_argument('--vocab', default=None, help='lib-map/词表-vocab.tsv（旧名 vocab.tsv 亦可）')
    ap.add_argument('--out', required=True, help='propose-*.tsv 输出路径')
    ap.add_argument('--top', type=int, default=3)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--min-pdf', type=int, default=0, help='只考虑 PDF 数 >= N 的目录（默认全部）')
    args = ap.parse_args()

    alias = load_vocab(args.vocab)
    print('词表: %d 个词条%s' % (len(alias), '（含别名扩展）' if alias else '（未提供，仅原词匹配）'))

    rows = io.open(args.dirs, encoding='utf-8').read().rstrip('\n').split('\n')
    head = rows[0].split('\t')
    i_rel, i_n = head.index('rel'), head.index('n_pdf')
    dirs = []
    for l in rows[1:]:
        c = l.split('\t')
        if not c[i_rel].strip() or c[i_rel] == '.':
            continue
        npdf = int(c[i_n]) if c[i_n].isdigit() else 0
        if npdf < args.min_pdf:
            continue
        dt = set(toks(c[i_rel].replace('\\', ' ')))
        leaf = [s for s in re.split(r'[\\/]', c[i_rel]) if s][-1]
        dirs.append({'rel': c[i_rel], 'npdf': npdf, 'tokens': dt,
                     'leaf': set(toks(leaf)), 'depth': c[i_rel].count('\\') + 1})
    # IDF：目录树中出现越少的词越有区分力
    df = {}
    for d in dirs:
        for w in d['tokens']:
            df[w] = df.get(w, 0) + 1
    N = len(dirs)
    idf = {w: math.log((N + 1) / (c + 0.5)) for w, c in df.items()}
    print('候选目录: %d 个（已排除根目录与空目录）' % N)

    items = json.load(io.open(args.parse, encoding='utf-8'))['items']
    if args.limit:
        items = items[:args.limit]

    out_rows = []
    for it in items:
        f = it.get('file') or ''
        if not (f.lower().endswith('.pdf')):
            continue
        title = it.get('title') or it.get('title_pdf') or ''
        abstract = it.get('abstract') or ''
        if not title and not abstract:
            continue
        q_raw = set(toks(title)) | set(toks(abstract)[:120])
        q = expand(q_raw, alias)
        scored = []
        for d in dirs:
            shared = q & d['tokens']
            if not shared:
                continue
            w = sum(idf.get(x, 0) for x in shared)
            leaf_hit = q & d['leaf']
            w += 1.2 * sum(idf.get(x, 0) for x in leaf_hit)
            # 目录越深越具体，轻微加权（你的分类体系本身是信号）
            w += 0.15 * d['depth']
            scored.append((round(w, 2), d, shared))
        scored.sort(key=lambda x: -x[0])
        top = scored[:args.top]
        # 置信度：以最高分自身的强度与"与第二名的差距"共同决定
        if not top:
            conf = ['0'] * args.top
        else:
            best = top[0][0]
            second = top[1][0] if len(top) > 1 else 0
            base = min(0.95, best / 40.0)          # 分数越高越可信（40 约为强匹配量级）
            gap = 1.0 if second <= 0 else min(1.0, (best - second) / max(best, 0.1) * 1.5 + 0.4)
            c1 = round(base * gap, 2)
            conf = [c1] + [round(max(0.05, c1 * 0.6), 2) for _ in top[1:]]
            conf += ['0'] * (args.top - len(conf))
        shared_terms = ' '.join(sorted(top[0][2])[:8]) if top else ''
        out_rows.append({
            'id': it.get('id', ''),
            'path': it.get('path', ''),
            'title': title[:120],
            'year': it.get('year', ''), 'venue': (it.get('venue') or '')[:40],
            'doi': it.get('doi', ''),
            'type': it.get('doc_type_hint', '') + ('/综述?' if it.get('is_review_hint') else ''),
            **{('cand%d_path' % (i + 1)): (top[i][1]['rel'] if i < len(top) else '') for i in range(args.top)},
            **{('cand%d_score' % (i + 1)): (top[i][0] if i < len(top) else '') for i in range(args.top)},
            **{('cand%d_conf' % (i + 1)): conf[i] for i in range(args.top)},
            'shared_terms': shared_terms,
            'choose': '', 'manual_dir': '', 'note': '',
        })

    head_cols = ['id', 'path', 'title', 'type', 'year', 'venue', 'doi']
    for i in range(args.top):
        head_cols += ['cand%d_path' % (i + 1), 'cand%d_score' % (i + 1), 'cand%d_conf' % (i + 1)]
    head_cols += ['shared_terms', 'choose', 'manual_dir', 'note']
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    for _ in range(8):
        try:
            with io.open(args.out, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write('\t'.join(head_cols) + '\n')
                for r in out_rows:
                    fh.write('\t'.join(str(r.get(k, '')) for k in head_cols) + '\n')
            break
        except PermissionError:
            time.sleep(3)
    print('已写出 %s（%d 篇）' % (args.out, len(out_rows)))
    print('\n前 5 篇的建议：')
    for r in out_rows[:5]:
        print('  %s' % (r['title'][:56] or r['path'][-40:]))
        for i in range(args.top):
            if r['cand%d_path' % (i + 1)]:
                print('     %d) %s  分=%s 置信=%s' % (i + 1, r['cand%d_path' % (i + 1)],
                                                      r['cand%d_score' % (i + 1)], r['cand%d_conf' % (i + 1)]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
