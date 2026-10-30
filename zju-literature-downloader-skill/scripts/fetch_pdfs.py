#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""fetch_pdfs.py — 清单驱动的通用下载器（第 ④ 类：直接复用的通用工具）。

与旧模板脚本的区别：**DOI 与清单不写死在代码里**。它只做三件事：
  1) 读清单（DOI 或题目都行；题目用 Crossref 反查 DOI）
  2) 查路由表 routes.yaml，决定"这篇该怎么拿、PDF 地址长什么样"
  3) 默认只输出计划（--dry-run 是默认行为）；要真下必须显式 --execute

三级验证（对应讨论里的 L1/L2/L3）：
  L1  默认：路由干跑，不联网取 PDF，只打印路由与 URL 形态        -> fetch_pdfs.py --list <清单>
  L2  加 --check-url：对已授权域做轻量探测（读 content-type 就断开）-> --check-url
  L3  加 --execute --only <DOI>：真的下载指定篇，并与库内已有文件比对哈希

用法：
  python -X utf8 fetch_pdfs.py --list list.txt --library "D:\我的文献库" --out "01-下载\PDFs"
  python -X utf8 fetch_pdfs.py --list list.txt --check-url
  python -X utf8 fetch_pdfs.py --list list.txt --execute --only 10.1109/JLT.2015.2465382
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

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROUTES = os.path.join(os.path.dirname(HERE), 'routes.yaml')


# ---------------- 路由表 ----------------
def load_routes(path):
    try:
        import yaml
    except ImportError:
        print('需要 PyYAML：pip install pyyaml', file=sys.stderr)
        raise SystemExit(2)
    with io.open(path, encoding='utf-8') as fh:
        return yaml.safe_load(fh)


def pick_route(doi, routes):
    for r in routes.get('routes') or []:
        if doi.startswith(r['prefix']):
            return r
    return dict(routes.get('fallback') or {}, prefix='(fallback)', publisher='(未匹配)')


# ---------------- 清单读取 ----------------
DOI_RE = re.compile(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+', re.I)   # 注意：后缀常为大写，必须不敏感


def read_list(path):
    """支持三种输入：纯 DOI 列表、带题目的编号清单（Crossref 反查）、TSV（doi/title 列）。"""
    raw = io.open(path, encoding='utf-8-sig').read()
    items = []
    for line in raw.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = DOI_RE.search(line)
        if m:
            items.append({'source': line, 'doi': m.group(0).rstrip('.,;)'), 'title': ''})
            continue
        # 形如 "1. Author A. Title here[J]. Journal, 2010."
        t = re.sub(r'^\s*\d+[.、)]\s*', '', line)
        t = re.sub(r'\[[JMRDC]\]\.?', ' ', t)
        t = re.sub(r'\s+', ' ', t).strip()
        if len(t) > 12:
            items.append({'source': line, 'doi': '', 'title': t})
    return items


def title_from_citation(line):
    """从中文式引文里抽出论文标题。

    例：'3. Xiang C, Bowers S M, et al. High-performance silicon photonics using heterogeneous
        integration[J]. *IEEE JSTQE*, 2022, 28(3): 8200315.'
      -> 'High-performance silicon photonics using heterogeneous integration'
    做法：去掉编号、去掉 [J]/[M] 之类标记与刊名年份，取"作者段之后、刊名之前"那一段。
    """
    s = line.strip()
    s = re.sub(r'^\s*\d+[.、)]\s*', '', s)            # 去掉编号
    s = re.sub(r'\[\s*[JMRDCSP]\s*\]', '\x00', s)     # [J] 等标记 -> 分隔符
    if '\x00' in s:
        left = s.split('\x00')[0]
        # 作者段与标题之间通常是 ". "（第一个句点）
        parts = left.split('. ', 1)
        cand = parts[1] if len(parts) > 1 else left
    else:
        # 无 [J] 标记：取第二个句点之后
        parts = re.split(r'\.\s+', s, maxsplit=2)
        cand = parts[1] if len(parts) > 1 else s
    cand = re.sub(r'[*_]', '', cand)
    cand = re.sub(r'\s+', ' ', cand).strip(' .;，,')
    return cand[:220]


def crossref_doi_by_title(title, timeout=15):
    u = 'https://api.crossref.org/works?' + urllib.parse.urlencode(
        {'query.bibliographic': title, 'rows': 3})
    try:
        req = urllib.request.Request(u, headers={'User-Agent': 'zju-lit-fetch/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            items = json.loads(r.read().decode('utf-8', 'replace'))['message']['items']
    except Exception as e:
        return None, f'Crossref 查询失败: {e}'
    def toks(x):
        return {w for w in re.findall(r'[a-z0-9]{3,}', (x or '').lower())}
    A = toks(title)
    best, best_score = None, 0.0
    for it in items:
        t = (it.get('title') or [''])[0]
        B = toks(t)
        if not A or not B:
            continue
        f1 = 2 * len(A & B) / (len(A) + len(B))
        if f1 > best_score:
            best, best_score = it, f1
    # 标准/研究报告/专著的题录常被 Crossref 误配到无关论文（实测 UCIe 规范匹配成了 ACM 论文）。
    # 因此：要求 F1 >= 0.7 且两侧词数相近（长度比 >= 0.7），否则不采用。
    if best:
        A2 = toks(title); B2 = toks((best.get('title') or [''])[0])
        lr = min(len(A2), len(B2)) / max(len(A2), len(B2)) if A2 and B2 else 0
    else:
        lr = 0
    # 额外护栏：查询里最长的那个实词必须出现在候选标题里。
    # 否则 'Heterogeneous silicon photonic integrated circuits' 会被
    # 'Heterogeneous Silicon III-V Photonic Integrated Circuits'（会议论文）抢走。
    key_ok = True
    if best:
        qt = sorted(toks(title), key=len, reverse=True)
        ct = toks((best.get('title') or [''])[0])
        if qt and len(qt[0]) >= 6 and qt[0] not in ct:
            key_ok = False
    if (not best) or best_score < 0.55 or lr < 0.55 or not key_ok:
        cand = (best.get('DOI') if best else '') or ''
        why = '关键实词不在候选标题中' if not key_ok else '相似度不足'
        return None, ('检索不可靠(%s; F1=%.2f 长度比=%.2f)，需人工确认 DOI；最相近候选=%s'
                      % (why, best_score, lr, cand or '无'))
    return best['DOI'], None


# ---------------- 路由决策（L1：不联网取 PDF） ----------------
def plan_one(item, routes):
    rec = dict(item)
    # 专著/标准/报告/中文纸质书：题录检索必然误配，直接标人工，不烧请求
    BOOKISH = ('[M]', '[R]', '[S]', '出版社', 'Press', 'Springer,', 'Wiley,', 'CRC',
               'Roadmap', 'Specification', 'Framework', 'Implementation Agreement', '专著', '规范')
    if not rec['doi']:
        # rec['title'] 可能是原始引文（含作者/刊名）——统一先抽成纯标题再查
        plain = title_from_citation(rec.get('source') or rec.get('title') or '')
        rec['title'] = plain or rec.get('title', '')
        doi, err = crossref_doi_by_title(rec['title'])
        if not doi:
            rec.update(route='(无法确定 DOI)', ok=False, note=err)
            return rec
        rec['doi'] = doi
        rec['doi_from'] = 'crossref 反查'
    else:
        rec['doi_from'] = '清单直给'
    r = pick_route(rec['doi'], routes)
    rec['publisher'] = r.get('publisher', '')
    rec['prefix'] = r.get('prefix', '')
    rec['mode'] = r.get('mode', 'via_landing')
    rec['note'] = r.get('notes', '') or r.get('reason', '')
    tmpl = dict(rec)
    tmpl['doi_suffix'] = rec['doi'].split('/', 1)[1] if '/' in rec['doi'] else rec['doi']
    tmpl['landing_no_fragment'] = (r.get('landing', '') or '').format(**tmpl).split('#')[0]
    rec['landing'] = tmpl['landing_no_fragment']
    ext = r.get('extract') or {}
    # 预期 URL 形态（能直接算出来的就写出来；需要页面提取的标注出来）
    if rec['mode'] == 'refuse':
        rec['ok'] = False
        rec['expected'] = '(跳过：%s)' % r.get('reason', '')
    elif ext.get('pattern_pdf'):
        rec['ok'] = True
        rec['expected'] = ext['pattern_pdf'].format(**tmpl)
        rec['how'] = '按 URL 模板直接取'
    elif ext.get('pdf_url'):
        rec['ok'] = True
        rec['expected'] = '(需先从页面提取参数) ' + ext['pdf_url']
        rec['how'] = '先开 landing，提取参数后取'
    elif ext.get('same_origin_pdf'):
        rec['ok'] = True
        rec['expected'] = '(landing 同源) ' + ext['same_origin_pdf'].format(**tmpl)
        rec['how'] = '在 landing 页同源 fetch'
    elif ext.get('meta_pdf'):
        rec['ok'] = True
        rec['expected'] = '(读页面 meta[%s])' % ext['meta_pdf']
        rec['how'] = '读 meta[citation_pdf_url]'
    else:
        rec['ok'] = True
        rec['expected'] = '(在 landing 页找 PDF/全文链接)'
        rec['how'] = '通用兜底：找 pdfLinks / iframe'
    if r.get('manual_step'):
        rec['note'] = (rec['note'] + ' ｜ 可能需要人工过一次验证').strip(' ｜')
    return rec


# ---------------- L2：轻量 URL 探测 ----------------
def probe_url(url, timeout=12):
    """只读响应头与前若干字节，判断是否像 PDF。不落盘。"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.headers.get('content-type', '')
            head = r.read(8)
            final = r.geturl()
        return {'ct': ct, 'head': head.decode('latin1', 'replace'), 'final': final,
                'is_pdf': head[:4] == b'%PDF'}
    except Exception as e:
        return {'error': '%s: %s' % (type(e).__name__, e)}


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--list', required=True, help='清单文件（DOI 或题目，一行一条）')
    ap.add_argument('--routes', default=DEFAULT_ROUTES, help='路由表 routes.yaml')
    ap.add_argument('--library', default=os.environ.get('ZJU_LIBRARY'),
                    help='文献库根目录（用于查重：库里已有就不再下）')
    ap.add_argument('--out', default=None, help='真实下载时的输出目录')
    ap.add_argument('--json', default=None, help='把计划写成 json')
    ap.add_argument('--md', default=None, help='把计划写成 markdown 表（便于人工审）')
    ap.add_argument('--check-url', action='store_true', help='L2：对可直接算出的 URL 做轻量探测')
    ap.add_argument('--execute', action='store_true', help='L3：真的下载（需配 --out）')
    ap.add_argument('--only', action='append', default=[], help='只处理这些 DOI（可重复）')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    routes = load_routes(args.routes)
    items = read_list(args.list)
    if args.limit:
        items = items[:args.limit]
    print('清单 %d 条；路由表 %s' % (len(items), os.path.basename(args.routes)))
    print('模式：%s' % ('L3 真实下载' if args.execute else
                        ('L2 轻量探测' if args.check_url else 'L1 路由干跑（默认，不取 PDF）')))

    # 库内已有文件的索引（查重用）
    have = {}
    if args.library and os.path.isdir(args.library):
        for dp, dn, fs in os.walk(args.library):
            dn[:] = [d for d in dn if not d.startswith('.')]
            for f in fs:
                if f.lower().endswith('.pdf'):
                    have.setdefault(f, os.path.join(dp, f))
        print('库内已有 %d 个 PDF 可用于查重' % len(have))

    plans = []
    for i, it in enumerate(items, 1):
        rec = plan_one(it, routes)
        if args.only and rec.get('doi') not in args.only:
            continue
        plans.append(rec)
        flag = '✓' if rec.get('ok') else '×'
        print('  [%d] %s %-28s %-22s %s' % (i, flag, (rec.get('doi') or '(无 DOI)')[:28],
                                            rec.get('publisher', '')[:20], rec.get('how', rec.get('note', ''))[:40]))
        if rec.get('expected'):
            print('        -> %s' % rec['expected'][:110])

    if args.check_url:
        print('\n=== L2 轻量探测（只读响应头与 8 字节）===')
        for rec in plans:
            u = rec.get('expected', '')
            if not u.startswith('http'):
                continue
            r = probe_url(u)
            ok = r.get('is_pdf')
            print('  %s %s' % ('✓' if ok else '×', u[:96]))
            print('        %s' % (('PDF ✓ ct=%s' % r['ct']) if ok else
                                  ('非 PDF: ct=%s head=%r %s' % (r.get('ct'), r.get('head'), r.get('error', '')))))

    if args.json:
        io.open(args.json, 'w', encoding='utf-8', newline='\n').write(
            json.dumps(plans, ensure_ascii=False, indent=1))
        print('\n计划 json: %s' % args.json)
    if args.md:
        lines = ['# 下载路由计划（干跑）', '',
                 '| # | DOI | 出版社 | 方式 | 预期地址/要做的动作 | 说明 |', '|---|---|---|---|---|---|']
        for i, r in enumerate(plans, 1):
            lines.append('| %d | `%s` | %s | %s | %s | %s |' % (
                i, r.get('doi') or '-', r.get('publisher', ''), r.get('how', ''),
                (r.get('expected') or '').replace('|', '/')[:90], (r.get('note') or '').replace('|', '/')[:70]))
        io.open(args.md, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
        print('计划 markdown: %s' % args.md)

    if args.execute:
        if not args.out:
            print('--execute 需要 --out <输出目录>', file=sys.stderr)
            return 2
        os.makedirs(args.out, exist_ok=True)
        print('\n=== L3 真实下载（逐篇取字节）===')
        print('  提示：本步骤需要浏览器已授权。当前这个 Python 版本只演示"计划到落盘"的接口，')
        print('        真正取字节走 Node 侧（scripts/browser_pdf_downloader.mjs），两者共用同一份计划 json。')
        print('        计划已写好，可用：')
        print('          node "%s" --url "<plan 里的 expected>" --out "%s\\<文件名>.pdf"' % (
            os.path.join(HERE, 'browser_pdf_downloader.mjs'), args.out))
    else:
        print('\n（默认干跑：未取任何字节。要真下加 --execute --out <目录>，或先用 --check-url 探测）')
    bad = sum(1 for r in plans if not r.get('ok'))
    print('\n合计 %d 条；不可得 %d 条（已在路由表标 refuse 或无法确定 DOI）' % (len(plans), bad))
    return 0


if __name__ == '__main__':
    sys.exit(main())
