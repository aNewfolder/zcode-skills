# -*- coding: utf-8 -*-
"""公众号最新推送读取 + 日报生成（公众平台 Cookie 路线，唯一通用方法）。

前提：用个人微信号免费注册一个订阅号（mp.weixin.qq.com），登录后台后：
    1. 地址栏 URL 里复制 token=xxxx；
    2. F12 → Network → 刷新 → 第一条请求 → Request Headers 里复制整串 Cookie；
    3. 存成 cookie.json：{"token": "xxx", "cookie": "..."}

用法：
    python fetch_latest.py --mp cookie.json 账号1 [账号2 ...] [-o 目录] [--limit N]
    账号可以是公众号名称，或 biz:xxxx / fakeid:xxxx（跳过搜索直接取列表）。

    --links-file txt        直接给链接清单（每行一条 mp.weixin.qq.com 链接）
    --limit N               每个账号取最新 N 篇（默认 5，宁小勿大）
    --since YYYY-MM-DD      只处理该日期（含）之后的文章
    --no-export             只生成日报清单，不抓全文
    --no-digest             不生成日报索引
    --no-dedupe             不去重（默认已导出过的自动跳过，状态存 .wechat_articles_state.json）

限流红线（封号风险，务必遵守）：
    - 接口限频严格：脚本已内置每页 3~6 秒、每篇 2~4 秒间隔，不要改小、不要并发；
    - ret=200013 表示触发频率限制：当天停手，隔天再试；
    - 新注册账号权重低，列表接口可能一开始就 200013：先在后台网页里
      「图文 → 超链接 → 查找文章」手动搜一次目标账号并点开（暖号），之后通常就通了；
    - 每天每账号建议不超过 1~2 次调用，一次 --limit 不超过 5。

输出：每篇文章一个文件夹（详见 fetch_wechat_article.py）+ YYYY-MM-DD_公众号日报.md 索引。
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'fwa', os.path.join(HERE, 'fetch_wechat_article.py'))
fwa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fwa)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
MP_BASE = 'https://mp.weixin.qq.com'

MP_RET_HINT = {
    0: 'ok',
    -1: '系统错误',
    -6: 'token 或 Cookie 已失效（重新登录 mp.weixin.qq.com 更新 cookie.json）',
    200002: '参数错误（fakeid 可能不对）',
    200012: '需要登录',
    200013: '触发频率限制：今天别再试了（新账号先去后台「超链接」手动搜一次暖号）',
    200003: '登录态无效（Cookie 与 token 不匹配或已过期，重新登录后更新 cookie.json）',
}


class Skip(Exception):
    """该源本轮跳过（限流/登录失效等），不视为致命错误。"""


def http_get(url, headers, timeout=40):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='ignore')


# ================================================================ 公众平台接口

def mp_headers(cookie):
    return {
        'User-Agent': UA,
        'Cookie': cookie,
        'Referer': MP_BASE + '/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&type=77&lang=zh_CN',
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
    }


def mp_load_cookie(path):
    with open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    token = str(data.get('token') or '').strip()
    cookie = (data.get('cookie') or '').strip()
    if not token or not cookie:
        raise Skip('cookie.json 缺少 token 或 cookie 字段')
    return token, cookie


def mp_resolve_fakeid(token, cookie, name):
    q = urllib.parse.quote(name)
    url = (MP_BASE + '/cgi-bin/searchbiz?action=search_biz&begin=0&count=5&query=%s'
           '&token=%s&lang=zh_CN&f=json&ajax=1&random=%f' % (q, token, random.random()))
    raw = http_get(url, mp_headers(cookie))
    try:
        j = json.loads(raw)
    except ValueError:
        raise Skip('searchbiz 返回非 JSON（多为登录失效）：%.80s' % raw)
    ret = j.get('base_resp', {}).get('ret')
    if ret != 0:
        raise Skip('searchbiz ret=%s：%s' % (ret, MP_RET_HINT.get(ret, '未知错误')))
    lst = j.get('list') or []
    if not lst:
        raise Skip('搜不到公众号「%s」' % name)
    for it in lst:
        if it.get('nickname') == name:
            return it['fakeid'], it.get('nickname', name)
    return lst[0]['fakeid'], lst[0].get('nickname', name)


def mp_list_articles(token, cookie, source, limit):
    """source: 公众号名称 / biz:xxx / fakeid:xxx。返回 (显示名, [条目])。

    接口每次最多 5 篇；limit<=5 时只发一次列表请求。
    """
    if re.match(r'^(biz|fakeid):', source):
        fakeid, nick = source.split(':', 1)[1], source.split(':', 1)[1]
    else:
        fakeid, nick = mp_resolve_fakeid(token, cookie, source)
        time.sleep(random.uniform(3.0, 6.0))          # searchbiz 与 appmsg 之间拉开间隔
    items = []
    begin = 0
    while len(items) < limit:
        url = (MP_BASE + '/cgi-bin/appmsg?action=list_ex&begin=%d&count=5&fakeid=%s'
               '&type=9&query=&token=%s&lang=zh_CN&f=json&ajax=1&random=%f'
               % (begin, urllib.parse.quote(fakeid), token, random.random()))
        raw = http_get(url, mp_headers(cookie))
        try:
            j = json.loads(raw)
        except ValueError:
            raise Skip('appmsg 返回非 JSON（多为登录失效）：%.80s' % raw)
        ret = j.get('base_resp', {}).get('ret')
        if ret != 0:
            raise Skip('appmsg ret=%s：%s' % (ret, MP_RET_HINT.get(ret, '未知错误')))
        page = j.get('app_msg_list') or []
        if not page:
            break
        for it in page:
            t = it.get('create_time')
            items.append({
                'title': it.get('title', ''),
                'link': it.get('link', ''),
                'time': datetime.datetime.fromtimestamp(t) if t else None,
                'digest': it.get('digest', ''),
            })
        begin += 5
        if len(page) < 5:
            break
        time.sleep(random.uniform(3.0, 6.0))
    return nick, items[:limit]


# ================================================================ 日报索引 & 导出

def load_state(out_root):
    p = os.path.join(out_root, '.wechat_articles_state.json')
    if os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f), p
        except Exception:
            pass
    return {}, p


def save_state(path, state):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def yq(s):
    return json.dumps(str(s), ensure_ascii=False)


def write_digest(out_root, digest_rows, date_str):
    name = '%s_公众号日报.md' % date_str
    lines = ['---',
             'title: %s' % yq('%s 公众号日报' % date_str),
             'date: %s' % yq(date_str),
             '---', '', '# 公众号日报 %s' % date_str, '']
    for label, rows in digest_rows:
        lines.append('## %s' % label)
        lines.append('')
        if not rows:
            lines.append('（本源没有符合条件的更新）')
            lines.append('')
            continue
        for it in rows:
            t = it['time'].strftime('%Y-%m-%d %H:%M') if it.get('time') else ''
            flags = {True: '✅ 已导出', False: '⚠️ 未导出', 'external': '↩️ 非微信链接'}
            mark = flags.get(it.get('exported'), '')
            lines.append('- [%s](%s)　%s %s' % (it['title'] or '(无标题)', it['link'], t, mark))
        lines.append('')
    path = os.path.join(out_root, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path


# ================================================================ 主流程

def main():
    ap = argparse.ArgumentParser(
        prog='fetch_latest.py',
        description='读取公众号最新推送生成日报并全文导出（公众平台 Cookie 路线）')
    ap.add_argument('--mp', nargs=2, action='append', default=[],
                    metavar=('COOKIE_JSON', '账号'),
                    help='cookie.json + 公众号名称/biz:xxx（可一次多个账号：重复 --mp）')
    ap.add_argument('--links-file', action='append', default=[], help='链接清单 txt（每行一条）')
    ap.add_argument('--limit', type=int, default=5, help='每个账号取最新 N 篇（默认5，建议≤5）')
    ap.add_argument('--since', default='', help='只处理 YYYY-MM-DD 及之后的文章')
    ap.add_argument('-o', '--output', default='.', help='输出根目录')
    ap.add_argument('--no-export', action='store_true', help='只生成清单，不抓全文')
    ap.add_argument('--no-digest', action='store_true', help='不生成日报索引')
    ap.add_argument('--no-dedupe', action='store_true', help='不去重，已导出的也重抓')
    ap.add_argument('--no-source-html', action='store_true')
    ap.add_argument('--text-only', action='store_true')
    opts = ap.parse_args()

    if not (opts.mp or opts.links_file):
        ap.print_help()
        return 1
    os.makedirs(opts.output, exist_ok=True)
    state, state_path = load_state(opts.output)
    since = datetime.date.fromisoformat(opts.since) if opts.since else None

    # ---- 收集各账号的文章列表
    sources, digest_rows, rc = [], [], 0
    for cookie_path, account in opts.mp:
        try:
            token, cookie = mp_load_cookie(cookie_path)
            nick, items = mp_list_articles(token, cookie, account, opts.limit)
        except Skip as e:
            print('[SKIP] mp %s：%s' % (account, e))
            rc = max(rc, 2)
            digest_rows.append(('公众平台: ' + account, []))
            continue
        sources.append(items)
        digest_rows.append(('公众平台: ' + (nick or account), items))
    for lf in opts.links_file:
        with open(lf, encoding='utf-8-sig') as f:
            items = [{'title': '', 'link': l.strip(), 'time': None, 'digest': ''}
                     for l in f if l.strip().startswith('http')]
        sources.append(items[:opts.limit])
        digest_rows.append(('链接清单: ' + lf, items[:opts.limit]))

    flat, seen = [], set()
    for items in sources:
        for it in items:
            if it['link'] not in seen:
                seen.add(it['link'])
                if since and it.get('time') and it['time'].date() < since:
                    continue
                flat.append(it)

    new_items = [it for it in flat
                 if opts.no_dedupe or it['link'] not in state]

    # ---- 日报索引
    if not opts.no_digest:
        for _, rows in digest_rows:
            for it in rows:
                if 'exported' not in it:
                    it['exported'] = bool(it['link'] in state) and not opts.no_export
        dp = write_digest(opts.output, digest_rows, datetime.date.today().isoformat())
        print('[DIGEST] %s' % os.path.abspath(dp))

    # ---- 全文导出
    export_opts = argparse.Namespace(text_only=opts.text_only, no_source_html=opts.no_source_html)
    ok = fail = 0
    todo = [] if opts.no_export else new_items
    if opts.no_export:
        print('[INFO] --no-export：仅生成清单，不抓取全文')
    for n, it in enumerate(todo, 1):
        if 'mp.weixin.qq.com' not in it['link']:
            print('[SKIP] 非微信链接，仅列入日报：%s' % it['link'][:70])
            it['exported'] = 'external'
            continue
        print('[导出 %d/%d] %s' % (n, len(todo), (it.get('title') or it['link'])[:50]))
        try:
            fwa.export(it['link'], opts.output, export_opts)
            state[it['link']] = True
            it['exported'] = True
            if not it.get('title') and fwa.LAST_RESULT.get('title'):
                it['title'] = fwa.LAST_RESULT['title']
            ok += 1
        except fwa.Captcha as e:
            print('[RISK] %s —— %s' % (it['link'], e))
            it['exported'] = False
            rc = max(rc, 2)
            fail += 1
        except Exception as e:
            print('[FAIL] %r' % e)
            it['exported'] = False
            fail += 1
        if not opts.no_digest:
            write_digest(opts.output, digest_rows, datetime.date.today().isoformat())
        time.sleep(random.uniform(2.0, 4.0))
    if new_items:
        save_state(state_path, state)

    print('[SUMMARY] 新文章 %d 篇：成功导出 %d，失败 %d；状态文件 %s'
          % (len(new_items), ok, fail, state_path))
    return rc


if __name__ == '__main__':
    sys.exit(main())
