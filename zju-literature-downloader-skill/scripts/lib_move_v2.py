#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lib_move_v2.py — 直接读 Excel 的归位执行器（实时状态表 + 历史归档）。

相对旧版 lib_move.py 的改动：
  1) 直接读 .xlsx（人工在 Excel 里填的选择列），也兼容 .tsv；
  2) 选择列支持：单填数字(1/2/3)、多选(1;3 或 1；3)、其他目录(手填完整路径)；
  3) 生成**实时状态表** 03-move/推送状态-最新.xlsx —— 每篇一行，含状态/时刻/目标/说明；
  4) 执行日志按时间戳单独归档到 archive/logs/push-<时间戳>.tsv，"回去翻记录"时用；
  5) 安全：目标必须已存在（除非 --allow-create 且由人工指定）；同名冲突比 SHA256，
     相同跳过、不同加 __dup2；默认移动（剪切走），--copy 则保留源。
"""
import argparse
import csv
import hashlib
import io
import os
import shutil
import sys
import time

# 项目根：默认取本脚本上一级目录；可用 --project 或环境变量 ZJU_PROJECT 覆盖
def _default_project():
    return os.environ.get('ZJU_PROJECT') or os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
P = _default_project()
# 规则 A：只有人工明确授权，AI 才创建目录。授权可以写在备注列，或由命令行开关给出。
AUTHORIZE_KW = ('请ai新建', '请ai建', '请新建', 'ai新建', 'ai建', '新建该目录', '新建这个目录',
                '新建目录', '可以新建', '允许新建', '帮我建', '建一个', '请创建', 'ai创建')


def is_authorized(note):
    """备注里是否明确要求 AI 新建目录。"""
    t = (note or '').replace(' ', '').lower()
    return any(k in t for k in AUTHORIZE_KW)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for c in iter(lambda: fh.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def read_table(path):
    """返回 (表头list, 行list[dict])；支持 .xlsx 与 .tsv。"""
    if path.lower().endswith(('.xlsx', '.xlsm')):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb[wb.sheetnames[0]]
        head = [str(ws.cell(1, c).value).strip() if ws.cell(1, c).value is not None else ''
                for c in range(1, ws.max_column + 1)]
        rows = []
        for r in range(2, ws.max_row + 1):
            d = {}
            for c, h in enumerate(head, 1):
                if not h:
                    continue
                v = ws.cell(r, c).value
                d[h] = '' if v is None else str(v).strip()
            if any(d.values()):
                rows.append(d)
        return head, rows
    raw = io.open(path, encoding='utf-8-sig').read().rstrip('\n').split('\n')
    head = raw[0].split('\t')
    rows = []
    for l in raw[1:]:
        if not l.strip():
            continue
        c = l.split('\t')
        c += [''] * (len(head) - len(c))
        rows.append(dict(zip(head, c)))
    return head, rows


def pick(row, *names):
    for n in names:
        if n in row and row[n]:
            return row[n].strip()
    return ''


def resolve_targets(row, top=3):
    """从一行里解析出目标目录列表（支持多个）。"""
    out = []
    manual = pick(row, '【你填】其他目录', 'manual_dir', '其他目录')
    if manual:
        out.append(manual.strip().strip('\\/'))
    multi = pick(row, '【你填】多选(如1;3)', '【你填】多选', 'multi', 'also_copy_to')
    single = pick(row, '【你填】选第几个(1/2/3)', '【你填】选择', 'choose', 'accept')
    idx = []
    for chunk in (multi or single).replace('；', ';').replace(',', ';').replace('，', ';').split(';'):
        chunk = chunk.strip()
        if chunk.isdigit() and 1 <= int(chunk) <= top:
            idx.append(int(chunk))
    cand_cols = ['建议1', '建议2(备选)', '建议3(备选)'] if '建议1' in row else \
                ['cand1_path', 'cand2_path', 'cand3_path']
    for i in idx:
        v = row.get(cand_cols[i - 1], '')
        if v:
            out.append(v.strip().strip('\\/'))
    seen, res = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return res


def rescan_library(library, dirs_tsv, quiet=False):
    """动作前重爬库目录，更新 库目录清单.tsv，并报告新增/消失。"""
    old = set()
    keep = {}      # 保留旧的 mb / sample 列，避免重爬把这些信息抹掉
    if os.path.exists(dirs_tsv):
        for l in io.open(dirs_tsv, encoding='utf-8').read().rstrip('\n').split('\n')[1:]:
            if not l.strip():
                continue
            c = l.split('\t')
            old.add(c[0])
            if len(c) >= 6:
                keep[c[0]] = (c[4], c[5])
    rows, pdfs = [], 0
    for dirpath, dirnames, filenames in os.walk(library):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        rel = os.path.relpath(dirpath, library)
        if rel == '.':
            continue
        npdf = sum(1 for f in filenames if f.lower().endswith('.pdf'))
        rows.append((rel, npdf))
        pdfs += npdf
    new = {r[0] for r in rows}
    added, gone = sorted(new - old), sorted(old - new)
    rows.sort()
    os.makedirs(os.path.dirname(dirs_tsv), exist_ok=True)
    with io.open(dirs_tsv, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('rel\tdepth\tn_pdf\tn_files\tmb\tsample\n')
        for rel, npdf in rows:
            d = rel.count(os.sep) + 1
            mb, sample = keep.get(rel, ('', ''))
            fh.write('%s\t%d\t%d\t%d\t%s\t%s\n' % (rel, d, npdf, 0, mb, sample))
    if not quiet:
        print('  [重爬] 目录 %d 个 / PDF %d 篇（更新时间 %s）' % (len(rows), pdfs, time.strftime('%H:%M:%S')))
        if added:
            print('  [新增目录] %d 个:' % len(added))
            for a in added[:10]:
                print('      + ' + a)
        if gone:
            print('  [已消失] %d 个:' % len(gone))
            for g in gone[:10]:
                print('      - ' + g)
        if not added and not gone:
            print('  [无变化] 与上次清单一致')
    return added, gone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--excel', default=os.path.join(P, '03-move', '归档建议.xlsx'))
    ap.add_argument('--sheet-hint', default='建议5篇', help='只用说明；读表取第一个 sheet')
    ap.add_argument('--library', default=os.environ.get('ZJU_LIBRARY'),
                    help='文献库根目录（必填，或设环境变量 ZJU_LIBRARY）；每个人的目录树不同，故无默认值')
    ap.add_argument('--source-dir', default=os.path.join(P, 'PDFs'))
    ap.add_argument('--copy', action='store_true', help='保留源文件（默认移动=剪切走）')
    ap.add_argument('--execute', action='store_true', help='真正执行；不加则只预览')
    ap.add_argument('--allow-create', action='store_true', help='允许创建人工指定的目标目录')
    ap.add_argument('--verify', action='store_true', default=True)
    ap.add_argument('--status-xlsx', default=os.path.join(P, '03-move', '推送状态-最新.xlsx'))
    ap.add_argument('--no-rescan', action='store_true',
                    help='跳过动作前的目录重爬（仅在明确知道库未变化时使用）')
    args = ap.parse_args()

    if not args.library:
        print('缺少 --library（文献库根目录）。每个人的目录树不同，需要你指定，例如：', file=sys.stderr)
        print('  python lib_move_v2.py --library "D:\\我的文献库" --excel "<建议表.xlsx>"', file=sys.stderr)
        print('  或设置环境变量 ZJU_LIBRARY', file=sys.stderr)
        return 2
    args.library = os.path.abspath(args.library)
    if not os.path.isdir(args.library):
        print('文献库根目录不存在: %s' % args.library, file=sys.stderr)
        return 2

    # 规则 B：动作前重爬目录（默认开启；--no-rescan 可跳过）
    lib_map = os.path.join(P, 'lib-map')
    dirs_tsv = None
    for n in ('库目录清单.tsv', 'dirs.tsv'):
        if os.path.exists(os.path.join(lib_map, n)):
            dirs_tsv = os.path.join(lib_map, n); break
    if dirs_tsv is None:
        dirs_tsv = os.path.join(lib_map, '库目录清单.tsv')
    if not getattr(args, 'no_rescan', False):
        print('=== 重爬库目录（避免漏掉人工新建的路径）===')
        try:
            rescan_library(args.library, dirs_tsv)
        except OSError as e:
            print('  [重爬失败] %s（继续用旧清单）' % e)
    print()

    head, rows = read_table(args.excel)
    print('读入 %s（%d 行）' % (os.path.basename(args.excel), len(rows)))

    # 组装任务
    tasks = []
    for r in rows:
        fname = pick(r, '文件名', 'file', 'old_name') or os.path.basename(pick(r, 'path'))
        if not fname:
            continue
        targets = resolve_targets(r)
        note = pick(r, '【你填】备注', 'note')
        tasks.append({'file': fname, 'row': r, 'targets': targets, 'note': note})
    plan = [t for t in tasks if t['targets']]
    print('有明确去向: %d 篇；未填去向: %d 篇' % (len(plan), len(tasks) - len(plan)))

    # 预检：源、目标
    print('\n=== 预检 ===')
    problems = 0
    for t in plan:
        src = os.path.join(args.source_dir, t['file'])
        if not os.path.exists(src):
            print('  [源缺失] %s' % t['file'][:60]); problems += 1
        for d in t['targets']:
            full = os.path.join(args.library, d)
            if not os.path.isdir(full):
                if args.allow_create or is_authorized(t['note']):
                    why = '命令行 --allow-create' if args.allow_create else '备注已授权'
                    print('  [将创建·%s] %s' % (why, d))
                else:
                    print('  [目标不存在] %s' % d); problems += 1
    print('  预检问题: %d' % problems)
    if problems and not args.allow_create:
        # 规则：文件夹由人创建，AI 不建。要 AI 代建必须人工显式加 --allow-create。
        print('')
        print('  拒绝执行：有目标目录不存在。')
        print('  【规则】目录由人工创建；若确需 AI 代建某个分支，请显式加 --allow-create，')
        print('          并在对话里说明要建哪个路径（AI 不会自行发明路径或层级）。')
        return 2

    # 收集历史日志目录
    logdir = os.path.join(P, 'archive', 'logs')
    os.makedirs(logdir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')

    results = []
    for i, t in enumerate(plan, 1):
        src = os.path.join(args.source_dir, t['file'])
        for d in t['targets']:
            full = os.path.join(args.library, d)
            rec = {'file': t['file'], 'target': d, 'status': '', 'note': t['note'], 'at': ''}
            if not os.path.exists(src):
                rec['status'] = '源缺失'
                results.append(rec); continue
            if not os.path.isdir(full):
                if not (args.allow_create or is_authorized(t['note'])):
                    rec['status'] = '目标不存在·未授权'
                    results.append(rec); continue
                if args.execute:
                    os.makedirs(full, exist_ok=True)
                    rec['note'] = (rec['note'] + '｜已创建目标目录').strip('｜')
                else:
                    rec['status'] = '待创建目录'
                    results.append(rec); continue
            dst = os.path.join(full, t['file'])
            if os.path.exists(dst):
                try:
                    same = sha256(src) == sha256(dst)
                except OSError:
                    same = False
                if same:
                    rec['status'] = '已存在·跳过'
                    results.append(rec)
                    if not args.copy and args.execute:
                        try:
                            os.remove(src)
                            rec['status'] = '已存在·删源'
                        except OSError:
                            pass
                    continue
                base, ext = os.path.splitext(t['file'])
                n = 2
                while os.path.exists(os.path.join(full, '%s__dup%d%s' % (base, n, ext))):
                    n += 1
                dst = os.path.join(full, '%s__dup%d%s' % (base, n, ext))
                rec['note'] = (rec['note'] + '｜同名不同内容，改名落库').strip('｜')
            if not args.execute:
                rec['status'] = '待执行'
                results.append(rec); continue
            try:
                # 多目标时：除最后一个目标外都用复制，只有最后一个才 move，
                # 否则第一个目标就把源搬走，后续目标会报"源缺失"（这是修过的 bug）。
                multi = len(t['targets']) > 1
                last_one = (d == t['targets'][-1])
                if args.copy or (multi and not last_one):
                    shutil.copy2(src, dst)
                else:
                    shutil.move(src, dst)
                rec['status'] = '已推送(复制)' if args.copy else '已推送(移动)'
                rec['at'] = time.strftime('%H:%M:%S')
                if args.verify:
                    rec['sha16'] = sha256(dst)[:16]
            except OSError as e:
                rec['status'] = '失败'
                rec['note'] = (rec['note'] + '｜' + str(e)[:60]).strip('｜')
            results.append(rec)
        print('  [%d/%d] %s -> %s' % (i, len(plan), t['file'][:44], ', '.join(t['targets'])))

    # ---- 实时状态表（xlsx，覆盖写）----
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '推送状态'
    ws.append(['#', '文件', '去向目录', '状态', '完成时刻', 'SHA16', '备注'])
    fill = PatternFill('solid', fgColor='E2EFDA')
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = fill
    for i, r in enumerate(results, 1):
        ws.append([i, r['file'], r['target'], r['status'], r.get('at', ''), r.get('sha16', ''), r.get('note', '')])
    for col, w in zip('ABCDEFG', [5, 46, 46, 16, 11, 18, 44]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical='top', wrap_text=(c.column in (2, 3, 7)))
    for _ in range(8):
        try:
            wb.save(args.status_xlsx)
            break
        except PermissionError:
            time.sleep(3)

    # ---- 历史日志（按时间戳单独放）----
    hist = os.path.join(logdir, 'push-%s.tsv' % stamp)
    with io.open(hist, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('time\tfile\ttarget\tstatus\tsha16\tnote\n')
        for r in results:
            fh.write('%s\t%s\t%s\t%s\t%s\t%s\n' % (time.strftime('%Y-%m-%dT%H:%M:%S'), r['file'],
                                                   r['target'], r['status'], r.get('sha16', ''), r.get('note', '')))
    # 同时追加到总日志，便于 grep
    total = os.path.join(logdir, 'push-总日志.tsv')
    if not os.path.exists(total):
        io.open(total, 'w', encoding='utf-8', newline='\n').write('batch\ttime\tfile\ttarget\tstatus\tsha16\tnote\n')
    with io.open(total, 'a', encoding='utf-8', newline='\n') as fh:
        for r in results:
            fh.write('%s\t%s\t%s\t%s\t%s\t%s\t%s\n' % (stamp, time.strftime('%Y-%m-%dT%H:%M:%S'), r['file'],
                                                       r['target'], r['status'], r.get('sha16', ''), r.get('note', '')))

    from collections import Counter
    print('\n=== 统计 ===')
    for k, v in Counter(r['status'] for r in results).most_common():
        print('  %-16s %d' % (k, v))
    print('  实时状态表: %s' % args.status_xlsx)
    print('  本次历史日志: %s' % hist)
    print('  累计总日志: %s' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
