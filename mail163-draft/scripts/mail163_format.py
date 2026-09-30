#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""163 邮箱公文草稿渲染模块。

把结构化信件 JSON 渲染成符合标准公文/书信格式的 HTML，供浏览器端
存草稿流程使用（mail163-draft skill）。

排版规格（内置，勿改）：
- 中文：仿宋（FangSong）；英文和数字：Times New Roman —— 由字体栈
  "Times New Roman" 在前实现自动混排（浏览器对中文字符自动回退到仿宋）
- 字号：统一小四（12pt）；行距：统一 1.5 倍
- 书信缩进：称呼顶格 → 正文每段首行缩进 2 字符 → "此致"缩进 2 字符、
  "敬礼！"顶格 → 署名、日期右对齐

用法：
    python mail163_format.py letter.json        # 输出 HTML 到 stdout
    python mail163_format.py letter.json -o out.html
"""

import argparse
import json
import re
import sys
from html import escape as html_escape
from pathlib import Path

FONT_STACK = ("'Times New Roman', 'FangSong', '仿宋', 'FangSong_GB2312', "
              "'仿宋_GB2312', 'SimSun', serif")
SIZE_XIAOSI = "12pt"      # 小四
LINE_HEIGHT = "1.5"

REQUIRED_HINT = ("letter JSON 需要字段：to（收件人地址或数组）、subject、"
                 "salutation（称呼）、paragraphs（正文段落数组）、"
                 "closing（默认 true，自动加此致敬礼）、signature、date")


def default_cn_date() -> str:
    from datetime import date
    d = date.today()
    return f"{d.year}年{d.month}月{d.day}日"


def _p(text: str, indent: str = "", align: str = "") -> str:
    style = "margin:0;"
    if indent:
        style += f" text-indent:{indent};"
    if align:
        style += f" text-align:{align};"
    return f'<p style="{style}">{text}</p>'


def normalize_addrs(value) -> list:
    """收件人字段统一成地址数组：字符串按逗号/分号/中英文逗号分号切分。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = re.split(r"[;,；，]", str(value))
    out = []
    for a in items:
        a = a.strip()
        if a and "@" in a and a not in out:
            out.append(a)
    return out


def render_letter(spec: dict) -> str:
    """标准书信/公文格式 HTML（与 zjuem-mail-draft 同规格）。"""
    parts = []
    if spec.get("salutation"):
        parts.append(_p(html_escape(str(spec["salutation"]))))
    for para in spec.get("paragraphs") or []:
        if str(para).strip():
            parts.append(_p(html_escape(str(para)), indent="2em"))
    if spec.get("closing", True):
        parts.append(_p("此致", indent="2em"))
        parts.append(_p("敬礼！"))
    if spec.get("signature"):
        parts.append(_p(html_escape(str(spec["signature"])), align="right"))
    if spec.get("date"):
        parts.append(_p(html_escape(str(spec["date"])), align="right"))
    inner = "".join(parts)
    return (f'<div style="font-family:{FONT_STACK}; font-size:{SIZE_XIAOSI}; '
            f'line-height:{LINE_HEIGHT};">{inner}</div>')


def validate_spec(spec: dict, allow_empty_to=False) -> tuple:
    """返回 (to列表, subject)。防误存：无收件人/无主题默认拒绝。"""
    to = normalize_addrs(spec.get("to"))
    subject = str(spec.get("subject") or "").strip()
    if not to and not allow_empty_to:
        raise SystemExit("[safety] 缺少收件人（to）。确要无收件人请加 --allow-empty-to")
    if not subject:
        raise SystemExit("[safety] 缺少主题（subject）。为防误存，无主题草稿一律拒绝")
    return to, subject


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="163 公文草稿 HTML 渲染器")
    ap.add_argument("letter", help="信件 JSON 文件")
    ap.add_argument("-o", "--output", help="输出 HTML 文件（缺省打印到 stdout）")
    ap.add_argument("--pretty", action="store_true", help="带换行便于人工检查")
    args = ap.parse_args()

    spec = json.loads(Path(args.letter).read_text("utf-8"))
    html = render_letter(spec)
    if args.pretty:
        html = html.replace("<p ", "\n  <p ").replace("</div>", "\n</div>")
    if args.output:
        Path(args.output).write_text(html, "utf-8")
        print(f"[format] HTML 已写入 {args.output}", file=sys.stderr)
    else:
        print(html)


if __name__ == "__main__":
    main()
