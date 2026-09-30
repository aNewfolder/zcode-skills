#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CC98 抓取应用的命令行入口（供 AI 调用，包装本地 app 的 HTTP 接口）。

用法:
  python cc98.py search <关键词> [--pages N] [--mode spaced|raw] [--json]
  python cc98.py search <词1> <词2> ...        # 多关键词合并搜索（去重）
  python cc98.py crawl <帖子ID> [更多ID...] [--label 名称] [--wait] [--timeout 秒]
  python cc98.py live [--refresh] [--limit N]  # 实时动态（热帖+关注版面）
  python cc98.py read <帖子ID> [--pages N]     # 阅读帖子正文（真实请求）
  python cc98.py net-status                    # 网络接入状态（校网/WebVPN/代理）
  python cc98.py feed [--refresh] [--limit N] [--json]   # 今日推荐（兴趣推送）
  python cc98.py prefs                                   # 查看推送偏好
  python cc98.py prefs add keyword <词> [--weight 1-5]    # 加兴趣关键词
  python cc98.py prefs add board <版面名> [--weight 1-5]  # 加关注版面
  python cc98.py prefs remove keyword|board <词或版面名>
  python cc98.py prefs hot on|off                         # 全站热帖开关
  python cc98.py history <关键词> [--limit N]             # 本地已抓取内容全文搜索（零联网）
  python cc98.py login-status | status | outputs

零第三方依赖（仅标准库）。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"
# 应用部署目录：通过环境变量 CC98_APP_DIR 指定；未设置时给出通用提示。
APP_DIR = os.environ.get("CC98_APP_DIR") or "CC98 应用目录"

NOT_RUNNING = (
    "CC98 应用未启动。请先启动：\n"
    f"  cd \"{APP_DIR}\" && python app.py\n"
    "（后台运行，然后重试本命令；可用环境变量 CC98_APP_DIR 指定应用目录）"
)


def call(method: str, path: str, data=None, timeout: int = 60):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:
            msg = str(e)
        print(f"[HTTP {e.code}] {msg}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError:
        print(NOT_RUNNING, file=sys.stderr)
        sys.exit(2)


def cmd_search(args):
    keywords = args.keywords or [args.keyword]
    keywords = [k for k in keywords if k and k.strip()][:5]
    if not keywords:
        print("关键词为空", file=sys.stderr)
        sys.exit(1)
    body = {"keywords": keywords} if len(keywords) > 1 else {"keyword": keywords[0]}
    body.update({"mode": args.mode, "max_pages": args.pages})
    r = call("POST", "/api/search", body, timeout=180)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    used = "、".join(r.get("keywords_used") or [r["keyword_used"]])
    print(f"实际关键词「{used}」，去重后共 {r['total']} 条：")
    for t in r["topics"]:
        matched = f"  ←{'+'.join(t['matched'])}" if len((t.get('matched') or [])) > 1 else ""
        print(f"  {t['id']}  {t['title']}  [{t.get('boardName') or '?'}]  "
              f"回复:{t.get('replyCount')} 点击:{t.get('hitCount')}{matched}")
    print("\n把想要的帖子 ID 传给 crawl 子命令，例如：")
    print(f"  python {sys.argv[0]} crawl {' '.join(str(t['id']) for t in r['topics'][:3])} --wait")


def cmd_crawl(args):
    ids = args.topic_ids
    if len(ids) > 20:
        print("一次最多抓 20 帖（防封号限制），请分批。", file=sys.stderr)
        sys.exit(1)
    r = call("POST", "/api/crawl", {"topic_ids": ids, "label": args.label})
    print(f"任务已启动：{r['count']} 个帖子。")
    if not args.wait:
        print("用 status 子命令查看进度（或加 --wait 让本命令等到结束）。")
        return
    printed = 0
    deadline = time.time() + args.timeout
    while True:
        time.sleep(1.5)
        st = call("GET", "/api/crawl/status", timeout=20)
        for line in st["logs"][printed:]:
            print("  " + line)
        printed = len(st["logs"])
        if not st["running"]:
            break
        if time.time() > deadline:
            print(f"等待超时（{args.timeout}s），任务仍在后台进行，请稍后用 status 查看。", file=sys.stderr)
            sys.exit(3)
    print()
    if st["error"]:
        print(f"⚠️ 任务异常停止：{st['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ 完成：成功 {st['topics_ok']} 帖，下载 {st['files_ok']} 个文件")
    print(f"输出目录：{st['result_dir']}")
    print("正文在各子目录 content.md，附件在其 files/ 下，清单见 index.md。")


def cmd_feed(args):
    if args.refresh:
        call("GET", "/api/feed?refresh=1", timeout=30)
        if not args.json:
            print("已触发刷新（一次约 10 个只读请求，需 30~60 秒），等待完成…")
        deadline = time.time() + args.timeout
        while True:
            time.sleep(4)
            r = call("GET", "/api/feed", timeout=30)
            if not r.get("refreshing"):
                break
            if time.time() > deadline:
                print("刷新超时，稍后再用 feed 查看。", file=sys.stderr)
                sys.exit(3)
    else:
        r = call("GET", "/api/feed", timeout=30)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    items = r.get("items") or []
    ts = (r.get("fetched_at") or "").replace("T", " ")[:16]
    if r.get("refreshing"):
        print("正在生成推荐（首次约 30~60 秒），稍后再运行一次本命令即可。")
        return
    print(f"共 {len(items)} 条推荐（数据更新于 {ts}）：")
    for i, it in enumerate(items[: args.limit], 1):
        reasons = " ".join(f"[{x['label']}]" for x in it.get("reasons", [])[:3])
        print(f"  {i}. {it['id']}  {it['title']}")
        print(f"     [{it.get('boardName') or '?'}] {it.get('userName', '')} "
              f"回复:{it.get('replyCount')} {it.get('time_str', '')} {reasons}")
    for n in r.get("notes") or []:
        print(f"  · {n}")
    print("\n抓取示例： python cc98.py crawl <ID...> --wait --label 今日推荐")
    print("调整偏好： python cc98.py prefs add keyword 词 / prefs add board 版面名 / prefs")


def _prefs_modify(args):
    prefs = call("GET", "/api/prefs", timeout=20)
    action, what, value = args.action, args.what, (args.value or "").strip()
    if action in ("add", "remove"):
        if what == "keyword":
            lst = prefs["keywords"]
            hit = next((k for k in lst if k["kw"].lower() == value.lower()), None)
            if action == "add":
                if hit:
                    print(f"关键词「{value}」已存在（权重 {hit['weight']}）")
                    return
                lst.append({"kw": value, "weight": max(1, min(5, args.weight))})
            else:
                prefs["keywords"] = [k for k in lst if k["kw"].lower() != value.lower()]
        elif what == "board":
            lst = prefs["boards"]
            hit = next((b for b in lst if b["name"].lower() == value.lower()), None)
            if action == "add":
                if hit:
                    print(f"版面「{value}」已存在（权重 {hit['weight']}）")
                    return
                lst.append({"name": value, "weight": max(1, min(5, args.weight)), "id": None})
            else:
                prefs["boards"] = [b for b in lst if b["name"].lower() != value.lower()]
        else:
            print("add/remove 需要指明类型：keyword 或 board", file=sys.stderr)
            sys.exit(1)
    elif action in ("on", "off"):
        if what != "hot":
            print("on/off 只支持 hot（全站热帖开关）", file=sys.stderr)
            sys.exit(1)
        flag = action == "on"
        prefs["hot_daily"] = flag
        prefs["hot_week"] = flag
    else:
        cmd_prefs_show()
        return
    r = call("POST", "/api/prefs", prefs, timeout=20)
    print(f"✅ 偏好已保存，下次刷新推荐生效。当前关键词 {len(r['prefs']['keywords'])} 个、"
          f"版面 {len(r['prefs']['boards'])} 个、热帖={'开' if r['prefs']['hot_daily'] or r['prefs']['hot_week'] else '关'}")
    print("立即生效可运行： python cc98.py feed --refresh")


def cmd_prefs_show():
    prefs = call("GET", "/api/prefs", timeout=20)
    print("兴趣关键词：")
    for k in prefs["keywords"]:
        print(f"  - {k['kw']}（权重 {k['weight']}）")
    print("关注版面：")
    for b in prefs["boards"]:
        print(f"  - {b['name']}（权重 {b['weight']}）")
    print(f"全站热帖：每日{'开' if prefs['hot_daily'] else '关'} / 每周{'开' if prefs['hot_week'] else '关'}")
    print(f"缓存 {prefs['feed_refresh_minutes']} 分钟 · 最多 {prefs['feed_max_items']} 条 · "
          f"看过 {prefs['seen_hide_days']} 天内不重推")


def cmd_history(args):
    r = call("GET", "/api/library/search?q=" + urllib.parse.quote(args.query)
             + f"&limit={args.limit}", timeout=60)
    rows = r.get("results") or []
    if not rows:
        print(f"本地已抓取内容中没有找到「{args.query}」。")
        return
    print(f"在本地已抓取内容中找到 {len(rows)} 处「{args.query}」：")
    for x in rows:
        print(f"  [{x['task']}] {x['topic_id']} {x['title']}")
        print(f"    …{x['snippet'][:110]}…")
        print(f"    {x['rel_path']}")


def cmd_status(_args):
    st = call("GET", "/api/crawl/status")
    print(json.dumps({k: v for k, v in st.items() if k != "logs" or v}, ensure_ascii=False, indent=1))


def cmd_outputs(_args):
    r = call("GET", "/api/outputs")
    if not r["outputs"]:
        print("还没有抓取记录。")
        return
    for o in r["outputs"]:
        print(f"  {o['name']}  {o.get('topics', 0)} 帖  {o['files']} 个文件  {o['path']}")


def cmd_login_status(_args):
    r = call("GET", "/api/login/status")
    if r.get("logged_in"):
        print(f"已登录：{r.get('user')}")
    else:
        print(f"未登录：{r.get('error') or r.get('hint') or ''}")
        print("请让用户在网页 http://127.0.0.1:8765 上点「登录 / 换账号」手动登录。", file=sys.stderr)
        sys.exit(1)


def cmd_net_status(_args):
    r = call("GET", "/api/net/status")
    label = {"direct": "校网直连", "webvpn": "WebVPN", "proxy": "本地代理", "none": "不可用"}.get(
        r.get("mode"), r.get("mode"))
    print(f"接入方式：{label}（设置：{r.get('mode_setting')}）")
    print(f"校网直连：{'可用' if r.get('direct_ok') else '不可达'}")
    wv = r.get("webvpn") or {}
    print(f"WebVPN：{'已登录 ' + str(wv.get('username')) if wv.get('logged_in') else '未登录/过期'}")
    print(f"本地代理：{'已配置 ' + r['proxy_url'] if r.get('proxy_set') else '未配置'}")
    print(f"CC98 账号：{'已登录 ' + str(r.get('cc98_logged_in') and '（token 有效）') if r.get('cc98_logged_in') else '未登录'}")
    if r.get("blocked"):
        print("\n⚠️ 当前无法进入校网。校外解决方案（二选一）：")
        print("  1. 网页 http://127.0.0.1:8765 → 设置 → 网络接入 → 登录 WebVPN（浙大上网账号）")
        print("  2. 或运行 zju-connect / RVPN 后，把本地代理地址（如 socks5://127.0.0.1:2345）填到同一页面")


def cmd_live(args):
    if args.refresh:
        r = call("POST", "/api/live/refresh", {}, timeout=300)
        print(f"一轮更新完成：新帖 {r.get('new', 0)} 条，回复更新 {r.get('updated', 0)} 条（每轮固定 ≤5 个请求）")
    r = call("GET", f"/api/live?limit={args.limit}")
    st = r.get("status") or {}
    bits = []
    if st.get("last_round_hhmmss"):
        bits.append("上轮 " + st["last_round_hhmmss"])
    if st.get("next_round_hhmmss") and st.get("enabled"):
        bits.append("下轮约 " + st["next_round_hhmmss"])
    print(f"实时动态（共 {st.get('total', 0)} 条）" + ("，".join(bits) and f" · {'，'.join(bits)}"))
    if st.get("status") not in ("ok", "init") and st.get("note"):
        print(f"⚠️ {st['note']}")
    for it in r.get("items") or []:
        mark = "🆕" if it.get("is_new") else "  "
        src = ",".join(it.get("sources") or [])
        print(f"  {mark} {it['id']}  {it.get('title')}  [{it.get('boardName') or '?'}]  "
              f"回复:{it.get('replyCount')}  {src}")
    print("\n阅读某帖（会发真实请求拉正文）：")
    print(f"  python {sys.argv[0]} read <帖子ID>")


def cmd_read(args):
    r = call("GET", f"/api/topic/{args.topic_id}/read?pages={args.pages}", timeout=120)
    print(f"# {r.get('title')}")
    meta = [r.get("board"), r.get("author"), f"回复 {r.get('replyCount')}"]
    print("· " + " · ".join(str(m) for m in meta if m))
    print(f"· 原帖：https://www.cc98.org/topic/{args.topic_id}\n")
    for f in r.get("floors") or []:
        body = (f.get("body") or "（无内容）").strip()
        if args.max_len and len(body) > args.max_len:
            body = body[:args.max_len] + " …（截断，--max-len 0 显示全文）"
        print(f"—— {f.get('floor')}F {f.get('user')} {f.get('time')}")
        print(body + "\n")
    if r.get("more"):
        print(f"（还有更多楼层：--pages 加大，如 --pages {args.pages + 2}）")


def main():
    p = argparse.ArgumentParser(description="CC98 本地抓取工具 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="搜索帖子标题（可多个关键词合并搜索）")
    s.add_argument("keyword", nargs="?", default="", help="单个关键词")
    s.add_argument("keywords", nargs="*", help="更多关键词（合并搜索并去重，最多5个）")
    s.add_argument("--pages", type=int, default=2, help="每个关键词搜索页数（每页20条，默认2）")
    s.add_argument("--mode", choices=["spaced", "raw"], default="spaced")
    s.add_argument("--json", action="store_true", help="输出原始 JSON")
    s.set_defaults(func=cmd_search)

    c = sub.add_parser("crawl", help="抓取指定帖子的正文与附件")
    c.add_argument("topic_ids", nargs="+", type=int)
    c.add_argument("--label", default="", help="输出文件夹名")
    c.add_argument("--wait", action="store_true", help="等待任务结束")
    c.add_argument("--timeout", type=int, default=1800, help="--wait 的最长等待秒数")
    c.set_defaults(func=cmd_crawl)

    f = sub.add_parser("feed", help="今日推荐（按你的兴趣推送帖子）")
    f.add_argument("--refresh", action="store_true", help="强制刷新候选（一次约10个只读请求）")
    f.add_argument("--limit", type=int, default=20)
    f.add_argument("--json", action="store_true")
    f.add_argument("--timeout", type=int, default=240, help="--refresh 的最长等待秒数")
    f.set_defaults(func=cmd_feed)

    pf = sub.add_parser("prefs", help="查看/修改推送偏好")
    pf.add_argument("action", nargs="?", default="show",
                    choices=["show", "add", "remove", "on", "off"])
    pf.add_argument("what", nargs="?", default=None, choices=[None, "keyword", "board", "hot"])
    pf.add_argument("value", nargs="?", default="")
    pf.add_argument("--weight", type=int, default=3, help="add 时的权重 1~5")
    pf.set_defaults(func=_prefs_modify)

    h = sub.add_parser("history", help="本地已抓取内容全文搜索（零联网）")
    h.add_argument("query")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=cmd_history)

    sub.add_parser("login-status", help="检查登录状态").set_defaults(func=cmd_login_status)
    sub.add_parser("status", help="当前抓取任务状态").set_defaults(func=cmd_status)
    sub.add_parser("outputs", help="列出历史输出").set_defaults(func=cmd_outputs)

    ns = sub.add_parser("net-status", help="网络接入状态（校网直连/WebVPN/代理）")
    ns.set_defaults(func=cmd_net_status)

    lv = sub.add_parser("live", help="实时动态（热帖+关注版面，读缓存不联网）")
    lv.add_argument("--refresh", action="store_true", help="先跑一轮真实更新（≤5个请求）")
    lv.add_argument("--limit", type=int, default=25)
    lv.set_defaults(func=cmd_live)

    rd = sub.add_parser("read", help="页内阅读：拉取帖子正文（真实请求，已限速）")
    rd.add_argument("topic_id", type=int)
    rd.add_argument("--pages", type=int, default=2, help="拉几页楼层（每页20楼，默认2）")
    rd.add_argument("--max-len", type=int, default=600, help="每楼正文截断长度（0=不截断）")
    rd.set_defaults(func=cmd_read)

    args = p.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args.func(args)


if __name__ == "__main__":
    main()
