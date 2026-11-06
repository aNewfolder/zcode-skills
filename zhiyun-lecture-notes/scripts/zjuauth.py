# -*- coding: utf-8 -*-
"""zjuauth.py —— zhiyun-lecture-notes skill 共享基础：限速、单实例锁、凭据、统一认证 CAS 登录、会话持久化。

CAS 登录实现已在 2026-09 实测跑通，RSA 加密与浏览器原版 security.js 逐位一致。
凭据只读本目录 config.json（用户手动创建，{username, password, password_verified}）。

安全纪律：
- 统一认证连续错约 5 次锁号：凭据类失败 1 小时内最多 2 次（state.json 记账）；
  password_verified=false 时拒绝自动登录。
- 全局单实例锁 + 请求最小间隔，禁止并行调用。
"""
import json
import os
import re
import ssl
import struct
import sys
import time
from pathlib import Path
from html import unescape
from urllib.parse import urljoin, unquote

import requests
from requests.adapters import HTTPAdapter

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CAS = "https://zjuam.zju.edu.cn/cas"

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "state.json"
LOCK_PATH = SCRIPT_DIR / ".lock"
SESSIONS_DIR = SCRIPT_DIR / "sessions"
JOURNAL_DIR = SCRIPT_DIR / "journal"

RATE_LIMIT = 1.5          # 本 skill 所有 HTTP 请求的最小间隔（秒）
TIMEOUT = 30
LOGIN_MAX_FAILURES = 2    # 1 小时内最大凭据类登录失败次数
LOGIN_LOCK_WINDOW = 3600
LOCK_STALE_SECONDS = 90

_last_request_time = 0.0


# ---------------------------------------------------------- 基础设施

class TLSAdapter(HTTPAdapter):
    """浙大部分站点用 1024 位弱 DH 密钥交换，新 OpenSSL 默认 SECLEVEL=2 握手直接拒绝。
    只把密码套件安全级别降到 1；证书校验（CERT_REQUIRED/系统 CA/主机名）全部不变。"""

    def init_poolmanager(self, connections, maxsize, block=False, **kw):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kw["ssl_context"] = ctx
        return super().init_poolmanager(connections, maxsize, block, **kw)


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.mount("https://", TLSAdapter())
    return s


def rate_sleep(min_interval: float = None):
    global _last_request_time
    interval = min_interval if min_interval is not None else RATE_LIMIT
    wait = interval - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return default


def save_json(path, data, chmod=False):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, p)
    if chmod:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


class LockBusy(RuntimeError):
    pass


def acquire_lock(tag="main"):
    """单实例锁：同一时刻只允许一个实例发请求（限速纪律）。崩溃残留 90s 后自动失效。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            data = json.loads(LOCK_PATH.read_text("utf-8"))
            if time.time() - data.get("t", 0) < LOCK_STALE_SECONDS:
                raise LockBusy(
                    f"[safety] 另一个本 skill 脚本实例正在运行（{time.time() - data.get('t', 0):.0f} 秒前启动），"
                    "为不突破限速纪律禁止并行，请串行执行或稍后重试。")
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    LOCK_PATH.write_text(json.dumps({"t": time.time(), "tag": tag}), "utf-8")


def release_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def force_utf8():
    """Windows 下控制台代码页可能是 GBK，强制 UTF-8 输出避免乱码/编码异常。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------- 凭据

def load_credentials(unverified_ok: bool = False) -> dict:
    """凭据只读本目录 config.json（用户手动创建，{username,password}）。不复制、不落盘第二份密码。"""
    cfg = load_json(CONFIG_PATH, {})
    if not cfg.get("username") or not cfg.get("password"):
        raise RuntimeError("缺少统一认证凭据：请在 %s 创建 {\"username\":..., \"password\":...}" % CONFIG_PATH)
    if not cfg.get("password_verified") and not unverified_ok:
        raise RuntimeError(
            "当前密码已被验证是错误的（password_verified=false），已按安全纪律拒绝自动登录。"
            "更正密码后把该标志改回 true。")
    return cfg


# ---------------------------------------------------------- 登录失败记账

def record_failure(reason: str):
    state = load_json(STATE_PATH, {})
    failures = [f for f in state.get("login_failures", []) if time.time() - f["t"] < LOGIN_LOCK_WINDOW]
    failures.append({"t": time.time(), "reason": (reason or "")[:200]})
    state["login_failures"] = failures
    save_json(STATE_PATH, state)


def login_locked():
    state = load_json(STATE_PATH, {})
    recent = [f for f in state.get("login_failures", []) if time.time() - f["t"] < LOGIN_LOCK_WINDOW]
    return len(recent) >= LOGIN_MAX_FAILURES, recent


class LoginAuthError(RuntimeError):
    """凭据类登录失败（CAS 明确拒绝），消耗统一认证失败额度。"""


class LoginAborted(RuntimeError):
    """中止类失败：未真正提交凭据（验证码/页面改版/网络），不记失败额度。"""


# ---------------------------------------------------------- CAS 登录

def _bi_high_index(n: int) -> int:
    idx = -1
    while n:
        n >>= 16
        idx += 1
    return max(idx, 0)


def rsa_encrypt(password: str, modulus_hex: str, exponent_hex: str) -> str:
    """统一认证登录加密（ohdave RSA，UTF-16 码元反转 + 16-bit 小端分块）。
    与浏览器原版 security.js 逐位一致（实测验证过的实现）。"""
    if any(ord(c) > 0xFF for c in password):
        raise ValueError("密码含非 ASCII 字符：浏览器原版加密对这类字符有损，无法登录")
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


META_REFRESH = re.compile(
    r'<meta[^>]*http-equiv\s*=\s*["\']?refresh["\']?[^>]*url\s*=\s*["\']?([^"\'>;\s]+)', re.I)
EXECUTION_RE = re.compile(r'name="execution" value="(.*?)"', re.S)
CAS_ERR_RE = [re.compile(p, re.I) for p in
              (r'id="errormsg"[^>]*>([^<]*)<', r'id="msg(?:box)?"[^>]*>([^<]*)<',
               r'class="auth_error[^"]*"[^>]*>\s*([^<]*)<', r'<span[^>]*class="error"[^>]*>([^<]*)<')]


def _follow_meta(s: requests.Session, r: requests.Response, hops: int = 5) -> requests.Response:
    for _ in range(hops):
        m = META_REFRESH.search(r.text or "")
        if not m:
            return r
        rate_sleep()
        r = s.get(urljoin(r.url, unescape(m.group(1))), timeout=TIMEOUT)
    return r


def cas_sso(entry_url: str, ok_check, fresh: bool = False, attempts: int = 2):
    """统一认证 SSO 通用登录。

    entry_url: 业务入口（会 302/meta-refresh 到 CAS 登录页）。
    ok_check(session, response) -> bool: 判定"已拿到目标站点会话"（如关键 cookie 存在且不在登录页）。
    fresh: True 时先清掉该入口域的 cookie 强制重登（由调用方在保存的会话失效时传入）。

    返回 (session, response, reused)。reused=True 表示现有会话本来就好，没发登录表单。
    失败抛 LoginAuthError（记额度）/ LoginAborted（不记）。
    """
    if fresh:
        locked, recent = login_locked()
        if locked:
            raise LoginAborted(
                "[safety] 1 小时内已有 %d 次凭据类登录失败，继续尝试会锁统一认证账号。"
                "请先确认密码正确（改 config.json 的 password 后把 "
                "password_verified 改 true），一小时后再试。" % len(recent))
    cfg = load_credentials()

    s = new_session()
    rate_sleep()
    try:
        r = s.get(f"{CAS}/v2/getKaptchaStatus", timeout=TIMEOUT)
        if r.text.strip().lower() == "true":
            raise LoginAborted(
                "统一认证当前要求图形验证码（getKaptchaStatus=true），已按安全纪律中止。"
                "请先用浏览器正常登录一次统一认证（或等风控解除）再试。")
    except LoginAborted:
        raise
    except Exception as e:
        raise LoginAborted(f"统一认证可达性检查失败：{e}")

    for attempt in range(1, attempts + 1):
        rate_sleep()
        try:
            r = s.get(entry_url, timeout=TIMEOUT, allow_redirects=True)
        except Exception as e:
            raise LoginAborted(f"入口请求失败：{e}")
        r = _follow_meta(s, r)
        if ok_check(s, r):
            return s, r, True
        if "cas/login" not in r.url and "zjuam.zju.edu.cn" not in r.url:
            raise LoginAborted(
                f"入口未跳转到统一认证（终止于 {r.url}，HTTP {r.status_code}）。页面可能改版，勿盲目重试。")
        m = EXECUTION_RE.search(r.text or "")
        if not m:
            raise LoginAborted(f"统一认证登录页解析失败（HTTP {r.status_code}，未找到 execution），页面可能改版。")
        execution = m.group(1)

        rate_sleep()
        try:
            pk = s.get(f"{CAS}/v2/getPubKey", timeout=TIMEOUT).json()
        except Exception as e:
            raise LoginAborted(f"统一认证公钥获取失败：{e}")
        modulus, exponent = pk.get("modulus"), pk.get("exponent")
        if not modulus or not exponent:
            raise LoginAborted("统一认证公钥响应缺少 modulus/exponent")

        try:
            pwd_enc = rsa_encrypt(cfg["password"], modulus, exponent)
        except ValueError as e:
            raise LoginAborted(f"本地密码校验未通过，未发起登录：{e}")

        rate_sleep()
        try:
            r = s.post(r.url, data={"username": cfg["username"], "password": pwd_enc,
                                    "authcode": "", "execution": execution,
                                    "_eventId": "submit", "rememberMe": "true", "ys": "5"},
                       allow_redirects=True, timeout=TIMEOUT)
        except Exception as e:
            raise LoginAborted(f"登录表单提交网络失败（未确认是否凭据问题）：{e}")
        r = _follow_meta(s, r)
        if ok_check(s, r):
            return s, r, False
        if r.status_code >= 500:
            raise LoginAborted(f"统一认证/站点服务异常：HTTP {r.status_code}（非凭据被拒，不计失败额度）")
        # 停在 CAS 或业务登录页：区分"明确拒绝"与"页面异常"
        body = r.text or ""
        msg = None
        for pat in CAS_ERR_RE:
            mm = pat.search(body)
            if mm and mm.group(1).strip():
                msg = mm.group(1).strip()
                break
        if "cas/login" in r.url and msg:
            record_failure(msg)
            raise LoginAuthError(f"统一认证拒绝：{msg}")
        if attempt < attempts:
            log(f"[auth] 第 {attempt} 次回跳未拿到会话（终止于 {r.url}），1.5s 后重试")
            time.sleep(1.5)
            continue
        if "cas/login" in r.url:
            record_failure("回跳停在 CAS 登录页且无错误信息")
            raise LoginAuthError("统一认证未通过（停在登录页，无明确错误信息）")
        record_failure(f"回跳后仍无目标会话：{r.url}")
        raise LoginAuthError(f"登录回跳后未取得目标站点会话（终止于 {r.url}，HTTP {r.status_code}）；"
                             "请勿自动重试，人工核对后再试。")
    raise LoginAborted("登录流程异常退出")


# ---------------------------------------------------------- 会话持久化

def persist_session(name: str, s: requests.Session, domains):
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    doms = tuple(domains)
    cookies = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
               for c in s.cookies if any(c.domain == d or c.domain == "." + d.lstrip(".") or c.domain.endswith(d) for d in doms)]
    save_json(SESSIONS_DIR / f"{name}.json",
              {"cookies": cookies, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}, chmod=True)


def restore_session(name: str, s: requests.Session, max_age_hours: float = None) -> bool:
    data = load_json(SESSIONS_DIR / f"{name}.json", None)
    if not data or not data.get("cookies"):
        return False
    if max_age_hours is not None:
        try:
            saved = time.mktime(time.strptime(data["saved_at"], "%Y-%m-%d %H:%M:%S"))
            if time.time() - saved > max_age_hours * 3600:
                return False
        except Exception:
            return False
    for c in data["cookies"]:
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    return True


def clear_session(name: str):
    try:
        (SESSIONS_DIR / f"{name}.json").unlink(missing_ok=True)
    except OSError:
        pass


def journal(action: str, detail: dict):
    """写操作审计日志（append JSONL）。落盘失败必须中止写操作。"""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = JOURNAL_DIR / f"{time.strftime('%Y%m')}-{action}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **detail},
                           ensure_ascii=False) + "\n")
