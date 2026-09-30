#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
超星学习通(Chaoxing)课程课件下载器

通过课程页面 URL + Cookie 调用超星 Web API，爬取章节列表与知识卡片中的
文件型课件（pptx/docx/pdf 等）并下载。

用法示例:
  # 列出课程的全部章节和文件（不下载）
  python chaoxing_dl.py list "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/stu?courseid=...&clazzid=...&cpi=...&enc=..."

  # 下载标题匹配"绪论"的章节中的全部文件课件，直接放入输出目录
  python chaoxing_dl.py download "<课程URL>" --chapter 绪论 --out "<输出目录>" --flat

  # 用账号密码登录并保存 Cookie（之后无需再登录）
  python chaoxing_dl.py login 155xxxxxxx "password"

认证方式（二选一）:
  1. Cookie 文件（默认脚本目录下 cookies.json，键值对 JSON 或浏览器复制的
     "k=v; k2=v2" 字符串），可用 login 子命令生成，或从已登录浏览器
     F12 -> Application -> Cookies 复制 UID/_d/fid 等字段
  2. login 子命令账号密码登录

依赖: requests（登录模式额外需要 pycryptodome）
"""

import argparse
import base64
import html as htmllib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_COOKIE_FILE = SCRIPT_DIR / "cookies.json"

MAX_CARDS_PER_CHAPTER = 15   # 每个章节最多探测的知识卡片数
CHUNK_SIZE = 1 << 16


# ---------------------------------------------------------------- session

def load_cookie_pairs(text: str) -> dict:
    """Cookie 来源兼容三种格式: JSON 对象 / 'k=v; k2=v2' 字符串 / 文件内容"""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("{"):
        return dict(json.loads(text))
    pairs = {}
    for part in text.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            pairs[k.strip()] = v.strip()
    return pairs


def build_session(cookies: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    s.cookies.update(cookies)
    return s


# ---------------------------------------------------------------- login

def aes_encrypt_b64(plaintext: str, key: bytes = b"u2oh6Vu^HWe4_AES") -> str:
    """学习通登录加密（与官方 login.js encryptByAES 一致）:
    AES-128-CBC, key=iv, PKCS7, base64"""
    from Crypto.Cipher import AES  # pycryptodome
    data = plaintext.encode("utf-8")
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    cipher = AES.new(key, AES.MODE_CBC, key)
    return base64.b64encode(cipher.encrypt(data)).decode()


def api_login(account: str, password: str) -> dict:
    """账号密码登录，成功返回 cookie 字典（含 HttpOnly 的 vc3 等关键字段）"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    # 登录页拿表单默认值（fid 等）
    page = s.get("https://passport2.chaoxing.com/login?refer=https%3A%2F%2Fi.chaoxing.com", timeout=30)
    def form_value(name, default):
        m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', page.text)
        return m.group(1) if m else default

    r = s.post(
        "https://passport2.chaoxing.com/fanyalogin",
        data={
            "fid": form_value("fid", "-1"),
            "uname": aes_encrypt_b64(account),
            "password": aes_encrypt_b64(password),
            "refer": "https://i.chaoxing.com",
            "t": "true",
            "forbidotherlogin": form_value("forbidotherlogin", "0"),
            "validate": "",
            "doubleFactorLogin": "0",
            "independentId": "0",
        },
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://passport2.chaoxing.com/login",
            "Origin": "https://passport2.chaoxing.com",
        },
        timeout=30)
    j = r.json()
    if not j.get("status"):
        raise SystemExit(f"[登录失败] {j.get('message') or j}")
    cookies = {c.name: c.value for c in s.cookies}
    if "UID" not in cookies or "_d" not in cookies:
        raise SystemExit(f"[登录异常] 响应 Cookie 缺少 UID/_d: {sorted(cookies)}")
    return cookies


# ---------------------------------------------------------------- url 解析

def parse_course_url(url: str) -> dict:
    """从课程页/学习页 URL 提取 courseid/clazzid/cpi/enc(/chapterId)"""
    q = parse_qs(urlparse(url).query)
    # requests 的 parse_qs 键区分大小写，超星两种风格都有
    def get(*names):
        for n in names:
            if n in q:
                return q[n][0]
        return None

    info = {
        "courseid": get("courseid", "courseId"),
        "clazzid": get("clazzid", "clazzId"),
        "cpi": get("cpi"),
        "enc": get("enc", "stuenc"),
        "chapterid": get("chapterId", "chapterid", "knowledgeid"),
    }
    missing = [k for k in ("courseid", "clazzid", "cpi") if not info[k]]
    if missing:
        raise SystemExit(f"[URL 缺少参数] {missing}，请复制完整的课程页面地址")
    return info


# ---------------------------------------------------------------- 章节列表

CH_ITEM_RE = re.compile(r'<div class="chapter_item"[^>]*>', re.S)
ATTR_ID_RE = re.compile(r'id="cur(\d+)"')
ATTR_TITLE_RE = re.compile(r'title="([^"]*)"')
ONCLICK_RE = re.compile(r"toOld\('(\d+)'\s*,\s*'(\d+)'")
SBAR_RE = re.compile(r'class="catalog_sbar"[^>]*>\s*([^<]*?)\s*<')


def get_chapters(sess: requests.Session, p: dict) -> list:
    """抓取 studentcourse 页并解析章节树, 返回 [{id, title, no}]"""
    t = str(int(time.time() * 1000))
    url = (f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
           f"?courseid={p['courseid']}&clazzid={p['clazzid']}&cpi={p['cpi']}"
           f"&ut=s&t={t}&stuenc={p.get('enc') or ''}&v=0&pageHeader=1&hideHead=0")
    r = sess.get(url, timeout=30)
    r.raise_for_status()
    text = htmllib.unescape(r.text)

    chapters = []
    for m in CH_ITEM_RE.finditer(text):
        tag = m.group(0)
        idm = ATTR_ID_RE.search(tag)
        if not idm:
            continue
        cid = idm.group(1)
        tm = ATTR_TITLE_RE.search(tag)
        title = htmllib.unescape(tm.group(1)).strip() if tm else ""
        # 章节编号（如 2.1），在 item 起始标签之后不远处
        window = text[m.end():m.end() + 1200]
        sm = SBAR_RE.search(window)
        no = sm.group(1).strip() if sm else ""
        chapters.append({"id": cid, "title": title, "no": no})
    if not chapters:
        raise SystemExit("[解析失败] studentcourse 页面未找到章节，Cookie 可能已过期"
                         f"（HTTP {r.status_code}，页面长度 {len(r.text)}）。"
                         "请重新登录: python chaoxing_dl.py login <账号> <密码>")
    return chapters


# ---------------------------------------------------------------- 知识卡片

MARG_ANCHOR = "mArg ="


def extract_balanced_json(text: str, start: int) -> str | None:
    """从 text[start:] 提取一段配平花括号的 JSON（跳过字符串字面量）"""
    i = start
    n = len(text)
    depth = 0
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def parse_card_attachments(card_html: str) -> list:
    """从知识卡片 HTML 解析 mArg JSON 中的附件列表。
    页面里可能先出现 `mArg = "";` 空声明，需逐个尝试 `mArg = {` 锚点。"""
    out = []
    for m in re.finditer(r"mArg\s*=\s*\{", card_html):
        js = extract_balanced_json(card_html, m.end() - 1)
        if not js:
            continue
        try:
            arg = json.loads(js)
        except json.JSONDecodeError:
            continue
        atts = arg.get("attachments")
        if isinstance(atts, list):
            out = atts
            break
    return out


def get_chapter_files(sess: requests.Session, p: dict, chapter_id: str) -> list:
    """遍历章节的每张知识卡片，汇总文件型附件（按 objectid 去重）"""
    files, seen = [], set()
    for num in range(MAX_CARDS_PER_CHAPTER):
        url = (f"https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
               f"?clazzid={p['clazzid']}&courseid={p['courseid']}"
               f"&knowledgeid={chapter_id}&num={num}&ut=s&cpi={p['cpi']}&mooc2=1")
        r = sess.get(url, timeout=30)
        r.raise_for_status()
        atts = parse_card_attachments(r.text)
        if num > 0 and not atts:
            break  # 后续卡片不存在
        for att in atts:
            prop = att.get("property") or {}
            oid = prop.get("objectid")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            files.append({
                "objectid": oid,
                "name": prop.get("name") or oid,
                "type": prop.get("type") or "",
                "size": int(prop.get("size") or 0),
                "attach_type": att.get("type") or "",   # document / video / ...
                "module": prop.get("module") or "",
            })
        if not atts:
            break
    return files


# ---------------------------------------------------------------- 下载

CARDS_REFERER = "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"


def get_download_info(sess: requests.Session, objectid: str) -> dict:
    url = f"https://mooc1.chaoxing.com/ananas/status/{objectid}?k=&flag=4"
    # 该接口校验 Referer 防盗链，必须带上
    r = sess.get(url, timeout=30, headers={"Referer": CARDS_REFERER})
    r.raise_for_status()
    return r.json()


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip()
    return name or "unnamed"


def download_file(sess: requests.Session, url: str, dest: Path) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_pct = -10
    with sess.get(url, stream=True, timeout=120, headers={"Referer": CARDS_REFERER}) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(CHUNK_SIZE):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct >= last_pct + 10:  # 按 10% 步进打印，避免刷屏
                        last_pct = pct
                        print(f"      {pct:3d}%  {done}/{total}")
    sys.stdout.write("\n")
    tmp.rename(dest)
    return dest


# ---------------------------------------------------------------- 命令

def select_chapters(chapters: list, keyword: str | None) -> list:
    if not keyword:
        return chapters
    kw = keyword.lower()
    picked = [c for c in chapters
              if kw in c["title"].lower() or kw == c["no"].lower() or kw == c["id"]]
    if not picked:
        raise SystemExit(f"[未匹配] 没有章节匹配关键词: {keyword}")
    return picked


def filter_files(files: list, kind: str) -> list:
    if kind == "all":
        return files
    want = {"document": {"document", "insertdoc"},
            "video": {"video"}}[kind]
    return [f for f in files
            if f["attach_type"] in want or f["module"] in want
            or (kind == "document" and f["type"].lower() in
                (".pptx", ".ppt", ".docx", ".doc", ".pdf", ".xlsx", ".xls",
                 ".zip", ".rar", ".txt", ".mp4"))]


def chapter_dir_name(c: dict) -> str:
    prefix = f"{c['no']} " if c.get("no") else ""
    return sanitize_filename(f"{prefix}{c['title']}")


def cmd_list(sess, p, args):
    chapters = select_chapters(get_chapters(sess, p), args.chapter)
    print(f"共 {len(chapters)} 个章节:\n")
    for c in chapters:
        print(f"  [{c['no'] or '-':>5}] {c['title']}  (chapterId={c['id']})")
        if args.files:
            for f in filter_files(get_chapter_files(sess, p, c["id"]), args.type):
                print(f"        - {f['name']}  ({f['size']/1e6:.2f} MB, {f['attach_type']})")


def cmd_download(sess, p, args):
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    chapters = select_chapters(get_chapters(sess, p), args.chapter)
    downloaded = skipped = 0
    for c in chapters:
        cname = chapter_dir_name(c)
        print(f"\n== 章节 {cname} (chapterId={c['id']})")
        files = filter_files(get_chapter_files(sess, p, c["id"]), args.type)
        if not files:
            print("   (无文件课件)")
            continue
        target_dir = out_root if args.flat else out_root / cname
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            info = get_download_info(sess, f["objectid"])
            name = sanitize_filename(info.get("filename") or f["name"])
            dest = target_dir / name
            if dest.exists() and dest.stat().st_size == (info.get("size") or f["size"]):
                print(f"   [跳过] {name} 已存在")
                skipped += 1
                continue
            dl = info.get("download")
            if not dl:
                print(f"   [失败] {name}: 无下载直链（可能无权限）")
                continue
            print(f"   [下载] {name}")
            download_file(sess, dl, dest)
            print(f"   [完成] {dest}")
            downloaded += 1
        time.sleep(0.3)
    print(f"\n完成: 新下载 {downloaded} 个, 已存在跳过 {skipped} 个 -> {out_root}")


def cmd_login(args):
    cookies = api_login(args.account, args.password)
    need = ("UID", "_d")
    if not all(k in cookies for k in need):
        raise SystemExit(f"[异常] 登录响应缺少关键字段: {cookies.keys()}")
    DEFAULT_COOKIE_FILE.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
    print(f"[登录成功] Cookie 已保存到 {DEFAULT_COOKIE_FILE}")


def main():
    ap = argparse.ArgumentParser(description="超星学习通课程课件下载器")
    ap.add_argument("--cookie", help="Cookie 字符串或 JSON（缺省读脚本目录 cookies.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_login = sub.add_parser("login", help="账号密码登录并保存 Cookie")
    ap_login.add_argument("account")
    ap_login.add_argument("password")

    for name, help_ in (("list", "列出章节与文件"), ("download", "下载文件课件")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("url", help="课程页 URL（stu 页或 studentstudy 页）")
        s.add_argument("--chapter", help="章节关键词/编号/chapterId，匹配第一个..全部匹配项")
        s.add_argument("--type", default="document", choices=["document", "video", "all"])
        if name == "list":
            s.add_argument("--files", action="store_true", help="同时列出每章的文件")
        else:
            s.add_argument("--out", default=".", help="输出目录")
            s.add_argument("--flat", action="store_true", help="文件直接放输出目录（不建章节子目录）")

    args = ap.parse_args()

    if args.cmd == "login":
        cmd_login(args)
        return

    raw = args.cookie or (DEFAULT_COOKIE_FILE.read_text(encoding="utf-8")
                          if DEFAULT_COOKIE_FILE.exists() else "")
    if not raw:
        raise SystemExit(f"[缺少认证] 未提供 Cookie 且 {DEFAULT_COOKIE_FILE} 不存在。\n"
                         "先执行: python chaoxing_dl.py login <账号> <密码>\n"
                         "或从浏览器复制 Cookie 后 --cookie 'UID=...; _d=...; fid=...'")
    sess = build_session(load_cookie_pairs(raw))
    p = parse_course_url(args.url)
    if args.cmd == "list":
        cmd_list(sess, p, args)
    else:
        cmd_download(sess, p, args)


if __name__ == "__main__":
    main()
