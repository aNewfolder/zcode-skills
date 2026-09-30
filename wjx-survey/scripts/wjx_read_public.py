# -*- coding: utf-8 -*-
"""抓取问卷星公开填写页的问卷内容（题目/选项/元数据）。

用法:
    python wjx_read_public.py <填写链接或 /vm/ 短链接> [--out result.json] [--md]

    python wjx_read_public.py https://tp.wjx.com/vm/Q0Q3h38.aspx
    python wjx_read_public.py https://www.wjx.cn/vm/xxxxx.aspx --md

只能拿到创建者公开展示的内容（题目、选项、说明、状态）。
答卷明细属于创建者私有数据，本脚本不获取、也无法获取（除非创建者开启了公开统计，
此时页面会自带统计展示，用 --md 直接看渲染结果即可）。
"""
import argparse
import html as htmllib
import json
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 问卷星题型代码 → 名称（div.field 的 type 属性）
TYPE_MAP = {
    "1": "填空", "2": "单选(圆角单选)", "3": "单选", "4": "多选",
    "5": "简答", "6": "矩阵单选", "7": "矩阵多选", "8": "量表",
    "9": "矩阵填空", "10": "矩阵量表", "11": "排序", "12": "下拉",
    "13": "评分", "14": "日期", "15": "时间", "16": "文件上传",
    "17": "地区", "18": "地址", "20": "图片单选", "21": "图片多选",
    "24": "滑条", "26": "矩阵评分", "30": "多项填空", "31": "图片",
    "32": "分页", "33": "说明文字", "34": "手机", "35": "姓名",
    "37": "性别", "38": "身份证", "42": "邮箱",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return htmllib.unescape(s).strip()


def parse(html: str) -> dict:
    out = {"title": "", "questions": [], "state": "ok"}

    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if m:
        out["title"] = strip_tags(m.group(1))

    # 问卷说明（描述/欢迎语）
    m = re.search(r'<div[^>]*class=["\']survey-detail(?:-intro)?["\'][^>]*>(.*?)</div>', html, re.S) \
        or re.search(r'<div[^>]*id=["\']divQuestionIntro["\'][^>]*>(.*?)</div>', html, re.S) \
        or re.search(r'<div[^>]*class=["\']field-label["\'][^>]*>\s*<div[^>]*class=["\']topichtml["\'][^>]*>(.*?)</div>', html, re.S)
    if m:
        out["description"] = strip_tags(m.group(1))[:500]

    # 常见异常状态识别
    for kw, state in [
        ("已停止", "stopped"), ("已暂停", "paused"), ("已结束", "ended"),
        ("问卷已关闭", "closed"), ("不允许提交", "closed"),
        ("请输入密码", "password"), ("需要登录", "login_required"),
        ("微信登录后", "login_required"), ("已达到答卷份数上限", "quota_full"),
    ]:
        if kw in html:
            out["state"] = state
            out["state_hint"] = kw
            break

    # 逐题解析：div.field 内 topic=题号 type=题型 req=是否必填
    fields = re.findall(
        r"<div[^>]*class=['\"]field ui-field-contain['\"][^>]*>"
        r".*?</div>\s*<div class=['\"]errorMessage['\"]>", html, re.S)
    if not fields:  # 兜底：按 field 起始切分
        starts = [m.start() for m in re.finditer(
            r"<div[^>]*class=['\"]field ui-field-contain['\"]", html)]
        starts.append(len(html))
        fields = [html[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

    for f in fields:
        q = {"options": []}
        mt = re.search(r"topic=['\"](\d+)['\"]", f)
        if not mt:
            continue
        q["index"] = int(mt.group(1))
        mq = re.search(r"type=['\"](\d+)['\"]", f)
        q["type_code"] = mq.group(1) if mq else ""
        q["type"] = TYPE_MAP.get(q["type_code"], f"type{q['type_code']}")
        q["required"] = "req='1'" in f or 'req="1"' in f
        mtip = re.search(r"【(.+?)】", f)
        if mtip:
            q["type"] = mtip.group(1)

        mtitle = re.search(r"class=['\"]topichtml['\"][^>]*>(.*?)</div>", f, re.S)
        if mtitle:
            title = strip_tags(mtitle.group(1))
            title = re.sub(r"^【.+?】\s*", "", title)
            title = re.sub(r"\s*【.+?】$", "", title)
            q["title"] = title.strip()

        # 选项：label div 的文本；图片选项取 img src
        for mlab in re.finditer(
                r"<div[^>]*class=['\"]label['\"][^>]*>(.*?)</div>", f, re.S):
            txt = strip_tags(mlab.group(1))
            if txt:
                q["options"].append(txt)
        for mimg in re.finditer(r"<img[^>]*src=['\"](//[^'\"]+)['\"]", f):
            if "wjx.cn/img" not in mimg.group(1) and "/img/" not in mimg.group(1):
                q.setdefault("option_images", []).append("https:" + mimg.group(1))

        # 矩阵题的行
        rows = re.findall(r"<td[^>]*class=['\"]tr_[^'\"]*['\"][^>]*>(.*?)</td>", f, re.S)
        if rows:
            q["rows"] = [strip_tags(r) for r in rows]

        if q.get("title") or q["options"]:
            out["questions"].append(q)

    out["question_count"] = len(out["questions"])
    return out


def to_md(data: dict) -> str:
    lines = [f"# {data['title']}", ""]
    if data.get("description"):
        lines += [data["description"], ""]
    if data["state"] != "ok":
        lines += [f"> ⚠️ 页面状态：{data.get('state_hint', data['state'])}", ""]
    for q in data["questions"]:
        req = "（必填）" if q["required"] else ""
        lines.append(f"{q['index']}. {q['title']}  [{q['type']}]{req}")
        for i, opt in enumerate(q["options"], 1):
            lines.append(f"   - {chr(64 + i) if q['options'] and i <= 26 else i}. {opt}")
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", help="JSON 输出路径")
    ap.add_argument("--md", action="store_true", help="输出 Markdown")
    args = ap.parse_args()

    url = args.url
    if url.startswith("wjx:"):  # 支持纯 sid 简写 wjx:Q0Q3h38
        sid = url[4:]
        url = f"https://www.wjx.cn/vm/{sid}.aspx"

    html = fetch(url)
    data = parse(html)
    data["source_url"] = url

    if args.md:
        md = to_md(data)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(md)
            print(f"saved → {args.out}")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(md)
    else:
        js = json.dumps(data, ensure_ascii=False, indent=1)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(js)
            print(f"saved → {args.out}")
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(js)


if __name__ == "__main__":
    main()
