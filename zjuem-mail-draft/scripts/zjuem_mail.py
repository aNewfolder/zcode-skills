#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浙江大学邮箱 (zjuem.zju.edu.cn / mail.zju.edu.cn, Coremail) 草稿箱命令行客户端。

功能：统一认证 SSO 登录 -> 生成公文格式排版 -> 存入草稿箱。
【安全红线】本工具只有"存草稿"能力，绝不实现发送（代码中不存在任何
send 动作；compose 保存请求的 action 写死为 "save"）。

安全纪律（改代码时不得破坏）：
1. 全局限速：任意两次 HTTP 请求间隔 >= RATE_LIMIT 秒；单实例锁防并发。
2. 登录失败锁定：1 小时内"凭据类"失败 LOGIN_MAX_FAILURES 次即拒绝再试；
   只有表单提交被 CAS 拒绝才计数，验证码/网络/页面异常不计数。
3. 统一认证要求图形验证码（getKaptchaStatus=true）时立即中止，绝不硬闯。
4. 密码仅支持 ASCII（统一认证加密算法本身对非 ASCII 有损）。
5. 会话持久化复用，失效才重登；写草稿失败不自动重试，如实报告。

API 依据（2026-09-21 对 mail.zju.edu.cn 实测抓包核对）：
1. 登录链：GET /coremail/cmcu_addon/sso.jsp -> CAS OAuth2.0 authorize ->
   /cas/login（execution）-> getPubKey RSA 加密 -> POST 表单 ->
   callbackAuthorize?ticket -> sso.jsp?code -> Coremail 会话
   （mail.zju.edu.cn 域 Cookie `Coremail.sid`，其值即 sid）。
2. 写信会话：POST /coremail/s/json?sid=X&func=mbox:compose
   body {"id":"<毫秒时间戳>","attrs":{"attachments":[]},"returnInfo":["attachments"]}
   -> S_OK（创建 compose 会话，id 由客户端生成的时间戳）。
3. 保存草稿：POST /coremail/common/mbox/compose.jsp?isUserConfirmed=true&sid=X
   body {"id":"<同上 id>","attrs":{account,to,cc,bcc,subject,isHtml,content,
   attachments,saveSentCopy,...},"returnInfo":true,"encryptPassword":"","action":"save"}
   -> S_OK，var 回显保存后的完整属性；正文 HTML 原样入库。
4. 草稿箱列表：POST /coremail/s/json?sid=X&func=mbox:listMessages
   body {"start":0,"limit":20,"order":"receivedDate","desc":true,
   "returnTotal":true,"fid":2,"mboxa":""}（fid=2 即草稿箱）。
"""

import argparse
import json
import re
import struct
import sys
import time
from html import escape as html_escape, unescape
from pathlib import Path

import requests

MAIL = "https://mail.zju.edu.cn"
# sso.jsp 在 zjuem 域也能用，但会跳到 mail 域建会话；直接用 zjuem 域入口更贴近浏览器行为
SSO_ENTRY = "https://zjuem.zju.edu.cn/coremail/cmcu_addon/sso.jsp"
CAS = "https://zjuam.zju.edu.cn/cas"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
SESSION_PATH = SCRIPT_DIR / "session.json"
STATE_PATH = SCRIPT_DIR / "state.json"
LOCK_PATH = SCRIPT_DIR / ".lock"

RATE_LIMIT = 2.5          # 相邻两次 HTTP 请求最小间隔（秒）
LOGIN_MAX_FAILURES = 2    # 1 小时内最大"凭据类"登录失败次数
LOGIN_LOCK_WINDOW = 3600
LOCK_STALE_SECONDS = 60
TIMEOUT = 30

DRAFT_FID = 2             # Coremail 系统文件夹：1收件箱 2草稿箱 3已发送

FONT_STACK = ("'Times New Roman', 'FangSong', '仿宋', 'FangSong_GB2312', "
              "'仿宋_GB2312', 'SimSun', serif")
SIZE_XIAOSI = "12pt"      # 小四
LINE_HEIGHT = "1.5"

_last_request_time = 0.0


# ---------------------------------------------------------- 基础设施

def rate_sleep():
    global _last_request_time
    wait = RATE_LIMIT - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def acquire_lock():
    import os
    if LOCK_PATH.exists():
        try:
            if time.time() - LOCK_PATH.stat().st_mtime > LOCK_STALE_SECONDS:
                LOCK_PATH.unlink()
            else:
                sys.exit("[safety] 已有另一个 zjuem_mail.py 实例在运行（串行是硬约束）")
        except OSError:
            pass
    LOCK_PATH.write_text(str(os.getpid()), "utf-8")


def release_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------- 登录加密（与统一认证浏览器原版一致）

def _bi_high_index(n: int) -> int:
    idx = -1
    while n:
        n >>= 16
        idx += 1
    return max(idx, 0)


def rsa_encrypt(password: str, modulus_hex: str, exponent_hex: str) -> str:
    """密码按 UTF-16 码元反转，ohdave RSA 每 2 码元一个 16-bit 小端数字
    分块，pow(block, e, n) 后 hex 定宽空格连接（已与浏览器原版逐位验证）。"""
    if any(ord(c) > 0xFF for c in password):
        raise ValueError("密码含非 ASCII 字符：统一认证加密算法对此有损，无法登录")
    n, e = int(modulus_hex, 16), int(exponent_hex, 16)
    b = password.encode("utf-16-le")
    cu = list(struct.unpack("<%dH" % (len(b) // 2), b))
    cu.reverse()
    chunk = 2 * _bi_high_index(n)
    cu += [0] * ((-len(cu)) % chunk)
    out = []
    for i in range(0, len(cu), chunk):
        block = 0
        for j in range(chunk // 2):
            block |= (cu[i + 2 * j] | (cu[i + 2 * j + 1] << 8)) << (16 * j)
        c = pow(block, e, n)
        out.append("".join("%04x" % ((c >> (16 * k)) & 0xFFFF)
                           for k in range(_bi_high_index(c), -1, -1)))
    return " ".join(out)


# ---------------------------------------------------------- 登录流程

class LoginAuthError(RuntimeError):
    """凭据类失败：表单已提交且被 CAS 拒绝，消耗失败额度。"""


class LoginAborted(RuntimeError):
    """中止类失败：未真正提交凭据，不消耗失败额度。"""


META_REFRESH = re.compile(
    r'<meta[^>]*http-equiv\s*=\s*["\']?refresh["\']?[^>]*'
    r'url\s*=\s*["\']?([^"\'>;\s]+)', re.I)


def record_failure(reason: str):
    state = load_json(STATE_PATH, {})
    failures = [f for f in state.get("login_failures", [])
                if time.time() - f["t"] < LOGIN_LOCK_WINDOW]
    failures.append({"t": time.time(), "reason": reason[:200]})
    state["login_failures"] = failures
    save_json(STATE_PATH, state)


def login_locked():
    state = load_json(STATE_PATH, {})
    recent = [f for f in state.get("login_failures", [])
              if time.time() - f["t"] < LOGIN_LOCK_WINDOW]
    return len(recent) >= LOGIN_MAX_FAILURES, recent


def _join(url, location):
    from urllib.parse import urljoin
    return urljoin(url, location.replace("&amp;", "&"))


def _follow(s: requests.Session, url: str, max_hops=15):
    """手动跟随 302 与 meta-refresh，返回 (最终URL, 最终响应)。"""
    r = None
    for _ in range(max_hops):
        rate_sleep()
        r = s.get(url, allow_redirects=False, timeout=TIMEOUT)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location", "")
            if not loc:
                raise LoginAborted(f"SSO 链：HTTP {r.status_code} 无 Location")
            url = _join(url, loc)
            continue
        if 200 <= r.status_code < 300:
            m = META_REFRESH.search(r.text)
            if m:
                url = _join(url, unescape(m.group(1)))
                continue
        return url, r
    raise LoginAborted("SSO 链：重定向次数过多")


def _mail_sid(s: requests.Session):
    for c in s.cookies:
        if c.name == "Coremail.sid" and "mail.zju.edu.cn" in (c.domain or ""):
            return c.value
    return None


def _auth_failure_page(r: requests.Response) -> LoginAuthError:
    cas_err = ""
    for pat in [r'id="errormsg"[^>]*>([^<]*)<', r'id="msg(?:box)?"[^>]*>([^<]*)<',
                r'class="errors[^"]*"[^>]*>([^<]*)<']:
        m = re.search(pat, r.text, re.I)
        if m and m.group(1).strip():
            cas_err = m.group(1).strip()
            break
    (SCRIPT_DIR / "last_login_fail.html").write_text(r.text, "utf-8")
    return LoginAuthError(
        f"登录失败：HTTP {r.status_code}"
        f"{('；服务端消息：' + cas_err) if cas_err else ''}。"
        f"完整页面已存 {SCRIPT_DIR / 'last_login_fail.html'} 供排查")


def do_login(unverified_ok: bool = False) -> requests.Session:
    """SSO 链登录：sso.jsp -> CAS 登录页 -> getPubKey -> POST -> 回跳建会话。"""
    cfg = load_json(CONFIG_PATH, {})
    if not cfg.get("username") or not cfg.get("password"):
        raise LoginAborted("config.json 缺少 username/password")
    if not cfg.get("password_verified") and not unverified_ok:
        raise LoginAborted(
            "config.json 的 password_verified=false（密码尚未登录验证通过）。"
            "确认密码正确后运行 `python zjuem_mail.py login --unverified-ok`；"
            "成功后此标志自动置 true。")

    s = requests.Session()
    s.headers["User-Agent"] = UA

    rate_sleep()
    r = s.get(f"{CAS}/v2/getKaptchaStatus", timeout=TIMEOUT)
    if r.text.strip().lower() == "true":
        raise LoginAborted("统一认证当前要求图形验证码，已按安全纪律中止。"
                           "请先用浏览器正常登录一次（或等风控解除）再试。")

    url, r = _follow(s, SSO_ENTRY)
    if "cas/login" not in url:
        raise LoginAborted(f"SSO 入口未跳转到统一认证登录页（终止于 {url}），"
                           "页面可能已改版，勿盲目重试")
    m = re.search(r'name="execution" value="(.*?)"', r.text, re.S)
    if not m:
        raise LoginAborted("统一认证登录页解析失败：未找到 execution 字段")
    execution = m.group(1)

    rate_sleep()
    r = s.get(f"{CAS}/v2/getPubKey", timeout=TIMEOUT)
    key = r.json()
    if not key.get("modulus") or not key.get("exponent"):
        raise LoginAborted(f"统一认证公钥获取失败：{r.text[:200]}")
    pwd_enc = rsa_encrypt(cfg["password"], key["modulus"], key["exponent"])

    rate_sleep()
    r = s.post(url, data={"username": cfg["username"], "password": pwd_enc,
                          "authcode": "", "execution": execution,
                          "_eventId": "submit", "rememberMe": "true", "ys": "5"},
               allow_redirects=False, timeout=TIMEOUT)

    def _nav(target):
        try:
            return _follow(s, target)
        except LoginAborted:
            if _mail_sid(s):
                return target, r
            raise

    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if not loc:
            raise _auth_failure_page(r)
        url2, r2 = _nav(_join(url, loc))
    elif 200 <= r.status_code < 300:
        m2 = META_REFRESH.search(r.text)
        if m2:
            url2, r2 = _nav(_join(url, unescape(m2.group(1))))
        else:
            raise _auth_failure_page(r)  # 停在登录页 = 凭据被拒
    else:
        raise _auth_failure_page(r)

    sid = _mail_sid(s)
    if not sid:
        raise _auth_failure_page(r2)
    cfg2 = dict(cfg)
    cfg2["password_verified"] = True
    cfg2["_note"] = ("密码已于 %s 登录验证通过（此标志由脚本自动维护）。"
                     % time.strftime("%Y-%m-%d %H:%M"))
    save_json(CONFIG_PATH, cfg2)
    persist_session(s, sid, cfg["username"])
    return s


def persist_session(s: requests.Session, sid: str, username: str):
    cookies = [{"name": c.name, "value": c.value, "domain": c.domain,
                "path": c.path} for c in s.cookies
               if "zju.edu.cn" in (c.domain or "")]
    save_json(SESSION_PATH, {"username": username, "sid": sid,
                             "cookies": cookies,
                             "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def probe_session(s: requests.Session, sid: str) -> bool:
    data = api_post(s, "mbox:getAllFolders", sid, {})
    return data.get("code") == "S_OK"


def session_from_file_or_login(allow_retry=False, unverified_ok=False):
    """优先复用持久化会话；失效才重登。密码未验证状态禁止自动重登。"""
    cfg = load_json(CONFIG_PATH, {})
    saved = load_json(SESSION_PATH, None)
    if saved and saved.get("cookies") and saved.get("sid") and \
            saved.get("username") == cfg.get("username"):
        s = requests.Session()
        s.headers["User-Agent"] = UA
        for c in saved["cookies"]:
            s.cookies.set(c["name"], c["value"],
                          domain=c.get("domain") or ".zju.edu.cn",
                          path=c.get("path") or "/")
        try:
            if probe_session(s, saved["sid"]):
                return s, saved["sid"]
        except requests.RequestException:
            pass
        print("[session] 持久化会话已失效，需要重新登录")

    if not cfg.get("password_verified") and not unverified_ok:
        raise SystemExit(
            "[safety] 会话失效且密码未通过登录验证，拒绝自动重登以免消耗"
            "统一认证尝试额度。更新 config.json 密码后运行 "
            "`python zjuem_mail.py login --unverified-ok`。")

    locked, recent = login_locked()
    if locked and not allow_retry:
        raise SystemExit(
            f"[safety] 1 小时内凭据类登录失败已达 {len(recent)} 次，已锁定。"
            "请人工核对密码后用 --allow-retry 重试。失败原因："
            + " | ".join(f.get("reason", "")[:80] for f in recent))

    try:
        s = do_login()
    except LoginAuthError as e:
        record_failure(str(e))
        raise SystemExit(f"[login] {e}")
    except LoginAborted as e:
        raise SystemExit(f"[login] 已中止（未消耗尝试额度）：{e}")
    print("[login] 登录成功，会话已持久化")
    return s, load_json(SESSION_PATH, {})["sid"]


# ---------------------------------------------------------- API 调用

def api_post(s: requests.Session, func: str, sid: str, body: dict) -> dict:
    rate_sleep()
    r = s.post(f"{MAIL}/coremail/s/json?sid={sid}&func={func}",
               json=body, timeout=TIMEOUT)
    try:
        return r.json()
    except ValueError:
        return {"code": f"HTTP_{r.status_code}", "raw": r.text[:300]}


# ---------------------------------------------------------- 公文排版

def _p(text: str, indent="", align="") -> str:
    style = "margin:0;"
    if indent:
        style += f" text-indent:{indent};"
    if align:
        style += f" text-align:{align};"
    return f'<p style="{style}">{text}</p>'


def render_letter(spec: dict) -> str:
    """标准书信/公文格式 HTML：
    - 中文仿宋、英文和数字 Times New Roman（font-family 顺序实现混排）
    - 字号统一小四（12pt），行距 1.5
    - 称呼顶格；正文每段首行缩进 2 字符；"此致"缩进 2 字符、"敬礼！"顶格；
      署名与日期右对齐。
    """
    parts = []
    if spec.get("salutation"):
        parts.append(_p(html_escape(spec["salutation"])))
    for para in spec.get("paragraphs") or []:
        if str(para).strip():
            parts.append(_p(html_escape(str(para)), indent="2em"))
    if spec.get("closing", True):
        parts.append(_p("此致", indent="2em"))
        parts.append(_p("敬礼！"))
    if spec.get("signature"):
        parts.append(_p(html_escape(spec["signature"]), align="right"))
    if spec.get("date"):
        parts.append(_p(html_escape(spec["date"]), align="right"))
    inner = "".join(parts)
    return (f'<div style="font-family:{FONT_STACK}; font-size:{SIZE_XIAOSI}; '
            f'line-height:{LINE_HEIGHT};">{inner}</div>')


def default_cn_date() -> str:
    from datetime import date
    d = date.today()
    return f"{d.year}年{d.month}月{d.day}日"


# ---------------------------------------------------------- 存草稿（唯一的写操作）

def save_draft(s: requests.Session, sid: str, *, account: str,
               to: list, cc: list, bcc: list, subject: str,
               content_html: str) -> dict:
    """存草稿。action 写死 "save"，本工具永远不产生发送动作。"""
    # 第一步：创建写信会话，服务器返回 compose id（毫秒时间戳字符串）
    r1 = api_post(s, "mbox:compose", sid, {})
    if r1.get("code") != "S_OK" or not r1.get("var"):
        raise RuntimeError(f"创建写信会话失败：{json.dumps(r1, ensure_ascii=False)[:300]}")
    compose_id = str(r1["var"])

    attrs = {
        "account": account,
        "to": to, "cc": cc, "bcc": [],
        "showOneRcpt": False,
        "smimeSign": False, "smimeEncrypt": False, "smimeEnvelopId": "",
        "saveSentCopy": True,
        "requestReadReceipt": False,
        "scheduleDate": None,
        "forbidDownload": False,
        "subject": subject,
        "isHtml": True,
        "content": content_html,
        "attachments": [],
    }
    # bcc 参数仅在显式给值时生效
    attrs["bcc"] = bcc
    rate_sleep()
    r = s.post(f"{MAIL}/coremail/common/mbox/compose.jsp?isUserConfirmed=true&sid={sid}",
               json={"id": compose_id, "attrs": attrs, "returnInfo": True,
                     "encryptPassword": "", "action": "save"},
               timeout=TIMEOUT)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"保存草稿返回非 JSON：HTTP {r.status_code} {r.text[:200]}")
    if data.get("code") != "S_OK":
        raise RuntimeError(f"保存草稿失败：{json.dumps(data, ensure_ascii=False)[:400]}")
    return data


def list_drafts(s: requests.Session, sid: str, limit=20) -> dict:
    return api_post(s, "mbox:listMessages", sid,
                    {"start": 0, "limit": limit, "order": "receivedDate",
                     "desc": True, "returnTotal": True, "fid": DRAFT_FID,
                     "mboxa": ""})


# ---------------------------------------------------------- 命令

def _flat_addrs(value: str) -> list:
    return [a.strip() for a in re.split(r"[;,；，]", value) if a.strip()]


def cmd_login(args):
    do_login()
    print("[login] 完成")


def cmd_check(args):
    s, sid = session_from_file_or_login(allow_retry=args.allow_retry)
    data = api_post(s, "mbox:getAllFolders", sid, {})
    if data.get("code") == "S_OK":
        names = [f"{f['id']}:{f['name']}" for f in data.get("var", [])]
        print("[check] 会话有效。文件夹：", " ".join(names))
    else:
        raise SystemExit(f"[check] 会话异常：{json.dumps(data, ensure_ascii=False)[:200]}")


def cmd_draft(args):
    spec = {}
    if args.json_file:
        spec = load_json(Path(args.json_file), None)
        if spec is None:
            raise SystemExit(f"[draft] 无法读取/解析 JSON 文件：{args.json_file}")
    else:
        if not args.subject:
            raise SystemExit("[draft] 必须 --subject 或 --json-file")
        spec = {"to": _flat_addrs(args.to or ""),
                "cc": _flat_addrs(args.cc or ""),
                "subject": args.subject,
                "salutation": args.salutation or "",
                "paragraphs": [l for l in (args.body or "").split("\n") if l.strip()],
                "closing": not args.no_closing,
                "signature": args.signature or "",
                "date": args.date if args.date is not None else default_cn_date()}

    to = spec.get("to") or []
    if not to:
        raise SystemExit("[safety] 缺少收件人（to）。为防误存，无收件人的草稿一律拒绝；"
                         "确要无收件人请加 --allow-empty-to")
    subject = str(spec.get("subject") or "").strip()
    if not subject:
        raise SystemExit("[safety] 缺少主题（subject）。为防误存，无主题草稿一律拒绝")

    content = render_letter(spec)
    s, sid = session_from_file_or_login(allow_retry=args.allow_retry)

    cfg = load_json(CONFIG_PATH, {})
    account = str(spec.get("account") or cfg.get("username"))
    if "@" not in account:
        account = f"{account}@zju.edu.cn"
    display_name = str(cfg.get("display_name") or "").strip()
    if display_name and not account.startswith('"'):
        account = f'"{display_name}" <{account}>'
    result = save_draft(s, sid, account=account, to=to,
                        cc=spec.get("cc") or [], bcc=spec.get("bcc") or [],
                        subject=subject, content_html=content)

    saved = result.get("var") or {}
    print(f"[draft] 草稿已保存到草稿箱（未发送）")
    print(f"  主题：{subject}")
    print(f"  收件人：{', '.join(to)}")
    if saved.get("id"):
        print(f"  草稿 id：{saved['id']}")
    print("  请到网页邮箱草稿箱核对排版后再自行发送。")

    if args.verify:
        data = list_drafts(s, sid, limit=5)
        items = data.get("var") or []
        hit = next((m for m in items if str(m.get("subject")) == subject), None)
        if hit:
            print(f"[verify] 草稿箱确认存在该草稿（fid={hit.get('fid')}, "
                  f"size={hit.get('size')}B, sentDate={hit.get('sentDate')}）")
        else:
            print("[verify] 警告：草稿箱前 5 封未回读到该草稿，请手动核对")


def cmd_list_drafts(args):
    s, sid = session_from_file_or_login(allow_retry=args.allow_retry)
    data = list_drafts(s, sid, limit=args.limit)
    if data.get("code") != "S_OK":
        raise SystemExit(f"[drafts] 读取失败：{json.dumps(data, ensure_ascii=False)[:200]}")
    items = data.get("var") or []
    print(f"草稿箱（显示最近 {len(items)} 封）：")
    for m in items:
        print(f"  [{m.get('sentDate','')}] {m.get('subject','(无主题)')} "
              f"-> {m.get('to') or '(无收件人)'}  id={m.get('id')}")


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="浙大邮箱草稿箱工具（只存草稿，绝不发送）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="登录（保存会话）")
    p.add_argument("--unverified-ok", action="store_true",
                   help="密码未验证状态下放行一次登录尝试")
    p.add_argument("--allow-retry", action="store_true",
                   help="失败锁定后，人工确认原因并修复后放行一次")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("check", help="校验会话有效性")
    p.add_argument("--allow-retry", action="store_true")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("draft", help="按公文格式排版并存入草稿箱（不发送）")
    p.add_argument("--json-file", help="结构化信件 JSON（to/cc/subject/salutation/"
                                       "paragraphs/closing/signature/date）")
    p.add_argument("--to", help="收件人（逗号/分号分隔多个）")
    p.add_argument("--cc", default="")
    p.add_argument("--subject")
    p.add_argument("--salutation", help="称呼，如：尊敬的老师：")
    p.add_argument("--body", default="", help="正文，多段用换行分隔（每段首行自动缩进）")
    p.add_argument("--no-closing", action="store_true", help="不自动加“此致/敬礼！”")
    p.add_argument("--signature", help="署名（右对齐）")
    p.add_argument("--date", help="日期（右对齐），默认今天中文格式；传空串取消")
    p.add_argument("--allow-empty-to", action="store_true")
    p.add_argument("--verify", action="store_true", help="保存后回读草稿箱核对")
    p.add_argument("--allow-retry", action="store_true")
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("list-drafts", help="列出草稿箱")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--allow-retry", action="store_true")
    p.set_defaults(fn=cmd_list_drafts)

    args = parser.parse_args()
    acquire_lock()
    try:
        if args.cmd == "login":
            cfg = load_json(CONFIG_PATH, {})
            if not cfg.get("password_verified") and not args.unverified_ok:
                raise SystemExit("[safety] password_verified=false：先确认 config.json "
                                 "密码正确，再显式 `login --unverified-ok`")
            locked, recent = login_locked()
            if locked and not args.allow_retry:
                raise SystemExit("[safety] 1 小时内凭据类失败已锁定，"
                                 "人工核对后用 --allow-retry 放行一次")
            try:
                do_login(unverified_ok=args.unverified_ok)
            except LoginAuthError as e:
                record_failure(str(e))
                raise SystemExit(f"[login] {e}")
            except LoginAborted as e:
                raise SystemExit(f"[login] 已中止（未消耗尝试额度）：{e}")
            print("[login] 登录成功")
        else:
            args.fn(args)
    finally:
        release_lock()


if __name__ == "__main__":
    main()
