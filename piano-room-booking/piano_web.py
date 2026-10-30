#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
琴房预约助手 - 多人共享版网页控制台
====================================
运行在阿里云服务器上(nginx /piano/ 反代 -> 127.0.0.1:8039), 三类入口:

  /t/<站长token>/          站长控制台(原有全部功能, token 在 web_token.txt)
  /t/<站长token>/admin     总控制台: 所有用户的计划/订单/门禁密码/候补/动态,
                           优先级调整、启停、删除、代约代取消、注册链接管理
  /reg/<注册token>/        注册页: 统一认证学号+密码验证后发放个人专属链接
  /u/<个人token>/          成员控制台: 与站长相同的预约功能(仅限本人数据)

安全设计(参考 xzzd-homework 多用户方案):
  - 成员密码绝不落盘: 注册/续期的密码只在请求线程内存中完成一次 CAS
    登录验证, 服务器仅保存登录后的会话 cookie(600 权限); 密码不进
    日志、不经 argv。会话失效时成员在面板「会话与安全」重新输密码续期。
  - 注册接口熔断: 学号 1h/2 次、IP 1h/10 次凭据失败(防借注册接口爆破
    统一认证); 服务端/网络故障不计入。续期每用户 1h/3 次。
  - CAS 登录全局串行 + 1s 间隔(注册/续期/站长重登共用一把锁)。
  - 所有链接 token 为 24 位 hex(96bit), URL 即凭据。

调度设计(单进程内三个守护线程, 接管原 piano-booking.timer 与
piano-watch 守护进程, 部署时两者应停用避免双跑):
  - scheduler: 每天 open_time 前 scheduler_lead_seconds 秒预检各用户
    会话(站长可自动重登, 成员失效则飞书提醒), 到点后按「优先级升序
    (站长=0 恒最高)」逐人预约其窗口内计划。优先级只体现在预约顺序上
    (先约者先挑房间/先捡空位), 绝不取消任何用户已成功的订单。
    收尾把约满目标转入候补并汇总飞书推送(仅推送站长自己的 webhook,
    成员端永远没有推送, 结果只在其个人面板展示)。
  - watch: 合并候补队列按优先级序扫描, 高优先级先捡漏。
  - keepalive: 对有近期计划/候补的用户定期轻量请求, 防学校网关把闲置
    会话踢掉(约 3h), 使成员免密码会话也能在放票时刻可用。

仅用 Python 标准库 + requests(经 piano_booking)。
"""

import json
import os
import random
import re
import secrets
import shutil
import threading
import time
import traceback
from collections import defaultdict, deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import piano_booking as pb

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
TOKEN_PATH = BASE_DIR / "web_token.txt"
WEBLOG = BASE_DIR / "web.log"
USERS_DIR = BASE_DIR / "users"
USERS_FILE = BASE_DIR / "users.json"
REG_TOKEN_PATH = BASE_DIR / "reg_token.txt"
HUB_STATE_PATH = BASE_DIR / "hub_state.json"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PIANO_WEB_PORT", "8039"))

CFG_LOCK = threading.Lock()   # 保护 config.json(全局配置)读写
BOOK_LOCK = threading.Lock()  # 一切预约/取消提交串行化(全用户共享)
CAS_LOCK = threading.Lock()   # CAS 登录全局串行(注册/续期/站长重登)
USERS_LOCK = threading.Lock() # 用户注册表 users.json
WATCH_LOCK = threading.Lock() # 候补队列 watch.json 读改写
SESSION_MAX_AGE = 2 * 3600
SESSIONS = {}                 # uid -> {"s": Session, "ts": float}
ORDERS_CACHE = {}             # uid -> {"orders": [...], "ts": float}
DEAD_NOTIFIED = set()         # 已就"会话失效"发过飞书的 uid(每轮失效只报一次)
SCHED = {"fired_date": None, "next_fire": "", "last_result": "", "last_run": ""}


class NeedsRelogin(RuntimeError):
    """成员会话已失效且无保存密码, 必须用户在面板续期。"""


def wlog(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(WEBLOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def harden(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def get_token() -> str:
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    harden(TOKEN_PATH, 0o600)
    return token


def reg_token() -> str:
    if REG_TOKEN_PATH.exists():
        return REG_TOKEN_PATH.read_text(encoding="utf-8").strip()
    t = secrets.token_hex(12)
    REG_TOKEN_PATH.write_text(t, encoding="utf-8")
    harden(REG_TOKEN_PATH, 0o600)
    return t


# ---------------- 用户注册表 ----------------
def _save_users_nolock(d):
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    harden(USERS_DIR, 0o700)
    tmp = USERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, USERS_FILE)
    harden(USERS_FILE, 0o600)


def ensure_users() -> dict:
    """加载并自愈用户注册表; 首次运行以 web_token.txt 引导站长账号(uid 0,
    优先级恒 0, token 始终与 web_token.txt 同步, 旧链接不失效)。"""
    with USERS_LOCK:
        mutated = False
        try:
            d = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = None
        if not isinstance(d, dict) or not isinstance(d.get("users"), list):
            d = {"users": [], "next_uid": 1}
            mutated = True
        clean = []
        for u in d["users"]:
            if not isinstance(u, dict):
                mutated = True
                continue
            try:
                u["uid"] = int(u.get("uid"))
            except (TypeError, ValueError):
                mutated = True
                continue
            if u.get("student_id") is None:
                u["student_id"] = ""
            u.setdefault("label", "")
            if not u.get("token"):
                u["token"] = secrets.token_hex(12)
                mutated = True
            if u.get("role") not in ("owner", "member"):
                u["role"] = "member"
            try:
                u["priority"] = max(1, min(999, int(u.get("priority", 100))))
            except (TypeError, ValueError):
                u["priority"] = 100
            u["enabled"] = bool(u.get("enabled", True))
            u.setdefault("created_at", "")
            clean.append(u)
        if len(clean) != len(d["users"]):
            mutated = True
        d["users"] = clean
        max_uid = max([0] + [u["uid"] for u in clean])
        try:
            nu = int(d.get("next_uid", 1))
        except (TypeError, ValueError):
            nu = 1
        if nu <= max_uid:
            d["next_uid"] = max_uid + 1
            mutated = True
        owner = next((u for u in clean if u["uid"] == 0), None)
        if owner is None:
            sid = ""
            try:
                sid = str(pb.load_config(CONFIG_PATH).get("username") or "")
            except Exception:
                pass
            clean.insert(0, {"uid": 0, "student_id": sid, "label": "站长",
                             "token": get_token(), "role": "owner",
                             "priority": 0, "enabled": True,
                             "created_at": ""})
            mutated = True
        else:
            tok = get_token()
            if owner.get("token") != tok or owner.get("role") != "owner" \
                    or int(owner.get("priority") or 0) != 0 \
                    or not owner.get("enabled"):
                owner["token"] = tok
                owner["role"] = "owner"
                owner["priority"] = 0
                owner["enabled"] = True
                mutated = True
        if mutated:
            _save_users_nolock(d)
        return d


def save_users(d):
    with USERS_LOCK:
        _save_users_nolock(d)


def get_user(uid):
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return None
    return next((u for u in ensure_users()["users"] if u["uid"] == uid), None)


def user_by_token(tok):
    if not tok:
        return None
    return next((u for u in ensure_users()["users"]
                 if u.get("token") == tok), None)


def enabled_users():
    """启用用户按 (优先级, uid) 升序; 站长 uid=0 恒在最前。"""
    us = [u for u in ensure_users()["users"] if u.get("enabled")]
    us.sort(key=lambda u: (int(u.get("priority", 100)), u["uid"]))
    return us


def uname(u):
    if not u:
        return "?"
    if u.get("role") == "owner":
        return "站长"
    lbl = str(u.get("label") or "")
    sid = str(u.get("student_id") or "")
    return f"{lbl}({sid})" if lbl else (sid or f"uid{u.get('uid')}")


def cfg_path(uid):
    return CONFIG_PATH if uid == 0 else USERS_DIR / str(uid) / "config.json"


def load_user_cfg(uid):
    p = cfg_path(uid)
    if uid != 0 and not p.exists():
        return None
    try:
        return pb.load_config(p)
    except Exception as e:
        wlog(f"(web) 用户 {uid} 配置读取失败({e.__class__.__name__}: {e})")
        return None


def default_member_cfg(sid, g):
    return {
        "username": sid,
        "timezone": g.get("timezone", "Asia/Shanghai"),
        "open_time": g.get("open_time", "08:00"),
        "advance_days": g.get("advance_days", 3),
        "campus": g.get("campus", "紫金港校区"),
        "room_preference": list(g.get("room_preference") or []),
        "targets": [],
        "retry_seconds": 30,
        "http_timeout": g.get("http_timeout", 20),
    }


def save_user_cfg_raw(uid, cfg):
    p = cfg_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)
    harden(p, 0o600)


def set_cfg_keys(updates):
    with CFG_LOCK:
        cfg = pb.load_config(CONFIG_PATH)
        cfg.update(updates)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)


# ---------------- 会话管理(密码不落盘, 仅存 cookie) ----------------
_UL_GUARD = threading.Lock()
_USER_LOCKS = {}


def ulock(uid):
    with _UL_GUARD:
        lk = _USER_LOCKS.get(uid)
        if lk is None:
            lk = _USER_LOCKS[uid] = threading.Lock()
    return lk


def session_file(uid):
    return USERS_DIR / str(uid) / "session.json"


def load_session_rec(uid):
    try:
        d = json.loads(session_file(uid).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def save_session_rec(uid, rec):
    p = session_file(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, p)
    harden(p, 0o600)


def rec_from_session(s, dead=False):
    d = pb.session_to_dict(s)
    d["dead"] = bool(dead)
    return d


def owner_relogin(cfg):
    last = None
    for i in range(2):
        try:
            with CAS_LOCK:
                time.sleep(1.0)  # 与成员注册/续期共用全局登录闸门
                return pb.cas_login(str(cfg["username"]), str(cfg["password"]),
                                    min(cfg["http_timeout"], 10),
                                    cfg.get("proxy", ""))
        except Exception as e:
            last = e
            wlog(f"(web) 站长登录失败({e.__class__.__name__}: {e}), "
                 f"2s 后重试({i + 1}/2)")
            time.sleep(2)
    raise last


def get_session(uid=0, force=False):
    """取 uid 的会话。站长存有密码可自动重登; 成员仅用已保存的会话
    cookie, 失效抛 NeedsRelogin(须用户在面板续期)。"""
    with ulock(uid):
        rec = SESSIONS.get(uid)
        if uid == 0:
            age = time.time() - rec["ts"] if rec else 1e18
            if force or rec is None or age > SESSION_MAX_AGE:
                cfg = pb.load_config(CONFIG_PATH)
                wlog("(web) 重新登录 CAS (站长) ...")
                SESSIONS[0] = {"s": owner_relogin(cfg), "ts": time.time()}
            return SESSIONS[0]["s"]
        if force:
            SESSIONS.pop(uid, None)
            rec = None
        if rec is not None:
            return rec["s"]
        r = load_session_rec(uid)
        if not r or r.get("dead") or not (r.get("cookies") or {}):
            raise NeedsRelogin("登录会话已失效, 请输入统一认证密码续期")
        try:
            proxy = pb.load_config(CONFIG_PATH).get("proxy", "")
        except Exception:
            proxy = ""
        s = pb.session_from_dict(r, proxy)
        SESSIONS[uid] = {"s": s, "ts": time.time()}
        return s


def mark_session_dead(uid, why=""):
    with ulock(uid):
        SESSIONS.pop(uid, None)
        rec = load_session_rec(uid) or {}
        if rec.get("dead"):
            return
        rec["dead"] = True
        rec["dead_note"] = str(why)[:200]
        rec["dead_at"] = _now_str()
        save_session_rec(uid, rec)
    if uid == 0 or uid in DEAD_NOTIFIED:
        return
    DEAD_NOTIFIED.add(uid)
    u = get_user(uid)
    try:
        g = pb.load_config(CONFIG_PATH)
    except Exception:
        return
    notify_user_event(
        g, f"🔒 用户 {uname(u)} 的琴房登录会话已失效({why or '未知原因'})，"
           f"其自动预约/候补已暂停。请通知 TA 打开个人链接, "
           f"在「会话与安全」输入密码续期。")


def notify_user_event(g, text):
    """面向成员的事件通知(走站长配置的飞书 webhook, 可关)。"""
    if g.get("feishu_member_events", True):
        pb.feishu_notify(g, text)
    else:
        pb.log("成员事件(飞书通知已关): " + text.splitlines()[0])


# ---------------- 限流(滑动窗口, 内存记账) ----------------
class RateLimiter:
    def __init__(self):
        self.ev = {}

    def _dq(self, key):
        now = time.time()
        dq = self.ev.setdefault(key, deque())
        while dq and now - dq[0] > 3600:
            dq.popleft()
        return dq

    def blocked(self, key, limit):
        return len(self._dq(key)) >= limit

    def hit(self, key):
        self._dq(key).append(time.time())


REG_FAIL_ID = RateLimiter()  # 注册: 学号凭据失败 1h/2 (统一认证约错5次锁号)
REG_FAIL_IP = RateLimiter()  # 注册: IP 凭据失败 1h/10 (NAT 场景放宽到 20)
REG_OK_ID = RateLimiter()    # 注册: 学号成功 1h/3 (防反复重置链接空耗 CAS)
RENEW_FAIL = RateLimiter()   # 续期: 每用户凭据失败 1h/3


def _client_limit(ip):
    # 经 nginx 反代正常会带 X-Forwarded-For; 若没有(直连/回环), 放宽 IP 阈值
    return 20 if (not ip or ip.startswith("127.") or ip.startswith("::1")) else 10


def cas_login_with(sid, password, timeout, proxy):
    """带凭据的 CAS 登录(注册/续期专用): 全局串行 + 1s 间隔。
    密码只在本次调用栈内存中出现, 不写盘、不进日志。"""
    with CAS_LOCK:
        time.sleep(1.0)
        return pb.cas_login(sid, password, timeout, proxy)


def classify_login_error(e):
    if isinstance(e, pb.requests.RequestException):
        return "net"
    if "登录未成功" in str(e):
        return "cred"
    return "server"


# ---------------- 用户动态(历史) ----------------
def hist_file(uid):
    return USERS_DIR / str(uid) / "history.json"


def hist_add(uid, event, detail=""):
    try:
        p = hist_file(uid)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            items = []
        if not isinstance(items, list):
            items = []
        items.append({"ts": _now_str(), "event": str(event)[:32],
                      "detail": str(detail)[:300]})
        items = items[-300:]
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
        harden(p, 0o600)
    except OSError:
        pass


def hist_read(uid, limit=60):
    try:
        items = json.loads(hist_file(uid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    out = [{"ts": str(x.get("ts") or ""), "event": str(x.get("event") or ""),
            "detail": str(x.get("detail") or "")}
           for x in items if isinstance(x, dict)]
    return out[-limit:]


EVENT_NAMES = {"registered": "注册", "renewed": "会话续期", "book_ok": "预约成功",
               "book_fail": "预约失败", "book_dead": "未执行(会话失效)",
               "watch_ok": "候补成功", "watch_stop": "候补停止",
               "order_cancelled": "取消订单", "token_reset": "链接重置",
               "checkin_ok": "签到确认", "checkin_bad": "判定爽约",
               "checkin_remind": "签到提醒"}


def cache_orders(uid, orders):
    if orders is None:
        return
    ORDERS_CACHE[uid] = {"orders": orders, "ts": time.time()}


# ---------------- 注册与续期 ----------------
def api_register(body, ip=""):
    sid = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not re.fullmatch(r"\d{5,12}", sid or ""):
        return {"ok": False, "error": "请输入统一认证学号(数字)"}
    if not password or len(password) > 128:
        return {"ok": False, "error": "请输入统一认证密码"}
    if REG_FAIL_ID.blocked("id:" + sid, 2) \
            or REG_FAIL_IP.blocked("ip:" + ip, _client_limit(ip)) \
            or REG_OK_ID.blocked("id:" + sid, 3):
        wlog(f"(web) 注册限流: 学号={sid} ip={ip}")
        return {"ok": False, "error": "尝试过于频繁, 请一小时后再试"}
    d0 = ensure_users()
    owner_sid = next((u.get("student_id") for u in d0["users"]
                      if u["uid"] == 0), "")
    if sid == str(owner_sid or ""):
        return {"ok": False, "error": "该学号是站长账号, 请直接用站长链接访问"}
    try:
        g = pb.load_config(CONFIG_PATH)
    except Exception:
        g = {}
    try:
        s = cas_login_with(sid, password, min(int(g.get("http_timeout", 20)), 10),
                           g.get("proxy", ""))
    except Exception as e:
        kind = classify_login_error(e)
        if kind == "cred":
            REG_FAIL_ID.hit("id:" + sid)
            REG_FAIL_IP.hit("ip:" + ip)
            wlog(f"(web) 注册凭据失败: 学号={sid} ip={ip}")
            return {"ok": False,
                    "error": "统一认证登录未成功, 请检查学号密码(连续失败会临时熔断)"}
        wlog(f"(web) 注册登录异常({kind}): {e.__class__.__name__}: {e}")
        return {"ok": False, "error": "统一认证服务暂时不可用, 请稍后再试"}
    REG_OK_ID.hit("id:" + sid)
    del password  # 密码使命完成(仅存于本栈内存), 之后只保存会话 cookie

    d = ensure_users()
    u = next((x for x in d["users"]
              if x.get("student_id") == sid and x["uid"] != 0), None)
    fresh = u is None
    if fresh:
        uid = int(d.get("next_uid", 1))
        d["next_uid"] = uid + 1
        u = {"uid": uid, "student_id": sid, "label": "",
             "token": secrets.token_hex(12), "role": "member",
             "priority": 100, "enabled": True, "created_at": _now_str()}
        d["users"].append(u)
    else:
        uid = u["uid"]
        # 已注册学号再次验证登录: 重置链接(旧链接作废), 保留计划与优先级
        u["token"] = secrets.token_hex(12)
        u["enabled"] = True
    save_users(d)
    udir = USERS_DIR / str(uid)
    udir.mkdir(parents=True, exist_ok=True)
    harden(udir, 0o700)
    if fresh or load_user_cfg(uid) is None:
        save_user_cfg_raw(uid, default_member_cfg(sid, g))
    save_session_rec(uid, rec_from_session(s))
    DEAD_NOTIFIED.discard(uid)
    hist_add(uid, "registered",
             "注册/重新登录成功" + ("" if fresh else "(个人链接已重置)"))
    wlog(f"(web) 注册成功: uid={uid} 学号={sid} ip={ip}")
    return {"ok": True, "uid": uid, "path": f"/u/{u['token']}/", "fresh": fresh}


def api_renew(uid, body):
    u = get_user(uid)
    if u is None:
        return {"ok": False, "error": "用户不存在"}
    if uid == 0:
        return {"ok": False, "error": "站长会话由系统自动重登, 无需续期"}
    password = str(body.get("password") or "")
    if not password or len(password) > 128:
        return {"ok": False, "error": "请输入统一认证密码"}
    if RENEW_FAIL.blocked(f"uid:{uid}", 3):
        return {"ok": False, "error": "尝试过于频繁, 请一小时后再试"}
    try:
        g = pb.load_config(CONFIG_PATH)
    except Exception:
        g = {}
    try:
        s = cas_login_with(str(u.get("student_id")), password,
                           min(int(g.get("http_timeout", 20)), 10),
                           g.get("proxy", ""))
    except Exception as e:
        kind = classify_login_error(e)
        if kind == "cred":
            RENEW_FAIL.hit(f"uid:{uid}")
            wlog(f"(web) 续期凭据失败: uid={uid}")
            return {"ok": False, "error": "统一认证登录未成功, 请检查密码"}
        wlog(f"(web) 续期登录异常({kind}): {e.__class__.__name__}")
        return {"ok": False, "error": "统一认证服务暂时不可用, 请稍后再试"}
    del password
    save_session_rec(uid, rec_from_session(s))
    DEAD_NOTIFIED.discard(uid)
    hist_add(uid, "renewed", "会话续期成功")
    wlog(f"(web) 会话续期成功: uid={uid}")
    return {"ok": True}


# ---------------- 候补队列(带 uid; 缺 uid 视为站长, 兼容 CLI/旧数据) ----------------
def load_watch_all():
    out = []
    for x in pb.load_watch():
        try:
            uid = int(x.get("uid", 0))
        except (TypeError, ValueError):
            uid = 0
        y = dict(x)
        y["uid"] = uid
        out.append(y)
    return out


def add_watch_uid(uid, date, start, end, source="web"):
    date, start, end = str(date)[:10], str(start), str(end)
    with WATCH_LOCK:
        items = load_watch_all()
        if any(x["uid"] == uid and x["date"] == date and x["start"] == start
               and x["end"] == end for x in items):
            return False
        mine = sum(1 for x in items if x["uid"] == uid)
        if mine >= pb._WATCH_MAX or len(items) >= 60:
            u = get_user(uid)
            pb.log(f"(hub) 候补队列达上限, 忽略: {uname(u)} {date} {start}-{end}")
            return False
        items.append({"uid": uid, "date": date, "start": start, "end": end,
                      "source": source, "added_at": _now_str()})
        items.sort(key=lambda x: (x["date"], x["start"]))
        pb.save_watch(items)
    pb.log(f"(hub) 候补: + uid{uid} {date} {start}-{end} ({source})")
    return True


def remove_watch_uid(uid, date, start=None, end=None):
    with WATCH_LOCK:
        items = load_watch_all()
        kept = [x for x in items
                if not (x["uid"] == uid and x["date"] == str(date)[:10]
                        and (start is None or x["start"] == str(start))
                        and (end is None or x["end"] == str(end)))]
        if len(kept) != len(items):
            pb.save_watch(kept)
            pb.log(f"(hub) 候补: - uid{uid} {date} {start or ''}-{end or ''}, "
                   f"剩 {len(kept)} 条")
        return len(items) - len(kept)


def purge_watch_uid(uid):
    """移除某用户全部候补条目(删除用户时用)。"""
    with WATCH_LOCK:
        items = load_watch_all()
        kept = [x for x in items if x["uid"] != uid]
        if len(kept) != len(items):
            pb.save_watch(kept)
            pb.log(f"(hub) 候补: 清空 uid{uid} 的 {len(items) - len(kept)} 条")
        return len(items) - len(kept)


# ---------------- 共享规则/琴房缓存 ----------------
_SHARED = {"adv_days": None, "adv_open": None, "adv_ts": 0.0,
           "rooms": None, "cmap": None, "campus": None, "rooms_ts": 0.0}


def hub_fetch_shared(s, cfg, force=False):
    if force or _SHARED["adv_days"] is None \
            or time.time() - _SHARED["adv_ts"] > 1800:
        try:
            days, op = pb.load_advance_rule(s, cfg["http_timeout"])
        except Exception:
            days, op = None, None
        if days is None:
            days, op = cfg["advance_days"], cfg["open_time"]
        _SHARED.update(adv_days=days, adv_open=op, adv_ts=time.time(),
                       rooms=None)
    if _SHARED["rooms"] is None or _SHARED["campus"] != cfg["campus"] \
            or time.time() - _SHARED["rooms_ts"] > 600:
        cmap = pb.campus_map(s, cfg["http_timeout"])
        _SHARED["rooms"] = pb.load_rooms(s, cfg, cmap, cfg["http_timeout"])
        _SHARED["cmap"] = cmap
        _SHARED["campus"] = cfg["campus"]
        _SHARED["rooms_ts"] = time.time()
    return (_SHARED["adv_days"], _SHARED["adv_open"],
            _SHARED["rooms"], _SHARED["cmap"])


def rooms_for_user(rooms, cfg_u):
    """按该用户的琴房偏好重排共享房间列表。"""
    prefs = cfg_u.get("room_preference") or []

    def sort_key(r):
        name = r.get("room_name") or ""
        for i, p in enumerate(prefs):
            if p in name:
                return (0, i, name)
        return (1, 99, name)

    return sorted(rooms, key=sort_key)


def _active_orders(orders):
    out = []
    for o in orders:
        st = str(pb.val(o.get("order_status")) or "")
        if st in ("cancelled", "refunded", "rejected", "closed"):
            continue
        out.append(o)
    return out


# ---------------- 预约核心(单目标, 供调度器与网页复用) ----------------
def hub_try_target(user, key, rooms, cmap, s, cfg_u, conflict_cache):
    """对 (date, start, end) 按房间偏好逐间提交一次。
    返回 {status: booked|wait|dead, kind?, room?, order_no?}。"""
    date, st, et = key
    desc = f"{uname(user)} {date} {st}-{et}"
    conflict_total = 0
    occ = pb.get_availability(s, date, rooms, cfg_u["http_timeout"])
    for room in rooms:
        ck = (date, st, et, room.get("formInstId"))
        if ck in conflict_cache:
            conflict_total += 1
            continue
        blk = pb.occupation_block(occ, room, st, et)
        if blk:
            conflict_cache.add(ck)
            conflict_total += 1
            pb.log(f"(hub) {desc} {room.get('room_name')} "
                   f"为管理方占用时段({blk}), 跳过")
            continue
        try:
            code, msg, order = pb.submit_booking(
                s, room, cmap.get(room.get("campus_id")) or cfg_u["campus"],
                date, st, et, cfg_u["http_timeout"])
        except Exception as e:
            pb.log(f"(hub) {desc} {room.get('room_name')} "
                   f"网络异常({e.__class__.__name__})")
            return {"status": "wait", "kind": "neterror"}
        if code == 200 and order:
            room_name = order.get("room_name") or room.get("room_name")
            pb.log(f"(hub) 预约成功! {desc} {room_name} "
                   f"订单 {order.get('order_no')}")
            return {"status": "booked", "room": str(room_name),
                    "order_no": str(order.get("order_no") or "")}
        if any(k in msg for k in pb.MSG_NOT_OPEN):
            return {"status": "wait", "kind": "not_open", "msg": str(msg)}
        if any(k in msg for k in pb.MSG_CONFLICT):
            conflict_cache.add(ck)
            conflict_total += 1
            continue
        if any(k in msg for k in pb.MSG_DAY_LIMIT):
            # 单日累计时长触顶: 该用户当天任何提交都会被拒, 立即返回,
            # 由调用方把当天剩余目标全部放弃(2026-10-23 空耗 2 分钟的教训)
            pb.log(f"(hub) {desc} {room.get('room_name')} "
                   f"当日累计预约时长已达上限({msg})")
            return {"status": "wait", "kind": "daylimit", "msg": str(msg)}
        pb.log(f"(hub) {desc} {room.get('room_name')} "
               f"提交失败(code={code}): {msg}")
        if code == 401 or "登录" in str(msg):
            return {"status": "dead"}
        time.sleep(2)  # 其他错误: 稍息后换下一间
    if rooms and conflict_total >= len(rooms):
        return {"status": "wait", "kind": "conflict"}
    return {"status": "wait", "kind": "other"}


# ---------------- 每天 08:00 的统一调度(接管 piano-booking.timer) ----------------
def _fmt_res(kk, r):
    slot = f"{kk[0]} {kk[1]}-{kk[2]}"
    stt = r.get("status")
    if stt == "booked":
        return f"✅ {slot} {r.get('room', '')}"
    if stt == "already":
        return f"➖ {slot} 已有订单"
    if stt == "limit":
        if r.get("hours"):
            return f"⚠️ {slot} 当日累计已达 2 小时上限"
        return f"⚠️ {slot} 当日已达 2 单上限"
    if stt == "dead":
        return f"🔒 {slot} 会话失效未能预约"
    kind = r.get("kind")
    if kind == "conflict":
        return f"❌ {slot} 约满(已转候补)"
    if kind == "not_open":
        return f"⏳ {slot} 尚未放票"
    return f"❓ {slot} 未完成({kind or '未知'})"


def preflight_users(cfg, tgt):
    """放票前预检: 校验各用户会话可用性(站长可自动重登; 成员失效只能
    飞书提醒站长转告续期——密码不落盘, 系统无法代登)。"""
    users = enabled_users()
    if not users:
        return
    for u in users:
        cfg_u = load_user_cfg(u["uid"])
        if not cfg_u:
            continue
        try:
            tgts = pb.compute_targets(cfg_u)
        except Exception:
            tgts = []
        if not tgts:
            continue  # 明早无计划, 无需会话
        try:
            s = get_session(u["uid"])
            orders = pb.list_my_orders(s, min(cfg_u["http_timeout"], 10))
            cache_orders(u["uid"], orders)
        except NeedsRelogin:
            mark_session_dead(u["uid"], "放票前预检发现会话失效")
            notify_user_event(
                cfg, f"⚠️ 用户 {uname(u)} 的琴房会话已失效, 今晨 "
                     f"{cfg['open_time']} 将无法自动预约({tgts[0][0]} "
                     f"{tgts[0][1]}-{tgts[0][2]} 等)。请尽快通知 TA 打开个人"
                     f"链接, 在「会话与安全」输入密码续期。")
        except Exception as e:
            pb.log(f"(hub) 预检 {uname(u)} 会话校验失败({e.__class__.__name__}), 忽略")
        time.sleep(1.5)


def run_booking_sequence(cfg, deadline):
    """放票时刻的核心调度: 全部用户按优先级轮次预约。

    deadline 兼容两种类型: epoch 秒(float, 测试/CLI 用法)或 datetime
    (scheduler_loop 传 tgt+window 的锚点; 2026-10-17 线上事故: datetime
    直接与 time.time() 比较在放票第一轮即 TypeError, 当天抢票整体未执行,
    且 fired_date 照常落盘不再重试)。
    """
    if isinstance(deadline, datetime):
        deadline = deadline.timestamp()
    with BOOK_LOCK:
        ensure_users()
        users = enabled_users()
        if not users:
            return
        g_timeout = min(int(cfg["http_timeout"]), 12)
        info = {}
        dead = set()

        # 1) 各用户会话与现有订单
        for u in users:
            uid = u["uid"]
            try:
                s = get_session(uid)
            except NeedsRelogin:
                mark_session_dead(uid, "调度开始时会话已失效")
                dead.add(uid)
                info[uid] = {"booked": set(), "daycount": {}, "daymin": {}}
                continue
            except Exception as e:
                pb.log(f"(hub) {uname(u)} 会话不可用({e.__class__.__name__})")
                dead.add(uid)
                info[uid] = {"booked": set(), "daycount": {}, "daymin": {}}
                continue
            try:
                orders = pb.list_my_orders(s, g_timeout)
            except Exception:
                time.sleep(3)
                try:
                    orders = pb.list_my_orders(s, g_timeout)
                except Exception as e2:
                    pb.log(f"(hub) {uname(u)} 订单读取失败({e2.__class__.__name__})")
                    dead.add(uid)
                    info[uid] = {"booked": set(), "daycount": {}, "daymin": {}}
                    continue
            cache_orders(uid, orders)
            act = _active_orders(orders)
            daycount = {}
            for o in act:
                d = (o.get("use_date") or "")[:10]
                if d:
                    daycount[d] = daycount.get(d, 0) + 1
            info[uid] = {"booked": pb.active_booked_keys(act),
                         "daycount": daycount,
                         "daymin": pb.daily_booked_minutes(act)}

        # 2) 共享规则/琴房(取任一可用会话)
        s0 = None
        for u in users:
            if u["uid"] not in dead:
                try:
                    s0 = get_session(u["uid"])
                    break
                except Exception:
                    continue
        if s0 is None:
            pb.log("(hub) 无任何可用会话, 本轮调度中止")
            SCHED["last_result"] = "无可用会话, 调度中止"
            SCHED["last_run"] = _now_str()
            save_hub_state()
            return
        try:
            adv_days, adv_open, rooms, cmap = hub_fetch_shared(s0, cfg)
        except Exception as e:
            pb.log(f"(hub) 规则/琴房读取失败({e.__class__.__name__}), 本轮调度中止")
            SCHED["last_result"] = "规则/琴房读取失败"
            SCHED["last_run"] = _now_str()
            save_hub_state()
            return

        # 3) 各用户待预约目标(跳过已有订单/当日已达 2 单)
        pending = {}
        outcomes = defaultdict(dict)
        lines = []
        for u in users:
            uid = u["uid"]
            cfg_u = load_user_cfg(uid)
            if not cfg_u:
                continue
            try:
                tgts = pb.compute_targets(cfg_u, adv_days)
            except Exception:
                tgts = []
            lst = []
            for key in tgts:
                kk = (key[0], key[1], key[2])
                if kk in info[uid]["booked"]:
                    outcomes[uid][kk] = {"status": "already"}
                    continue
                dmin = info[uid]["daymin"].get(kk[0], 0)
                if info[uid]["daycount"].get(kk[0], 0) >= 2 \
                        or dmin >= pb.DAY_LIMIT_MIN:
                    # 本地预判熔断: 2 单或累计 2 小时, 不发注定被拒的提交
                    outcomes[uid][kk] = {"status": "limit",
                                         "hours": dmin >= pb.DAY_LIMIT_MIN}
                    lines.append(f"{uname(u)} {_fmt_res(kk, outcomes[uid][kk])}")
                    hist_add(uid, "book_fail",
                             f"{kk[0]} {kk[1]}-{kk[2]}: "
                             + ("当日累计预约时长已达 2 小时上限"
                                if outcomes[uid][kk]["hours"]
                                else "当日已达 2 单上限"))
                    continue
                lst.append(kk)
            if lst:
                # 新放票的日期(窗口内最晚一天)开票瞬间最紧俏, 必须先约;
                # 老目标(如已触顶的当天补约)排在后面, 不能挡道
                lst.sort(key=lambda kk: kk[1])
                lst.sort(key=lambda kk: kk[0], reverse=True)
                pending[uid] = lst
        wlog(f"(hub) 放票调度开始: {len(pending)} 人有待预约目标, "
             f"琴房 {len(rooms)} 间")

        # 4) 轮次推进(每轮按优先级序; 同目标 15s 冷却防刷接口)
        #    优先级只决定预约顺序(先约者先挑房间), 绝不取消任何已成功订单
        conflict_cache = set()
        next_try = {}
        while any(pending.values()) and time.time() < deadline:
            for u in users:
                uid = u["uid"]
                keys = pending.get(uid)
                if not keys or uid in dead:
                    continue
                try:
                    s = get_session(uid)
                except NeedsRelogin:
                    dead.add(uid)
                    for kk in keys:
                        outcomes[uid][kk] = {"status": "dead"}
                        lines.append(f"{uname(u)} {_fmt_res(kk, outcomes[uid][kk])}")
                    pending[uid] = []
                    mark_session_dead(uid, "调度中会话失效")
                    continue
                except Exception:
                    for kk in keys:
                        next_try[(uid,) + kk] = time.time() + 6
                    continue
                cfg_u = load_user_cfg(uid)
                if not cfg_u:
                    pending[uid] = []
                    continue
                rooms_u = rooms_for_user(rooms, cfg_u)
                capped = set()  # 服务端确认当日时长触顶的日期, 本轮剩余目标直接放弃
                for kk in list(keys):
                    if time.time() >= deadline:
                        break
                    if kk[0] in capped:
                        continue  # 结果已在下方熔断时记录
                    ck4 = (uid,) + kk
                    if time.time() < next_try.get(ck4, 0):
                        continue
                    r = hub_try_target(u, kk, rooms_u, cmap, s, cfg_u,
                                       conflict_cache)
                    outcomes[uid][kk] = r
                    if r["status"] == "booked":
                        pending[uid].remove(kk)
                        info[uid]["booked"].add(kk)
                        info[uid]["daycount"][kk[0]] = \
                            info[uid]["daycount"].get(kk[0], 0) + 1
                        hist_add(uid, "book_ok",
                                 f"{kk[0]} {kk[1]}-{kk[2]} {r.get('room', '')}")
                        lines.append(f"{uname(u)} {_fmt_res(kk, r)}")
                    elif r["status"] == "dead":
                        if uid == 0:
                            try:
                                get_session(0, force=True)
                                next_try[ck4] = time.time() + 3
                            except Exception:
                                dead.add(0)
                                for k2 in list(keys):
                                    outcomes[uid].setdefault(
                                        k2, {"status": "dead"})
                                    lines.append(f"{uname(u)} "
                                                 f"{_fmt_res(k2, outcomes[uid][k2])}")
                                    hist_add(uid, "book_dead",
                                             f"{k2[0]} {k2[1]}-{k2[2]}: 会话失效")
                                pending[uid] = []
                        else:
                            dead.add(uid)
                            for k2 in keys:
                                outcomes[uid].setdefault(k2, {"status": "dead"})
                                lines.append(f"{uname(u)} {_fmt_res(k2, outcomes[uid][k2])}")
                                hist_add(uid, "book_dead",
                                         f"{k2[0]} {k2[1]}-{k2[2]}: 会话失效")
                            pending[uid] = []
                            mark_session_dead(uid, "调度中会话失效")
                        break
                    elif r.get("kind") == "daylimit":
                        capped.add(kk[0])
                        outcomes[uid][kk] = {"status": "limit", "hours": True}
                        lines.append(
                            f"{uname(u)} {_fmt_res(kk, outcomes[uid][kk])}")
                        hist_add(uid, "book_fail",
                                 f"{kk[0]} {kk[1]}-{kk[2]}: "
                                 f"当日累计预约时长已达 2 小时上限")
                        if kk in pending[uid]:
                            pending[uid].remove(kk)
                        for k2 in list(pending[uid]):
                            if k2[0] == kk[0]:
                                outcomes[uid].setdefault(
                                    k2, {"status": "limit", "hours": True})
                                lines.append(f"{uname(u)} "
                                             f"{_fmt_res(k2, outcomes[uid][k2])}")
                                hist_add(uid, "book_fail",
                                         f"{k2[0]} {k2[1]}-{k2[2]}: "
                                         f"当日累计预约时长已达 2 小时上限")
                                pending[uid].remove(k2)
                    else:
                        cool = {"not_open": 12, "conflict": 15,
                                "other": 8}.get(r.get("kind"), 6)
                        next_try[ck4] = time.time() + cool
                time.sleep(0.5)
            if time.time() < deadline and any(pending.values()):
                time.sleep(2.0 + random.random() * 1.5)

        # 5) 收尾: 约满转候补 + 记录 + 汇总推送(dead 已在轮内即时记录)
        for u in users:
            uid = u["uid"]
            for kk in sorted(outcomes[uid]):
                r = outcomes[uid][kk]
                if r.get("status") in ("booked", "already", "limit", "dead"):
                    continue
                if r.get("status") == "wait" and r.get("kind") == "conflict":
                    add_watch_uid(uid, kk[0], kk[1], kk[2], "auto")
                hist_add(uid, "book_fail",
                         f"{kk[0]} {kk[1]}-{kk[2]}: {_fmt_res(kk, r)}")
                lines.append(f"{uname(u)} {_fmt_res(kk, r)}")
        text = "\n".join(lines) if lines else "全部目标均已有订单, 无需操作"
        SCHED["last_result"] = text
        SCHED["last_run"] = _now_str()
        save_hub_state()
        pb.log("(hub) === 调度结束 ===\n" + text)
        if lines:
            pb.feishu_notify(cfg, "📅 琴房调度结果:\n" + text)


def _sleep_until(cfg, target_dt, step=30):
    """锚定 target_dt 的休眠。勿用 seconds_until 逐轮重算(整点会把目标
    滚到明天, 循环永不退出, 见 piano_booking.wait_until_open 教训)。"""
    while True:
        remain = (target_dt - pb.now_in(cfg)).total_seconds()
        if remain <= 0:
            return
        time.sleep(min(remain, step))


def load_hub_state():
    try:
        d = json.loads(HUB_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            SCHED["fired_date"] = d.get("fired_date") or None
            SCHED["last_result"] = str(d.get("last_result") or "")
            SCHED["last_run"] = str(d.get("last_run") or "")
    except (OSError, ValueError):
        pass


def save_hub_state():
    try:
        tmp = HUB_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(SCHED, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, HUB_STATE_PATH)
    except OSError:
        pass


def scheduler_loop():
    load_hub_state()
    pb.log("(hub) 调度线程启动")
    while True:
        try:
            cfg = pb.load_config(CONFIG_PATH)
            if not cfg.get("scheduler_enabled", True):
                SCHED["next_fire"] = "调度已停用"
                time.sleep(60)
                continue
            window = int(cfg.get("scheduler_active_window", 240))
            now = pb.now_in(cfg)
            oh, om = map(int, str(cfg["open_time"]).split(":"))
            tgt = now.replace(hour=oh, minute=om, second=0, microsecond=0)
            if now >= tgt + timedelta(seconds=window):
                tgt += timedelta(days=1)
            SCHED["next_fire"] = tgt.strftime("%Y-%m-%d %H:%M:%S")
            pre = tgt - timedelta(
                seconds=int(cfg.get("scheduler_lead_seconds", 150)))
            today = tgt.date().isoformat()
            if SCHED.get("fired_date") != today:
                if pb.now_in(cfg) < pre:
                    _sleep_until(cfg, pre, step=20)
                if SCHED.get("fired_date") != today:
                    try:
                        preflight_users(cfg, tgt)
                    except Exception:
                        pb.log("(hub) 会话预检异常:\n" + traceback.format_exc())
                    if pb.now_in(cfg) < tgt:
                        _sleep_until(cfg, tgt, step=1)
                    # 预约幂等(已约目标自动跳过): 崩溃后原地重试一次,
                    # 尽量不在放票窗口内因单次异常整场放弃
                    for attempt in range(2):
                        try:
                            run_booking_sequence(
                                cfg, deadline=tgt + timedelta(seconds=window))
                            break
                        except Exception:
                            pb.log("(hub) 调度异常" +
                                   ("(重试仍失败)" if attempt else ", "
                                    "10s 后原地重试一次") + ":\n"
                                   + traceback.format_exc())
                            time.sleep(10)
                    # fire 标记在跑完后落盘: 中途崩溃重启时会重试
                    # (预约幂等, 已约目标会被跳过), 避免当天漏抢
                    SCHED["fired_date"] = today
                    save_hub_state()
            remain = (tgt + timedelta(seconds=window)
                      - pb.now_in(cfg)).total_seconds()
            if remain > 0:
                time.sleep(min(remain, 120))
        except KeyboardInterrupt:
            raise
        except Exception:
            pb.log("(hub) 调度线程异常:\n" + traceback.format_exc())
            time.sleep(60)


# ---------------- 候补扫描线程(接管 piano-watch, 全用户按优先级) ----------------
_WATCH_ST = {"hb": 0.0}


def _watch_save(removed_keys):
    if not removed_keys:
        return
    with WATCH_LOCK:
        current = load_watch_all()
        final = [x for x in current
                 if (x["uid"], x["date"], x["start"], x["end"])
                 not in removed_keys]
        if len(final) != len(current):
            pb.save_watch(final)
            pb.log(f"(hub) 候补队列更新: 移除 {len(current) - len(final)} 条, "
                   f"剩 {len(final)} 条")


def hub_watch_round(cfg):
    entries = load_watch_all()
    if not entries:
        return
    now = pb.now_in(cfg)
    naive_now = now.replace(tzinfo=None)
    lead = int(cfg.get("watch_stop_lead_min", 120))
    start_lead = int(cfg.get("watch_start_lead_min", 0) or 0)
    users = {u["uid"]: u for u in enabled_users()}
    known_uids = {u["uid"] for u in ensure_users()["users"]}
    removed = set()

    def rkey(x):
        return (x["uid"], x["date"], x["start"], x["end"])

    # 0) 已删除用户的条目清理; 停用用户的条目保留但不参与本轮
    kept = []
    for it in entries:
        if it["uid"] not in known_uids:
            removed.add(rkey(it))
        elif it["uid"] != 0 and it["uid"] not in users:
            continue  # 已停用: 留在队列, 暂不扫描
        else:
            kept.append(it)
    entries = kept

    # 1) 坏日期 / 超时放弃
    for it in list(entries):
        try:
            start_dt = pb.datetime.fromisoformat(f"{it['date']}T{it['start']}")
        except ValueError:
            entries.remove(it)
            removed.add(rkey(it))
            continue
        if naive_now >= start_dt - pb.timedelta(minutes=lead):
            entries.remove(it)
            removed.add(rkey(it))
            u = users.get(it["uid"])
            pb.log(f"(hub) 候补超时停止: {uname(u)} "
                   f"{it['date']} {it['start']}-{it['end']}")
            hist_add(it["uid"], "watch_stop",
                     f"{it['date']} {it['start']}-{it['end']} 临近开始自动放弃")
            pb.feishu_notify(cfg, f"⏰ 琴房候补停止：{uname(u)} "
                                  f"{it['date']} {it['start']}-{it['end']}\n"
                                  f"已临近开始仍未捡到空位，自动放弃。")
    if not entries:
        _watch_save(removed)
        return

    # 2) 用户已有订单(常规预约已覆盖) -> 静默移除; 同时记录当日累计时长
    booked_by = {}
    daymin_by = {}
    for uid in {it["uid"] for it in entries}:
        try:
            s = get_session(uid)
            orders = pb.list_my_orders(s, min(cfg["http_timeout"], 10))
            booked_by[uid] = pb.active_booked_keys(orders)
            daymin_by[uid] = pb.daily_booked_minutes(orders)
            cache_orders(uid, orders)
        except NeedsRelogin:
            mark_session_dead(uid, "候补扫描发现会话失效")
        except Exception:
            pass  # 网络/VPN 抖动: 本轮不核对, 照扫
    for it in list(entries):
        bk = booked_by.get(it["uid"])
        if bk and (it["date"], it["start"], it["end"]) in bk:
            entries.remove(it)
            removed.add(rkey(it))
            pb.log(f"(hub) 候补移除(已有订单): uid{it['uid']} "
                   f"{it['date']} {it['start']}-{it['end']}")
    if not entries:
        _watch_save(removed)
        return

    # 3) 共享规则/琴房(任一可用会话, 站长优先)
    s0 = None
    for uid in [0] + sorted(users):
        try:
            s0 = get_session(uid)
            break
        except Exception:
            continue
    if s0 is None:
        pb.log("(hub) 候补: 无可用会话, 本轮跳过")
        return
    try:
        adv_days, _, rooms, cmap = hub_fetch_shared(s0, cfg)
    except Exception as e:
        pb.log(f"(hub) 候补: 规则读取失败({e.__class__.__name__}), 本轮跳过")
        return
    if not rooms:
        pb.log("(hub) 候补: 无候选琴房, 本轮跳过")
        return

    global _WATCH_ST
    if time.time() - _WATCH_ST["hb"] > 600:
        pb.log(f"(hub) 候补心跳: {len(entries)} 条在队, "
               f"间隔约 {max(30, int(cfg.get('watch_interval', 60)))}s")
        _WATCH_ST["hb"] = time.time()

    # 4) 按优先级逐条提交(原子"测试即预约", 高优先级先捡漏)
    prio = {0: 0}
    prio.update({u["uid"]: int(u.get("priority", 100))
                 for u in users.values()})

    def sort_key(x):
        return (prio.get(x["uid"], 100), x["date"], x["start"])

    for it in sorted(entries, key=sort_key):
        uid = it["uid"]
        u = users.get(uid)
        bk = booked_by.get(uid)
        if bk and (it["date"], it["start"], it["end"]) in bk:
            continue
        if daymin_by.get(uid, {}).get(it["date"], 0) >= pb.DAY_LIMIT_MIN:
            continue  # 该用户当天已触顶: 提交必被拒, 条目保留待其取消订单后再捡
        cfg_u = load_user_cfg(uid)
        if not cfg_u:
            continue
        try:
            use_d = pb.datetime.fromisoformat(it["date"]).date()
        except ValueError:
            continue
        if (use_d - now.date()).days > adv_days:
            continue  # 未放票
        if start_lead:
            try:
                sdt = pb.datetime.fromisoformat(
                    f"{it['date']}T{it['start']}")
            except ValueError:
                continue
            if sdt - naive_now > pb.timedelta(minutes=start_lead):
                continue  # 未到扫描窗口
        try:
            s = get_session(uid)
        except NeedsRelogin:
            mark_session_dead(uid, "候补扫描发现会话失效")
            continue
        except Exception:
            continue
        rooms_u = rooms_for_user(rooms, cfg_u)
        occ = pb.get_availability(s, it["date"], rooms_u,
                                  cfg["http_timeout"])
        i, retried = 0, False
        while i < len(rooms_u):
            room = rooms_u[i]
            i += 1
            if pb.occupation_block(occ, room, it["start"], it["end"]):
                continue  # 管理方占用段: 服务端不拦, 必须自查, 换下一间
            try:
                code, msg, order = pb.submit_booking(
                    s, room, cmap.get(room.get("campus_id"))
                    or cfg_u["campus"],
                    it["date"], it["start"], it["end"],
                    cfg_u["http_timeout"])
            except Exception as e:
                pb.log(f"(hub) 候补 {it['date']} {it['start']} "
                       f"{room.get('room_name')} 网络异常({e.__class__.__name__})")
                time.sleep(3)
                break  # 下轮再试
            if code == 200 and order:
                room_name = order.get("room_name") or room.get("room_name")
                wd = pb.WEEKDAY_NAMES[use_d.isoweekday()]
                pb.log(f"(hub) 候补成功! {uname(u)} {it['date']}({wd}) "
                       f"{it['start']}-{it['end']} {room_name} "
                       f"订单 {order.get('order_no')}")
                removed.add(rkey(it))
                hist_add(uid, "watch_ok",
                         f"{it['date']}({wd}) {it['start']}-{it['end']} "
                         f"{room_name}")
                pb.feishu_notify(
                    cfg, f"🎹 琴房候补成功！\n{uname(u)}\n"
                         f"{it['date']}({wd}) {it['start']}-{it['end']}\n"
                         f"{room_name}\n订单号 {order.get('order_no')}\n"
                         f"门禁密码见各自面板（开场前 15 分钟内显示）")
                break
            if any(k in msg for k in pb.MSG_NOT_OPEN):
                break  # 未放票, 下轮再看
            if any(k in msg for k in pb.MSG_CONFLICT):
                continue  # 该间满, 试下一间
            if any(k in str(msg) for k in pb.MSG_DAY_LIMIT):
                break  # 该用户当天已触顶: 换房间/重试都无用, 也不刷日志
            pb.log(f"(hub) 候补提交异常(code={code}): {msg}")
            if code == 401 or "登录" in str(msg):
                if uid == 0:
                    try:
                        get_session(0, force=True)
                    except Exception:
                        pass
                else:
                    mark_session_dead(uid, "候补扫描会话失效")
                break
            if not retried:  # 偶发 403/网关抖动: 同房间重试一次
                retried = True
                time.sleep(3)
                i -= 1
    _watch_save(removed)


def watch_loop():
    pb.log("(hub) 候补扫描线程启动")
    while True:
        try:
            cfg = pb.load_config(CONFIG_PATH)
            if cfg.get("watch_enabled", True):
                hub_watch_round(cfg)
            interval = max(30, int(cfg.get("watch_interval", 60)))
        except KeyboardInterrupt:
            raise
        except Exception:
            pb.log("(hub) 候补轮异常:\n" + traceback.format_exc())
            interval = 20
        time.sleep(interval + random.uniform(0, max(1.0, interval * 0.2)))


# ---------------- 会话保活线程 ----------------
def keepalive_loop():
    pb.log("(hub) 会话保活线程启动")
    while True:
        try:
            cfg = pb.load_config(CONFIG_PATH)
            if cfg.get("keepalive_enabled", True):
                _keepalive_tick(cfg)
        except KeyboardInterrupt:
            raise
        except Exception:
            pb.log("(hub) 保活线程异常:\n" + traceback.format_exc())
        time.sleep(600)


def _keepalive_tick(cfg):
    """对有近期计划/候补的用户做轻量请求, 保持会话不过期(学校网关约 3h
    踢闲置会话; 成员密码不落盘, 会话一死只能人工续期)。"""
    ka = int(cfg.get("keepalive_minutes", 100)) * 60
    watch_uids = {x["uid"] for x in load_watch_all()}
    todo = []
    for u in enabled_users():
        cfg_u = load_user_cfg(u["uid"])
        if not cfg_u:
            continue
        try:
            has_targets = bool(pb.compute_targets(cfg_u))
        except Exception:
            has_targets = False
        if has_targets or u["uid"] in watch_uids:
            todo.append(u)
    for u in todo:
        uid = u["uid"]
        rec = load_session_rec(uid) or {}
        age = time.time() - float(rec.get("ts") or 0)
        if age < ka:
            continue
        try:
            s = get_session(uid)
            orders = pb.list_my_orders(s, 12)
            cache_orders(uid, orders)
            rec["ts"] = time.time()
            rec.pop("dead", None)
            save_session_rec(uid, rec)
            pb.log(f"(hub) 会话保活: {uname(u)} OK")
        except NeedsRelogin:
            pass  # 已标记失效, 等用户续期
        except RuntimeError as e:
            if "会话" in str(e):
                mark_session_dead(uid, "保活发现会话已失效")
            else:
                pb.log(f"(hub) 会话保活 {uname(u)} 异常: {e}")
        except Exception as e:
            pb.log(f"(hub) 会话保活 {uname(u)} 网络异常({e.__class__.__name__}), 不判死")
        time.sleep(2)


# ---------------- 入场签到监控线程 ----------------
_CHECKIN_ST = {}       # (uid, order_no) -> 上次见到的订单状态
_CHECKIN_REMINDED = set()  # 已发过「仍未签到」提醒的 (uid, order_no), 每单一次


def _checkin_tick(cfg):
    """对今天有预约的用户刷新订单, 捕捉输密码入场后的状态变化:
    waiting_use -> checked_in(已签到) / no_show(爽约)。数据源与官方 App
    同一接口(list_my_orders)。动态所有人记录(面板「动态」可查);
    飞书提醒按既有策略只推站长——站在琴房门口掏出手机即可确认没被判违约。

    另有迟到风险提醒: 开场 remind_min 分钟仍无签到记录时推一次飞书,
    赶在 15 分钟窗口关闭前提醒真人去输密码(签到只能由门禁产生,
    系统不做也不做不了代签; 门禁/密码故障时留时间找管理员补签)。"""
    now = pb.now_in(cfg)
    naive_now = now.replace(tzinfo=None)
    today = now.date().isoformat()
    late_min = int(cfg.get("checkin_late_min", 15) or 15)
    remind_min = int(cfg.get("checkin_remind_min", 5) or 5)
    for u in enabled_users():
        uid = u["uid"]
        oc = ORDERS_CACHE.get(uid) or {}
        if not any((o.get("use_date") or "")[:10] == today
                   for o in oc.get("orders") or []):
            continue  # 缓存里今天没有订单(缓存由面板/调度/保活线程刷新)
        try:
            s = get_session(uid)
            fresh = pb.list_my_orders(
                s, min(int(cfg.get("http_timeout", 20) or 20), 10))
        except NeedsRelogin:
            continue  # 已标失效, 等用户续期
        except Exception:
            continue  # 网络/VPN 抖动: 下轮再看
        cache_orders(uid, fresh)
        for o in _active_orders(fresh):
            if (o.get("use_date") or "")[:10] != today:
                continue
            status = str(pb.val(o.get("order_status")) or "")
            key = (uid, str(o.get("order_no") or ""))
            prev = _CHECKIN_ST.get(key)
            _CHECKIN_ST[key] = status
            room = str(pb.val(o.get("room_name")) or "")
            slot = (f"{today} {o.get('start_time')}-{o.get('end_time')} "
                    f"{room}").strip()
            start_dt = end_dt = None
            try:
                start_dt = datetime.fromisoformat(
                    f"{today}T{(o.get('start_time') or '')[:5]}")
                end_dt = datetime.fromisoformat(
                    f"{today}T{(o.get('end_time') or '')[:5]}")
            except ValueError:
                pass
            if status != prev:
                if status in CHECKIN_DONE:
                    hist_add(uid, "checkin_ok", f"{slot} 已确认入场签到")
                    if uid == 0:
                        pb.feishu_notify(
                            cfg, f"✅ 琴房签到确认\n{slot}\n系统已记录你的入场"
                                 f"签到，本次预约有效，无违约风险。")
                elif status == "no_show":
                    if prev is None and end_dt and naive_now > end_dt:
                        pass  # 重启后首轮见到的是历史状态, 不补报
                    else:
                        hist_add(uid, "checkin_bad", f"{slot} 已被判定爽约")
                        if uid == 0:
                            pb.feishu_notify(
                                cfg, f"❌ 琴房爽约判定\n{slot}\n系统未在签到"
                                     f"窗口内检测到你的入场；如实际已入场，"
                                     f"请尽快联系管理员补签。")
            # 迟到风险提醒(每单只发一次): 仍在签到窗口内才发, 窗口已过
            # 再发就只剩坏消息没有补救时间了
            if status in ("waiting_use", "booked") and start_dt \
                    and start_dt + timedelta(minutes=remind_min) <= naive_now \
                    < start_dt + timedelta(minutes=late_min) \
                    and key not in _CHECKIN_REMINDED:
                _CHECKIN_REMINDED.add(key)
                hist_add(uid, "checkin_remind",
                         f"{slot} 开场已超 {remind_min} 分钟仍未检测到签到")
                if uid == 0:
                    pb.feishu_notify(
                        cfg, f"⏰ 琴房签到提醒\n{slot}\n开场已超过 {remind_min} "
                             f"分钟，系统还没检测到你的入场签到。请在开场 "
                             f"{late_min} 分钟内输入门禁密码完成签到，否则会"
                             f"被判爽约；如已在琴房请确认门禁是否正常，"
                             f"异常时尽快联系管理员补签。")


def checkin_loop():
    pb.log("(hub) 签到监控线程启动")
    while True:
        try:
            cfg = pb.load_config(CONFIG_PATH)
            if cfg.get("checkin_watch_enabled", True):
                _checkin_tick(cfg)
            interval = max(60, int(cfg.get("checkin_interval", 180)))
        except KeyboardInterrupt:
            raise
        except Exception:
            pb.log("(hub) 签到监控异常:\n" + traceback.format_exc())
            interval = 120
        time.sleep(interval)


# ---------------- API: 状态/计划/预约/取消/候补 ----------------
def api_state(uid=0):
    """读取状态; 死会话自动重试。成员会话失效时返回 needs_relogin
    提示(密码不落盘, 系统无法代为重登)。"""
    last = None
    for i in range(2):
        try:
            return _api_state_once(uid)
        except NeedsRelogin as e:
            if uid != 0:
                mark_session_dead(uid, "面板读取发现会话失效")
                return {"ok": False, "error": str(e), "needs_relogin": True}
            raise
        except Exception as e:
            last = e
            wlog(f"(web) state 读取失败(uid={uid}, "
                 f"{e.__class__.__name__}: {e}), 重试 {i + 1}/2")
            if uid != 0 and "会话" in str(e):
                mark_session_dead(uid, str(e))
            try:
                get_session(uid, force=True)
            except Exception as e2:
                wlog(f"(web) 刷新会话失败({e2.__class__.__name__}: {e2})")
            time.sleep(2)
    if uid != 0 and last is not None and "会话" in str(last):
        return {"ok": False, "error": str(last), "needs_relogin": True}
    raise last


def _api_state_once(uid=0):
    u = get_user(uid) or {"uid": uid, "role": "owner", "priority": 0,
                          "student_id": "", "label": "站长"}
    cfg = load_user_cfg(uid)
    if cfg is None:
        raise RuntimeError("用户配置缺失")
    cfg["http_timeout"] = min(cfg["http_timeout"], 10)
    s = get_session(uid)
    adv_days, adv_open = pb.load_advance_rule(s, cfg["http_timeout"])
    if adv_days is None:
        adv_days, adv_open = cfg["advance_days"], cfg["open_time"]
    orders = pb.list_my_orders(s, cfg["http_timeout"])
    cache_orders(uid, orders)
    booked = pb.active_booked_keys(orders)
    now = pb.now_in(cfg)
    passwords = _door_passwords(s, orders, now, cfg["http_timeout"],
                                late_min=int(cfg.get("checkin_late_min", 15)
                                             or 15))

    skip_dates, dated_dates = set(), set()
    for t in cfg["targets"]:
        if t.get("date"):
            (skip_dates if t.get("skip") else dated_dates).add(str(t["date"]))

    targets = []
    for i, t in enumerate(cfg["targets"]):
        st, et = t.get("start"), t.get("end")
        if t.get("date"):
            try:
                d0 = datetime.fromisoformat(str(t["date"])).date()
            except ValueError:
                continue
            delta = (d0 - now.date()).days
            label = str(t["date"])
            kind = "skip" if t.get("skip") else "date"
            if kind == "skip":
                targets.append({"index": i, "kind": "skip", "label": label,
                                "date": str(t["date"]), "start": "", "end": "",
                                "next_date": label, "delta": delta,
                                "in_window": False,
                                "expired": delta < 0, "booked": False,
                                "suppressed": False})
                continue
        else:
            wd = int(t["weekday"])
            delta = (wd - now.isoweekday()) % 7
            d0 = (now + timedelta(days=delta)).date()
            label = f"每周{pb.WEEKDAY_NAMES[wd]}"
            kind = "weekly"
        next_date = d0.isoformat()
        expired = bool(t.get("date")) and delta < 0
        passed = delta == 0 and st and now > now.replace(
            hour=int(st.split(":")[0]), minute=int(st.split(":")[1]),
            second=0, microsecond=0) - timedelta(minutes=30)
        in_window = (0 <= delta <= adv_days) and not passed
        suppressed = (kind == "weekly"
                      and (next_date in skip_dates
                           or next_date in dated_dates))
        targets.append({
            "index": i, "kind": kind, "label": label,
            "weekday": t.get("weekday"), "date": t.get("date"),
            "start": st or "", "end": et or "",
            "next_date": next_date, "delta": delta,
            "in_window": in_window and not expired and not suppressed,
            "expired": expired,
            "booked": (next_date, st, et) in booked if st else False,
            "suppressed": suppressed,
        })
    order_kind = {"skip": 0, "date": 1, "weekly": 2}
    targets.sort(key=lambda x: (order_kind[x["kind"]], x["next_date"],
                                x["start"]))

    order_list = []
    for o in orders:
        status = str(pb.val(o.get("order_status")) or "")
        d = (o.get("use_date") or "")[:10]
        order_list.append({
            "order_no": o.get("order_no"), "room": o.get("room_name"),
            "date": d, "start": o.get("start_time"), "end": o.get("end_time"),
            "status": status,
        })
    order_list.sort(key=lambda x: (x["date"], x["start"] or ""), reverse=True)

    drop_remain = pb.seconds_until(cfg, *map(int, cfg["open_time"].split(":")))
    if drop_remain < 0:
        drop_remain += 86400
    h, rem = divmod(int(drop_remain), 3600)

    g = {}
    try:
        g = pb.load_config(CONFIG_PATH)
    except Exception:
        pass
    rec = load_session_rec(uid) or {}
    return {
        "ok": True,
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": pb.WEEKDAY_NAMES[now.isoweekday()],
        "rules": {"advance_days": adv_days, "open_time": adv_open,
                  "campus": cfg["campus"],
                  "rooms": "、".join(cfg["room_preference"][:6])},
        "drop_countdown": f"{h}小时{rem // 60:02d}分",
        "targets": targets,
        "orders": order_list[:15],
        "passwords": passwords,
        "watch": [x for x in load_watch_all() if x["uid"] == uid],
        "watch_cfg": {"stop_min": int(g.get("watch_stop_lead_min", 120)),
                      "start_min": int(g.get("watch_start_lead_min", 0) or 0)},
        "uid": uid,
        "admin": uid == 0,
        "role": u.get("role"),
        "label": str(u.get("label") or ("站长" if uid == 0 else "")),
        "student_id": str(u.get("student_id") or ""),
        "priority": int(u.get("priority", 0) or 0),
        "session": {"alive": not rec.get("dead"),
                    "note": str(rec.get("dead_note") or "")},
    }


def validate_target(t):
    """校验并规范化一条计划, 返回 (ok, 规范化条目或错误信息)。

    琴房按整点开放预约, 时长只允许 1 或 2 小时(2026-10-27 起,
    与官方规则一致; 新增/保存计划时统一拦截半小时步长)。
    """
    def ptime(v):
        parts = str(v).split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h < 24 and m == 0):
            return None
        return f"{h:02d}:00"

    def check(st, et):
        if not st or not et:
            return False, "时间格式有误(需整点 HH:00)"
        h1, h2 = int(st.split(":")[0]), int(et.split(":")[0])
        if h2 - h1 not in (1, 2):
            return False, "时长需为 1 或 2 小时(整点起止)"
        return True, ""

    if "date" in t and t.get("date"):
        try:
            datetime.fromisoformat(str(t["date"]))
        except ValueError:
            return False, "日期格式有误"
        if t.get("skip"):
            return True, {"date": str(t["date"]), "skip": True}
        st, et = ptime(t.get("start")), ptime(t.get("end"))
        ok, err = check(st, et)
        if not ok:
            return False, err
        return True, {"date": str(t["date"]), "start": st, "end": et}
    st, et = ptime(t.get("start")), ptime(t.get("end"))
    ok, err = check(st, et)
    if not ok:
        return False, err
    try:
        wd = int(t.get("weekday"))
    except (TypeError, ValueError):
        return False, "weekday 需为 1-7"
    if not 1 <= wd <= 7:
        return False, "weekday 需为 1-7"
    return True, {"weekday": wd, "start": st, "end": et}


def api_targets_replace(body, uid=0):
    items = body.get("targets")
    if not isinstance(items, list):
        return {"ok": False, "error": "缺少 targets 数组"}
    normalized, seen = [], set()
    for t in items[:50]:
        ok, r = validate_target(t)
        if not ok:
            return {"ok": False, "error": r}
        key = json.dumps(r, sort_keys=True)
        if key not in seen:
            seen.add(key)
            normalized.append(r)
    if uid == 0:
        with CFG_LOCK:
            cfg = pb.load_config(CONFIG_PATH)
            cfg["targets"] = normalized
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
    else:
        cfg = load_user_cfg(uid)
        if cfg is None:
            return {"ok": False, "error": "用户配置缺失"}
        cfg["targets"] = normalized
        save_user_cfg_raw(uid, cfg)
    wlog(f"(web) 计划已更新(uid={uid}), 共 {len(normalized)} 条")
    return {"ok": True, "count": len(normalized)}


def web_book_once(uid, target, g):
    """网页手动预约一条计划(即时返回, 不长占请求)。"""
    cfg_u = load_user_cfg(uid)
    if cfg_u is None:
        return {"status": "error", "error": "用户配置缺失"}
    try:
        s = get_session(uid)
    except NeedsRelogin as e:
        return {"status": "dead", "error": str(e), "needs_relogin": True}
    now = pb.now_in(cfg_u)
    if target.get("date"):
        key = (str(target["date"]), str(target["start"]), str(target["end"]))
    else:
        delta = (int(target["weekday"]) - now.isoweekday()) % 7
        key = ((now + timedelta(days=delta)).date().isoformat(),
               str(target["start"]), str(target["end"]))
    try:
        _, _, rooms, cmap = hub_fetch_shared(s, cfg_u)
    except Exception as e:
        return {"status": "error", "error": f"读取预约数据失败: {e}"}
    rooms_u = rooms_for_user(rooms, cfg_u)
    conflict_cache = set()
    deadline = time.time() + 20
    r = None
    while time.time() < deadline:
        r = hub_try_target(get_user(uid) or {"uid": uid}, key, rooms_u, cmap,
                           s, cfg_u, conflict_cache)
        r["key"] = key
        if r["status"] in ("booked", "dead") or r.get("kind") == "conflict":
            break
        time.sleep(6)
    return r


def api_book(body, uid=0):
    cfg_u = load_user_cfg(uid)
    if cfg_u is None:
        return {"ok": False, "error": "用户配置缺失"}
    idx = body.get("index")
    if idx is None or not isinstance(cfg_u["targets"], list) \
            or not (0 <= int(idx) < len(cfg_u["targets"])):
        return {"ok": False, "error": "无效的计划编号"}
    target = cfg_u["targets"][int(idx)]
    ok, r = validate_target(target)
    if not ok:
        return {"ok": False, "error": r}
    if target.get("skip"):
        return {"ok": False, "error": "跳过类计划无需预约"}
    try:
        g = pb.load_config(CONFIG_PATH)
    except Exception:
        g = {}
    with BOOK_LOCK:
        log_before = pb.LOG_FILE.stat().st_size if pb.LOG_FILE.exists() else 0
        res = web_book_once(uid, target, g)
        tail = ""
        try:
            with open(pb.LOG_FILE, "r", encoding="utf-8",
                      errors="replace") as f:
                f.seek(log_before)
                tail = f.read()
        except OSError:
            pass
    lines = [ln.split("] ", 1)[-1] for ln in tail.strip().splitlines()
             if ln.strip()
             and not ln.split("] ", 1)[-1].startswith("=====")]
    if res.get("status") == "dead":
        if uid != 0:
            mark_session_dead(uid, "手动预约发现会话失效")
        return {"ok": False,
                "error": res.get("error") or "会话已失效, 请先续期",
                "needs_relogin": True}
    if res.get("status") == "error":
        return {"ok": False, "error": res.get("error") or "预约失败"}
    if res.get("status") == "booked":
        hist_add(uid, "book_ok", f"{res.get('room', '')} (手动预约)")
    elif res.get("kind") == "conflict":
        kk = res.get("key") or ("", "", "")
        add_watch_uid(uid, kk[0], kk[1], kk[2], "auto")
    return {"ok": True, "log": lines[-8:],
            "result": {k: res.get(k) for k in
                       ("status", "kind", "room", "order_no") if k in res}}


CANCELLABLE = ("waiting_use", "pending_approval")
# no_show(爽约)不再排除: 门禁密码卡片要展示「已判定爽约」供当场核对
PW_INACTIVE = ("cancelled", "refunded", "rejected", "closed")
CHECKIN_DONE = ("checked_in", "completed")  # 官方状态: 已签到/已完成


def api_watch_add(body, uid=0):
    """把一条计划的下一次时段加入候补队列(约满自动捡漏)。"""
    cfg_u = load_user_cfg(uid)
    if cfg_u is None:
        return {"ok": False, "error": "用户配置缺失"}
    idx = body.get("index")
    if idx is None or not isinstance(cfg_u["targets"], list) \
            or not (0 <= int(idx) < len(cfg_u["targets"])):
        return {"ok": False, "error": "无效的计划编号"}
    target = cfg_u["targets"][int(idx)]
    ok, r = validate_target(target)
    if not ok:
        return {"ok": False, "error": r}
    if target.get("skip"):
        return {"ok": False, "error": "跳过类计划无需候补"}
    now = pb.now_in(cfg_u)
    if target.get("date"):
        date = str(target["date"])
    else:
        delta = (int(target["weekday"]) - now.isoweekday()) % 7
        date = (now + timedelta(days=delta)).date().isoformat()
    queued = add_watch_uid(uid, date, target["start"], target["end"], "web")
    wlog(f"(web) 加入候补(uid={uid}): {date} {target['start']}-"
         f"{target['end']}")
    return {"ok": True, "queued": queued,
            "watch": {"date": date, "start": target["start"],
                      "end": target["end"]}}


def api_watch_remove(body, uid=0):
    date = str(body.get("date") or "")[:10]
    if not date:
        return {"ok": False, "error": "缺少日期"}
    n = remove_watch_uid(uid, date, body.get("start"), body.get("end"))
    wlog(f"(web) 停止候补(uid={uid}): {date} {body.get('start')}-"
         f"{body.get('end')} (移除 {n} 条)")
    return {"ok": True, "removed": n}


def api_watch_config(body):
    """设置候补扫描窗口: 开始前 stop_min 分钟停止, start_min 分钟才开始。
    全局配置(影响所有用户的扫描), 仅管理员。"""
    stop, start = body.get("stop_min"), body.get("start_min")
    for v in (stop, start):
        if not isinstance(v, int) or isinstance(v, bool) \
                or not (0 <= v <= 31 * 1440):
            return {"ok": False, "error": "扫描窗口数值无效"}
    if start and start < stop:
        return {"ok": False,
                "error": "开始扫描的提前量需不小于停止扫描的提前量"}
    set_cfg_keys({"watch_stop_lead_min": stop,
                  "watch_start_lead_min": start})
    wlog(f"(web) 候补扫描窗口更新: 开始前 {start or 0}min 开始扫描, "
         f"开始前 {stop}min 停止")
    return {"ok": True}


def _door_passwords(s, orders, now, timeout, late_min=15):
    """已约订单的门禁密码与入场签到状态(官方入场凭证同源数据)。

    权限记录按 order_id(=订单 instanceId)或 order_no 匹配; 密码缺失时
    依权限状态与使用时段给出提示。只保留今天及以后的订单, 按时间升序。
    签到状态: 输密码入场后订单 waiting_use -> checked_in(已签到); 开始后
    late_min 分钟内未签到会被学校端判 no_show(爽约)——每条附带 checkin
    字段(done/pending/late/upcoming/violation)供面板当场确认违约风险。
    """
    dates = sorted({(o.get("use_date") or "")[:10] for o in orders
                    if (o.get("use_date") or "")[:10]}
                   | {now.date().isoformat()})
    try:
        perms = pb.list_access_permissions(s, dates, timeout)
    except Exception as e:
        wlog(f"(web) 门禁权限读取失败({e.__class__.__name__})")
        return []

    by_key = {}
    for p in perms:
        for k in (str(pb.val(p.get("order_id")) or ""),
                  str(pb.val(p.get("order_no")) or "")):
            if k:
                by_key.setdefault(k, p)

    today = now.date().isoformat()
    naive_now = now.replace(tzinfo=None)
    items = []
    for o in orders:
        status = str(pb.val(o.get("order_status")) or "")
        if status in PW_INACTIVE:
            continue
        d = (o.get("use_date") or "")[:10]
        if d and d < today:
            continue  # 已过去的时段, 密码不再展示
        perm = by_key.get(str(o.get("instanceId") or "")) \
            or by_key.get(str(pb.val(o.get("order_no")) or ""))
        pstat = str(pb.val((perm or {}).get("permission_status")) or "")
        pwd = str(pb.val((perm or {}).get("entrance_pwd"))
                  or pb.val(o.get("entrance_pwd")) or "")
        campus = str(pb.val(o.get("campus_name")) or "")
        st, et = (o.get("start_time") or "")[:5], (o.get("end_time") or "")[:5]

        start_dt = end_dt = None
        if d and st and et:
            try:
                start_dt = datetime.fromisoformat(f"{d}T{st}")
                end_dt = datetime.fromisoformat(f"{d}T{et}")
            except ValueError:
                pass
        if end_dt and naive_now > end_dt + timedelta(minutes=30):
            continue  # 时段已结束半小时, 密码与签到状态不再展示

        note = ""
        if "玉泉" in campus:
            note = "该校区刷脸/刷卡入场，无需密码"
        elif pstat == "failed":
            note = "门禁权限下发失败，请联系管理员"
        elif pstat in ("revoked", "expired"):
            note = "门禁权限已回收"
        elif not pwd:
            if start_dt and naive_now < start_dt - timedelta(minutes=15):
                note = "开场前 15 分钟内开放，可稍后刷新"
            else:
                note = "密码下发中，请稍候刷新"

        # 入场签到状态(与官方 App 订单状态同源)
        checkin, cbadge, cnote = "upcoming", "gr", ""
        if status in CHECKIN_DONE:
            checkin, cbadge = "done", "g"
            cnote = "✅ 系统已确认你的入场签到，本次预约有效，无违约风险"
        elif status == "no_show":
            checkin, cbadge = "violation", "r"
            cnote = "❌ 已被判定爽约；如实际已入场，请尽快联系管理员补签"
        elif start_dt and naive_now > start_dt + timedelta(minutes=late_min):
            checkin, cbadge = "late", "r"
            cnote = f"⚠️ 已过签到窗口(开始后 {late_min} 分钟)仍未检测到入场，" \
                    f"如已入场请尽快联系管理员补签"
        elif start_dt and naive_now >= start_dt:
            checkin, cbadge = "pending", "o"
            cnote = f"签到窗口内(开始后 {late_min} 分钟内)：输入密码后门禁会" \
                    f"自动匹配签到，稍等 1-2 分钟点「查签到」确认"
        elif status == "pending_approval":
            cnote = "审批通过后，开场后 15 分钟内输密码即完成签到"
        elif d == now.date().isoformat():
            cnote = f"开场后 {late_min} 分钟内输密码即完成签到，" \
                    f"可点「查签到」确认无违约"

        items.append({
            "order_no": str(pb.val(o.get("order_no")) or ""),
            "room": str(pb.val(o.get("room_name")) or ""),
            "date": d, "start": st, "end": et,
            "entry": "face_card" if "玉泉" in campus else "password",
            "permission_status": pstat or "pending",
            "password": pwd, "note": note,
            "order_status": status,
            "checkin": checkin, "checkin_badge": cbadge,
            "checkin_note": cnote,
        })
    items.sort(key=lambda x: (x["date"], x["start"]))
    return items


def api_cancel(body, uid=0):
    order_no = str(body.get("order_no") or "").strip()
    if not order_no:
        return {"ok": False, "error": "缺少订单号"}
    cfg_u = load_user_cfg(uid)
    if cfg_u is None:
        return {"ok": False, "error": "用户配置缺失"}
    with BOOK_LOCK:
        try:
            s = get_session(uid)
        except NeedsRelogin as e:
            return {"ok": False, "error": str(e), "needs_relogin": True}
        orders = pb.list_my_orders(s, cfg_u["http_timeout"])
        order = next((o for o in orders if o.get("order_no") == order_no),
                     None)
        if not order:
            return {"ok": False, "error": "未找到该订单(请刷新后重试)"}
        status = str(pb.val(order.get("order_status")) or "")
        if status not in CANCELLABLE:
            return {"ok": False,
                    "error": f"订单状态为 {status or '未知'}, 无需取消"}
        log_before = pb.LOG_FILE.stat().st_size if pb.LOG_FILE.exists() else 0
        ok, msg = pb.cancel_order(s, order, timeout=cfg_u["http_timeout"],
                                  ignore_door_errors=True)
        tail = ""
        try:
            with open(pb.LOG_FILE, "r", encoding="utf-8",
                      errors="replace") as f:
                f.seek(log_before)
                tail = f.read()
        except OSError:
            pass
    lines = [ln.split("] ", 1)[-1] for ln in tail.strip().splitlines()
             if ln.strip()]
    if ok:
        hist_add(uid, "order_cancelled",
                 f"{(order.get('use_date') or '')[:10]} "
                 f"{order.get('start_time')}-{order.get('end_time')} "
                 f"{pb.val(order.get('room_name')) or order.get('room_name')}")
    wlog(f"(web) 取消订单(uid={uid}) {order_no}: {'成功' if ok else '失败'} - {msg}")
    return {"ok": ok, "message": msg, "log": lines[-8:]}


# ---------------- 管理 API(仅站长) ----------------
def api_admin_overview():
    d = ensure_users()
    try:
        g = pb.load_config(CONFIG_PATH)
    except Exception:
        g = {}
    entries = load_watch_all()
    today = pb.now_in(g).date().isoformat()
    out = []
    for u in sorted(d["users"],
                    key=lambda x: (int(x.get("priority", 100)), x["uid"])):
        uid = u["uid"]
        cfg_u = load_user_cfg(uid) if u.get("enabled") else None
        nxt = ""
        if cfg_u:
            try:
                tg = pb.compute_targets(cfg_u)
                if tg:
                    nxt = f"{tg[0][0]} {tg[0][1]}-{tg[0][2]}"
            except Exception:
                pass
        rec = load_session_rec(uid) or {}
        oc = ORDERS_CACHE.get(uid) or {}
        upcoming = []
        for o in _active_orders(oc.get("orders") or []):
            dd = (o.get("use_date") or "")[:10]
            if dd and dd >= today:
                upcoming.append(
                    f"{dd} {o.get('start_time')}-{o.get('end_time')} "
                    f"{pb.val(o.get('room_name')) or o.get('room_name')}")
        synced = ""
        ts = oc.get("ts")
        if ts:
            synced = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        h = hist_read(uid, 1)
        out.append({
            "uid": uid,
            "student_id": str(u.get("student_id") or ""),
            "label": str(u.get("label") or ""),
            "role": u.get("role"),
            "priority": int(u.get("priority", 100)),
            "enabled": bool(u.get("enabled")),
            "session_alive": not rec.get("dead"),
            "next_target": nxt,
            "watch_n": sum(1 for x in entries if x["uid"] == uid),
            "upcoming": upcoming[:4],
            "orders_synced": synced,
            "last_event": (EVENT_NAMES.get(h[0]["event"], h[0]["event"])
                           + " " + h[0]["detail"]) if h else "",
        })
    rt = reg_token()
    return {"ok": True, "users": out,
            "reg_path": f"/reg/{rt}/",  # 页面按自身路径推导外部前缀后拼完整链接
            "sched": {"next_fire": SCHED.get("next_fire", ""),
                      "fired_date": SCHED.get("fired_date") or "",
                      "last_run": SCHED.get("last_run", ""),
                      "last_result": SCHED.get("last_result", "")},
            "watch_cfg": {"stop_min": int(g.get("watch_stop_lead_min", 120)),
                          "start_min": int(g.get("watch_start_lead_min", 0) or 0)}}


def api_admin_user_state(uid):
    u = get_user(uid)
    if u is None:
        return {"ok": False, "error": "用户不存在"}
    out = {"ok": True,
           "user": {k: u.get(k) for k in
                    ("uid", "student_id", "label", "role", "priority",
                     "enabled", "created_at", "token")}}
    try:
        out["state"] = _api_state_once(uid)
    except NeedsRelogin as e:
        out["state"] = {"ok": False, "needs_relogin": True, "error": str(e)}
    except Exception as e:
        out["state"] = {"ok": False, "error": f"读取失败: {e}"}
    out["history"] = hist_read(uid, 40)
    return out


def api_admin_priority(body):
    u = get_user(body.get("uid"))
    if u is None or u["role"] == "owner":
        return {"ok": False, "error": "无效用户(站长优先级固定为 0)"}
    try:
        prio = int(body.get("priority"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "优先级需为 1-999 的整数(越小越优先)"}
    if not 1 <= prio <= 999:
        return {"ok": False, "error": "优先级需为 1-999 的整数(越小越优先)"}
    d = ensure_users()
    for x in d["users"]:
        if x["uid"] == u["uid"]:
            x["priority"] = prio
    save_users(d)
    wlog(f"(web) 管理员调整优先级: {uname(u)} -> {prio}")
    return {"ok": True}


def api_admin_label(body):
    u = get_user(body.get("uid"))
    if u is None:
        return {"ok": False, "error": "用户不存在"}
    label = str(body.get("label") or "").strip()[:16]
    d = ensure_users()
    for x in d["users"]:
        if x["uid"] == u["uid"]:
            x["label"] = label
    save_users(d)
    return {"ok": True}


def api_admin_enable(body):
    u = get_user(body.get("uid"))
    if u is None or u["role"] == "owner":
        return {"ok": False, "error": "无效用户(站长不可停用)"}
    flag = bool(body.get("enabled"))
    d = ensure_users()
    for x in d["users"]:
        if x["uid"] == u["uid"]:
            x["enabled"] = flag
    save_users(d)
    wlog(f"(web) 管理员{'启用' if flag else '停用'}用户: {uname(u)}")
    return {"ok": True}


def api_admin_remove(body):
    u = get_user(body.get("uid"))
    if u is None or u["role"] == "owner":
        return {"ok": False, "error": "无效用户(站长不可删除)"}
    d = ensure_users()
    d["users"] = [x for x in d["users"] if x["uid"] != u["uid"]]
    save_users(d)
    purge_watch_uid(u["uid"])
    SESSIONS.pop(u["uid"], None)
    ORDERS_CACHE.pop(u["uid"], None)
    shutil.rmtree(USERS_DIR / str(u["uid"]), ignore_errors=True)
    wlog(f"(web) 管理员移除用户: {uname(u)}(其在学校系统中的已有订单不受影响)")
    return {"ok": True}


def api_admin_regen_reg():
    t = secrets.token_hex(12)
    REG_TOKEN_PATH.write_text(t, encoding="utf-8")
    harden(REG_TOKEN_PATH, 0o600)
    wlog("(web) 注册链接已重置(旧链接作废)")
    return {"ok": True}


def api_admin_regen_token(body):
    u = get_user(body.get("uid"))
    if u is None or u["role"] == "owner":
        return {"ok": False, "error": "无效用户"}
    tok = secrets.token_hex(12)
    d = ensure_users()
    for x in d["users"]:
        if x["uid"] == u["uid"]:
            x["token"] = tok
    save_users(d)
    hist_add(u["uid"], "token_reset", "站长重置了个人链接(旧链接作废)")
    wlog(f"(web) 管理员重置用户链接: {uname(u)}")
    return {"ok": True, "token": tok}


def api_admin_watch_remove(body):
    try:
        uid = int(body.get("uid"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "无效用户"}
    date = str(body.get("date") or "")[:10]
    if not date:
        return {"ok": False, "error": "缺少日期"}
    n = remove_watch_uid(uid, date, body.get("start"), body.get("end"))
    return {"ok": True, "removed": n}


def hub_health():
    try:
        n = len(ensure_users()["users"])
    except Exception:
        n = 0
    try:
        w = len(pb.load_watch())
    except Exception:
        w = 0
    return {"ok": True, "service": "piano-hub", "users": n, "watch": w,
            "next_fire": SCHED.get("next_fire", ""),
            "fired_date": SCHED.get("fired_date") or ""}


# ---------------- 页面 ----------------
PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<link rel="icon" href="data:,">
<title>琴房预约助手</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c1e21;--sub:#65676b;--pri:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:15px;padding:12px;max-width:520px;margin:0 auto;padding-bottom:80px}
h1{font-size:19px;margin:6px 2px 2px}
.sub{color:var(--sub);font-size:12.5px;margin:0 2px 12px}
.sub a{color:var(--pri);text-decoration:none;font-weight:600}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card h2{font-size:15px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.row{display:flex;align-items:center;gap:10px;padding:10px 4px;border-top:1px solid #f0f0f2;flex-wrap:wrap}
.row:first-of-type{border-top:none}
.row .main{flex:1;min-width:150px}
.t1{font-weight:600}
.t2{color:var(--sub);font-size:12.5px;margin-top:2px}
.badge{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:20px;background:#eef2ff;color:var(--pri);margin-right:4px}
.badge.g{background:#ecfdf5;color:var(--ok)}.badge.o{background:#fffbeb;color:var(--warn)}.badge.r{background:#fef2f2;color:var(--bad)}.badge.gr{background:#f3f4f6;color:var(--sub)}
button{border:none;border-radius:9px;padding:9px 14px;font-size:14px;background:#eef2ff;color:var(--pri);font-weight:600}
button.danger{background:#fef2f2;color:var(--bad)}
button.pri{background:var(--pri);color:#fff}
button:active{opacity:.7}
select,input{padding:9px;border:1px solid #d9dbe0;border-radius:9px;font-size:15px;background:#fff;color:var(--ink);max-width:100%}
label.ck{display:flex;align-items:center;gap:6px;font-size:13.5px;color:var(--sub);margin:6px 0}
.grid{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.grid>*{flex:1;min-width:100px}
.tabs{display:flex;gap:6px;margin-bottom:10px}
.tabs button{flex:1;background:#f3f4f6;color:var(--sub)}
.tabs button.on{background:var(--pri);color:#fff}
.empty{color:var(--sub);text-align:center;padding:14px 0;font-size:13.5px}
.wcrow{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--sub);margin-bottom:8px;flex-wrap:wrap}
.wcrow select{padding:5px 6px;font-size:13px;width:auto;min-width:0}
.pwd{font-size:26px;font-weight:700;letter-spacing:5px;color:var(--ok);font-variant-numeric:tabular-nums;user-select:all}
.pwdbox{display:flex;align-items:center;gap:8px}
.tip{font-size:12px;color:var(--sub);margin-top:8px;line-height:1.5}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#111827ee;color:#fff;padding:11px 18px;border-radius:10px;font-size:14px;max-width:88%;display:none;z-index:9}
</style></head><body>
<h1>🎹 琴房预约助手</h1>
<p class="sub" id="meta">加载中…</p>
<p class="sub" id="who"></p>

<div class="card" id="sessCard" style="display:none"><h2>🛡 会话与安全 <span id="sessState"></span></h2>
<div class="grid"><input type="password" id="fPwd" placeholder="输入统一认证密码续期" autocomplete="off"></div>
<button style="width:100%" onclick="renew()">重新登录续期</button>
<div class="tip">本服务<b>不会保存你的密码</b>：密码只用于当场统一认证登录，服务器仅保存登录后的会话。会话失效（红色）时自动预约和候补都会暂停，需在此重新输入密码续期。建议约到票后顺手看一眼这里的颜色。</div></div>

<div class="card"><h2>🔑 门禁密码 <span><button onclick="load()">刷新</button> <button onclick="checkCheckin()">我已入场·查签到</button></span></h2><div id="pwds"></div>
<div class="tip">实时取自官方入场凭证，与 App 显示一致；密码由门禁系统生成，最迟开场前 15 分钟可用。紫金港琴房输入密码或扫入场码进入。<b>开场后 15 分钟内输密码即自动签到</b>（超时会判爽约）；输完密码点「我已入场·查签到」，出现「已签到 ✓」即确认系统没有判定违约。</div></div>

<div class="card"><h2>我的预约 <button onclick="load()">刷新</button></h2><div id="orders"></div>
<div class="tip">点「取消」即可取消预约（需在开始前 30 分钟以上）。若门禁撤销失败会自动继续完成取消，不影响订单。</div></div>

<div class="card"><h2>预约计划 <button onclick="showAdd()">+ 添加</button></h2>
<div id="targets"></div>
<div class="tip">每天 <b id="ruleOpen">08:00</b> 放出 <b id="ruleAdv">3</b> 天内的新票，服务器会按<b>优先级</b>依次为大家自动抢票。加一条「单次日期」计划会自动<b>替代当天</b>的每周计划；想跳过某天就加一条「该天不约」。约满的计划会<b>自动转入候补</b>。</div></div>

<div class="card"><h2>🔍 候补中</h2>
<div class="wcrow" id="wcAdmin">提前 <select id="wcStart" onchange="saveWc()"></select> 开始扫描 · 提前 <select id="wcStop" onchange="saveWc()"></select> 停止</div>
<div id="watches"></div>
<div class="tip">候补时段由服务器约每分钟自动扫描一次（按优先级顺序），一旦有琴房被取消空出，立即自动补约并<b>飞书推送通知</b>。</div></div>

<div class="card" id="addCard" style="display:none">
<h2>添加计划</h2>
<div class="tabs">
<button id="tabWeekly" onclick="setTab('weekly')">每周重复</button>
<button id="tabDate" onclick="setTab('date')">指定日期</button></div>
<div class="grid" id="dateWrap" style="display:none"><input type="date" id="fDate"></div>
<div class="grid" id="wdWrap"><select id="fWd"></select></div>
<label class="ck" id="skipWrap" style="display:none"><input type="checkbox" id="fSkip"> 这天不约（跳过自动预约）</label>
<div class="grid" id="timeWrap">
<select id="fStart"></select>
<select id="fDur"><option value="60">1 小时</option><option value="120" selected>2 小时</option></select>
</div>
<button class="pri" style="width:100%" onclick="addTarget()">添加计划</button>
<div class="tip">琴房整点开放预约：开始时间只能选整点，时长 1 或 2 小时，时段 08:00-22:00。</div>
</div>

<div id="toast"></div>
<script>
let S=null, tab='weekly', failCnt=0;
const WD={1:'周一',2:'周二',3:'周三',4:'周四',5:'周五',6:'周六',7:'周日'};
const $=id=>document.getElementById(id);
function toast(m,ms=2600){const t=$('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms)}
async function api(p,body){const r=await fetch('api/'+p,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
function stBadge(s){const m={'waiting_use':['待使用','g'],'pending_approval':['待审批','o'],'checked_in':['已签到','g'],'completed':['已完成','g'],'cancelled':['已取消','gr'],'no_show':['爽约','r'],'refunded':['已退款','gr'],'rejected':['已拒绝','r']};const x=m[s]||[s,'gr'];return `<span class="badge ${x[1]}">${x[0]}</span>`}
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function renderRole(){
  const w=$('who');if(!w)return;
  if(S.admin){w.innerHTML='管理员 · <a href="admin/">🛠 打开总控制台</a>'}
  else{w.textContent=(S.label?('你好, '+S.label+' · '):'')+'优先级 '+(S.priority!=null?S.priority:'-')+'（数字越小越优先）'}
  const sc=$('sessCard');if(sc)sc.style.display=S.admin?'none':'block';
  const wa=$('wcAdmin');if(wa)wa.style.display=S.admin?'flex':'none';
}
function renderSess(){
  if(S.admin)return;
  const el=$('sessState');if(!el)return;
  const alive=S.session&&S.session.alive;
  el.innerHTML=alive?'<span class="badge g">会话正常</span>':'<span class="badge r">会话已失效·请续期</span>';
}
async function renew(){
  const p=$('fPwd').value;if(!p){toast('请输入统一认证密码');return}
  toast('统一认证登录中…',10000);
  const r=await api('renew',{password:p});
  $('fPwd').value='';
  if(r.ok){toast('续期成功');load()}else toast(r.error||'续期失败',4000);
}
const CK={done:['已签到 ✓','g'],pending:['等待签到记录','o'],late:['未检测到签到','r'],violation:['已判定爽约','r']};
function renderPwds(list){
  const el=$('pwds');
  if(!list.length){el.innerHTML='<div class="empty">暂无有效预约，抢到票后密码会显示在这里</div>';return}
  el.innerHTML=list.map(o=>{
    const ck=CK[o.checkin];
    const ckb=ck?`<span class="badge ${ck[1]}">${ck[0]}</span>`:'';
    const right=o.password
      ?`<div class="pwdbox"><span class="pwd">${esc(o.password)}</span><button data-p="${esc(o.password)}" onclick="copyPwd(this.dataset.p)">复制</button></div>`
      :`<span class="badge gr">${esc(o.note||'密码下发中')}</span>`;
    const sub=esc(o.room)+(ckb?' · '+ckb:'')+(o.password&&o.note?' · '+esc(o.note):'');
    const extra=o.checkin_note?`<div class="t2">${esc(o.checkin_note)}</div>`:'';
    return `<div class="row"><div class="main"><div class="t1">${esc(o.date)} ${esc(o.start)}-${esc(o.end)}</div><div class="t2">${sub}</div>${extra}</div>${right}</div>`;
  }).join('');
}
async function checkCheckin(){
  toast('正在核对你的签到状态…',8000);
  await load();
  const ps=(S.passwords||[]).filter(o=>o.checkin&&o.checkin!=='upcoming');
  const done=ps.filter(o=>o.checkin==='done');
  if(done.length){toast('✅ 已确认签到: '+done.map(o=>(o.start||'')+' '+(o.room||'')).join('；')+'，无违约风险',6000);return}
  if(ps.some(o=>o.checkin==='violation')){toast('❌ 有预约被判定爽约；如实际已入场请尽快联系管理员补签',6000);return}
  if(ps.some(o=>o.checkin==='late')){toast('⚠️ 已过签到窗口仍未检测到入场；如已入场请尽快联系管理员补签',6000);return}
  if(ps.some(o=>o.checkin==='pending')){toast('⏳ 在签到窗口内但暂未看到签到记录，输完密码等 1~2 分钟再点一次',6000);return}
  toast('当前没有待签到的预约');
}
function copyText(p,ok){
  const done=()=>toast('已复制');
  const fb=()=>{const t=document.createElement('textarea');t.value=p;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();let c=false;try{c=document.execCommand('copy')}catch(e){}t.remove();c?done():toast('复制失败，请长按手动复制',3000)};
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(p).then(done).catch(fb)}else fb();
}
function copyPwd(p){copyText(p);toast('已复制: '+p)}
const WC_START={0:'立即',60:'1 小时',120:'2 小时',360:'6 小时',720:'12 小时',1440:'1 天',2880:'2 天',4320:'3 天'};
const WC_STOP={30:'30 分钟',60:'1 小时',120:'2 小时',180:'3 小时',240:'4 小时',360:'6 小时',720:'12 小时',1440:'1 天'};
function fillWc(sel,map,val){
  if(val==null||!(val in map)){const o=document.createElement('option');o.value=val==null?120:val;o.textContent=(val==null?120:val)+' 分钟';sel.appendChild(o)}
  for(const k in map){const o=document.createElement('option');o.value=k;o.textContent=map[k];sel.appendChild(o)}
  sel.value=val;
}
function renderWc(){
  if(!S.watch_cfg)return;
  const cs=$('wcStart'),ct=$('wcStop');
  if(document.activeElement!==cs)fillWc(cs,WC_START,S.watch_cfg.start_min);
  if(document.activeElement!==ct)fillWc(ct,WC_STOP,S.watch_cfg.stop_min);
}
async function saveWc(){
  const r=await api('watch_config',{start_min:+$('wcStart').value,stop_min:+$('wcStop').value});
  if(r.ok)toast('扫描窗口已更新');else toast(r.error||'保存失败',3500);
}
function renderWatches(list){
  const el=$('watches');
  if(!list.length){el.innerHTML='<div class="empty">暂无候补。可在计划上点「候补」手动加入，或等抢票约满后自动进入</div>';return}
  el.innerHTML=list.map(w=>`<div class="row"><div class="main"><div class="t1">${esc(w.date)} ${esc(w.start)}-${esc(w.end)}</div><div class="t2">${w.source==='web'?'手动添加':'约满自动加入'} · ${esc(w.added_at||'')}</div></div><span class="badge o">扫描中</span><button class="danger" onclick="unwatch('${esc(w.date)}','${esc(w.start)}','${esc(w.end)}')">停止</button></div>`).join('');
}
async function watchTarget(i){toast('提交中…',8000);const r=await api('watch',{index:i});if(r.ok){toast('已加入候补, 约满将自动捡漏');load()}else toast(r.error||'失败',3500)}
async function unwatch(d,s,e){const r=await api('unwatch',{date:d,start:s,end:e});if(r.ok){toast('已停止候补');load()}else toast(r.error||'失败')}
function render(){
  if(S.now)$('meta').textContent=`${S.now} ${S.weekday} · 下次放票约 ${S.drop_countdown} 后`;
  if(S.rules){$('ruleOpen').textContent=S.rules.open_time;$('ruleAdv').textContent=S.rules.advance_days}
  const orders=S.orders||[];
  const oEl=$('orders');
  if(!orders.length){oEl.innerHTML='<div class="empty">暂无预约记录</div>'}
  else{oEl.innerHTML=orders.map(o=>{
    const cc=o.status==='waiting_use'||o.status==='pending_approval';
    return `<div class="row"><div class="main"><div class="t1">${o.date} ${o.start}-${o.end}</div>
    <div class="t2">${o.room||''} · ${o.order_no||''}</div></div>${stBadge(o.status)}${cc?`<button class="danger" onclick="cancelOrder('${o.order_no}')">取消</button>`:''}</div>`}).join('')}
  const targets=S.targets||[];
  const tEl=$('targets');
  if(!targets.length){tEl.innerHTML='<div class="empty">还没有计划, 点右上角 + 添加</div>'}
  else{tEl.innerHTML=targets.map(t=>{
    let state, t1;
    if(t.kind==='skip'){t1=`<span class="t1">${t.label} 不约</span>`;state='<span class="badge gr">跳过该天</span>'}
    else{
      t1=`<span class="t1">${t.kind==='weekly'?'每周'+WD[t.weekday]:t.label} ${t.start}-${t.end}</span>`;
      if(t.expired) state='<span class="badge gr">已过期</span>';
      else if(t.booked) state='<span class="badge g">已约到</span>';
      else if(t.suppressed) state='<span class="badge gr">当天已由其他计划替代</span>';
      else if(t.in_window) state=`<span class="badge o">可约 ${t.next_date}</span>`;
      else state=`<span class="badge gr">等放票 ${t.next_date}</span>`;
    }
    const bk=(t.kind!=='skip'&&!t.expired&&!t.booked&&!t.suppressed)?`<button onclick="bookTarget(${t.index})">立即预约</button>`:'';
    const wt=(t.kind!=='skip'&&!t.expired&&!t.booked)?`<button onclick="watchTarget(${t.index})">候补</button>`:'';
    return `<div class="row"><div class="main">${t1}<div class="t2">${t.kind==='skip'?'该天自动预约将被跳过':'最近一次: '+t.next_date}</div></div>${state}${bk}${wt}
    <button class="danger" onclick="delTarget(${t.index})">删除</button></div>`}).join('')}
  renderWc();
  renderPwds(S.passwords||[]);
  renderWatches(S.watch||[]);
  renderRole();
  renderSess();
}
async function load(){try{const r=await api('state');if(!r||!r.ok){
  if(r&&r.needs_relogin){S=Object.assign({targets:[],orders:[],watch:[],passwords:[],session:{alive:false},admin:false},r);render();toast('会话已失效, 请在「会话与安全」续期',4000);failCnt=0;setTimeout(load,60000);return}
  throw new Error((r&&r.error)||'状态异常')}
 S=r;failCnt=0;render()}catch(e){failCnt++;if(failCnt>=3)toast('网络波动, 自动重试中…',2500);setTimeout(load,5000)}}
function planOf(t){if(t.kind==='skip')return{date:t.date,skip:true};if(t.kind==='date')return{date:t.date,start:t.start,end:t.end};return{weekday:t.weekday,start:t.start,end:t.end}}
async function addTarget(){
  const b={};
  if(tab==='weekly'){b.weekday=+$('fWd').value}
  else{b.date=$('fDate').value;if(!b.date){toast('请选择日期');return}}
  if($('fSkip').checked&&tab==='date'){b.skip=true}
  else{b.start=$('fStart').value;const mins=+$('fDur').value,[h,m]=b.start.split(':').map(Number);
    const e=new Date(2000,0,1,h,m+mins);b.end=String(e.getHours()).padStart(2,'0')+':'+String(e.getMinutes()).padStart(2,'0')}
  const cur=(S.targets||[]).map(planOf);cur.push(b);
  const r=await api('targets',{targets:cur});if(r.ok){toast('已添加');hideAdd();load()}else toast(r.error||'失败',3500)}
async function delTarget(i){if(!confirm('确定删除这条计划? (已约到的订单不受影响)'))return;const cur=(S.targets||[]).filter(t=>t.index!==i).map(planOf);const r=await api('targets',{targets:cur});if(r.ok){toast('已删除');load()}else toast(r.error||'失败')}
async function bookTarget(i){toast('提交中, 请稍候…',15000);const r=await api('book',{index:i});
  if(r.needs_relogin){toast('会话已失效, 请先续期',4000);load();return}
  if(r.ok){const res=r.result||{};let m='完成';
    if(res.status==='booked')m='✅ 已约上: '+(res.room||'');
    else if(res.kind==='conflict')m='约满, 已自动转入候补';
    else if(res.kind==='not_open')m='尚未放票';
    else m='暂时未能约上, 可稍后重试或点「候补」';
    toast(m,5000);if(r.log)console.log(r.log)}
  else toast(r.error||'失败',3500);load()}
async function cancelOrder(no){
  if(!confirm('确定取消这条预约? 时段会立即释放; 请在开始前 30 分钟以上操作。'))return;
  toast('取消中, 请稍候…',12000);
  const r=await api('cancel',{order_no:no});
  if(r.ok)toast(r.message||'已取消',5000);else toast(r.error||r.message||'取消失败',4500);
  load()}
function setTab(k){tab=k;$('tabWeekly').classList.toggle('on',k==='weekly');$('tabDate').classList.toggle('on',k==='date');$('wdWrap').style.display=k==='weekly'?'flex':'none';$('dateWrap').style.display=k==='date'?'flex':'none';$('skipWrap').style.display=k==='date'?'block':'none';$('timeWrap').style.display=(k==='weekly'||!$('fSkip').checked)?'flex':'none'}
function showAdd(){$('addCard').style.display='block';$('addCard').scrollIntoView({behavior:'smooth'})}
function hideAdd(){$('addCard').style.display='none'}
(function init(){
  const wd=$('fWd');for(let i=1;i<=7;i++){const o=document.createElement('option');o.value=i;o.textContent='每'+WD[i];wd.appendChild(o)}
  const st=$('fStart');for(let h=8;h<=21;h++){const v=String(h).padStart(2,'0')+':00';const o=document.createElement('option');o.value=v;o.textContent=v;st.appendChild(o)}
  $('fDate').min=new Date().toISOString().slice(0,10);
  $('fSkip').addEventListener('change',()=>setTab(tab));
  setTab('weekly');load();setInterval(load,60000);
})();
</script></body></html>"""


PAGE_ADMIN = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<link rel="icon" href="data:,">
<title>琴房总控制台</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c1e21;--sub:#65676b;--pri:#2563eb;--ok:#16a34a;--warn:#d97706;--bad:#dc2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:15px;padding:12px;max-width:560px;margin:0 auto;padding-bottom:80px}
h1{font-size:19px;margin:6px 2px 2px}
h3.sh{font-size:13.5px;color:var(--sub);margin:12px 0 2px;border-bottom:1px solid #f0f0f2;padding-bottom:4px}
.sub{color:var(--sub);font-size:12.5px;margin:0 2px 12px}
.sub a{color:var(--pri);text-decoration:none;font-weight:600}
.card{background:var(--card);border-radius:14px;padding:14px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card h2{font-size:15px;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.row{display:flex;align-items:center;gap:8px;padding:10px 4px;border-top:1px solid #f0f0f2;flex-wrap:wrap}
.row:first-of-type{border-top:none}
.row .main{flex:1;min-width:150px}
.t1{font-weight:600}
.t2{color:var(--sub);font-size:12.5px;margin-top:2px}
.badge{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:20px;background:#eef2ff;color:var(--pri);margin-right:4px}
.badge.g{background:#ecfdf5;color:var(--ok)}.badge.o{background:#fffbeb;color:var(--warn)}.badge.r{background:#fef2f2;color:var(--bad)}.badge.gr{background:#f3f4f6;color:var(--sub)}
button{border:none;border-radius:9px;padding:8px 12px;font-size:13.5px;background:#eef2ff;color:var(--pri);font-weight:600}
button.danger{background:#fef2f2;color:var(--bad)}
button.pri{background:var(--pri);color:#fff}
button:active{opacity:.7}
select,input{padding:8px;border:1px solid #d9dbe0;border-radius:9px;font-size:14px;background:#fff;color:var(--ink);max-width:100%}
input.pr{width:64px;padding:6px;text-align:center}
.wcrow{display:flex;align-items:center;gap:6px;font-size:12.5px;color:var(--sub);margin:8px 0;flex-wrap:wrap}
.wcrow select{padding:5px 6px;font-size:13px;width:auto;min-width:0}
.pwd{font-size:22px;font-weight:700;letter-spacing:4px;color:var(--ok);font-variant-numeric:tabular-nums;user-select:all}
.empty{color:var(--sub);text-align:center;padding:14px 0;font-size:13.5px}
pre.res{background:#f8f9fb;border-radius:9px;padding:9px;font-size:12px;white-space:pre-wrap;word-break:break-all;color:#374151;max-height:180px;overflow:auto}
.tip{font-size:12px;color:var(--sub);margin-top:8px;line-height:1.5}
code{background:#f3f4f6;border-radius:6px;padding:2px 6px;font-size:12.5px;word-break:break-all}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#111827ee;color:#fff;padding:11px 18px;border-radius:10px;font-size:14px;max-width:88%;display:none;z-index:9}
</style></head><body>
<h1>🛠 琴房总控制台</h1>
<p class="sub"><a href="./">← 返回我的控制台</a> · 仅站长可见</p>

<div class="card"><h2>⚙️ 统一调度</h2>
<div class="t2" id="schedInfo">加载中…</div>
<pre class="res" id="lastResult">尚无调度记录</pre>
<div class="wcrow">候补扫描: 提前 <select id="wcStart" onchange="saveWc()"></select> 开始 · 提前 <select id="wcStop" onchange="saveWc()"></select> 停止</div>
<div class="tip">优先级只决定抢票/捡漏的先后顺序（站长恒最优先），<b>绝不会取消任何用户已成功的订单</b>。所有飞书通知只发站长配置的群，成员端不推送、只在各自面板看结果。</div>
</div>

<div class="card"><h2>🔗 注册链接 <button onclick="regen()">重新生成</button></h2>
<div class="row"><div class="main"><div class="t2" id="regLink">…</div></div><button onclick="copyReg()">复制</button></div>
<div class="tip">同学打开链接, 输入统一认证学号密码验证后获得个人专属链接。<b>密码不会被保存</b>(仅当场验证一次, 服务器只存登录后的会话)。防爆破: 学号 1 小时错 2 次或同一 IP 错 10 次会临时熔断。重新生成后旧注册链接立即作废。</div></div>

<div class="card"><h2>👥 用户 <button onclick="load()">刷新</button></h2><div id="users"></div>
<div class="tip">优先级数字越小越先抢票/捡漏(站长恒为 0)。点「详情」可查看 TA 的全部计划/订单/门禁密码/候补/动态, 并可代约代取消。删除用户不影响其在学校系统中的已有订单。</div></div>

<div class="card" id="detailCard" style="display:none"><h2 id="detailTitle">详情</h2><div id="detail"></div></div>

<div id="toast"></div>
<script>
let A=null;
const PREFIX=location.pathname.replace(/\\/t\\/[^\\/]+\\/admin\\/?$/,'');
const WD={1:'周一',2:'周二',3:'周三',4:'周四',5:'周五',6:'周六',7:'周日'};
const EV={'registered':'注册','renewed':'会话续期','book_ok':'预约成功','book_fail':'预约失败','book_dead':'未执行(会话失效)','watch_ok':'候补成功','watch_stop':'候补停止','order_cancelled':'取消订单','token_reset':'链接重置','checkin_ok':'签到确认','checkin_bad':'判定爽约','checkin_remind':'签到提醒'};
const $=id=>document.getElementById(id);
function toast(m,ms=2600){const t=$('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms)}
async function api(p,body){const r=await fetch('../api/'+p,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});return r.json()}
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function stBadge(s){const m={'waiting_use':['待使用','g'],'pending_approval':['待审批','o'],'checked_in':['已签到','g'],'completed':['已完成','g'],'cancelled':['已取消','gr'],'no_show':['爽约','r'],'refunded':['已退款','gr'],'rejected':['已拒绝','r']};const x=m[s]||[s,'gr'];return `<span class="badge ${x[1]}">${x[0]}</span>`}
function copyText(p){const done=()=>toast('已复制');const fb=()=>{const t=document.createElement('textarea');t.value=p;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();let c=false;try{c=document.execCommand('copy')}catch(e){}t.remove();c?done():toast('复制失败, 请长按手动复制',3000)};if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(p).then(done).catch(fb)}else fb()}
const WC_START={0:'立即',60:'1 小时',120:'2 小时',360:'6 小时',720:'12 小时',1440:'1 天',2880:'2 天',4320:'3 天'};
const WC_STOP={30:'30 分钟',60:'1 小时',120:'2 小时',180:'3 小时',240:'4 小时',360:'6 小时',720:'12 小时',1440:'1 天'};
function fillWc(sel,map,val){
  if(val==null||!(val in map)){const o=document.createElement('option');o.value=val==null?120:val;o.textContent=(val==null?120:val)+' 分钟';sel.appendChild(o)}
  for(const k in map){const o=document.createElement('option');o.value=k;o.textContent=map[k];sel.appendChild(o)}
  sel.value=val;
}
function unameOf(u){return u.label||u.student_id||('uid'+u.uid)}
function render(){
  if(!A)return;
  const sc=A.sched||{};
  $('schedInfo').innerHTML=`下次调度 <b>${esc(sc.next_fire||'-')}</b> · 上次运行 ${esc(sc.last_run||'未运行')||'未运行'} · 用户 ${A.users.length} 人`;
  $('lastResult').textContent=sc.last_result||'尚无调度记录';
  $('regLink').textContent=location.origin+PREFIX+(A.reg_path||'');
  fillWc($('wcStart'),WC_START,(A.watch_cfg||{}).start_min);
  fillWc($('wcStop'),WC_STOP,(A.watch_cfg||{}).stop_min);
  const el=$('users');
  if(!A.users.length){el.innerHTML='<div class="empty">还没有用户, 把注册链接分享给同学吧</div>';return}
  el.innerHTML=A.users.map(u=>{
    const owner=u.role==='owner';
    const sess=!u.enabled?'<span class="badge gr">已停用</span>':(u.session_alive?'<span class="badge g">会话正常</span>':'<span class="badge r">会话失效</span>');
    return `<div class="row"><div class="main"><div class="t1">${esc(unameOf(u))} ${owner?'<span class="badge">站长</span>':''}${u.enabled?'':'<span class="badge gr">停用</span>'}</div>
    <div class="t2">${esc(u.student_id)} · 优先级 ${u.priority} · 下次计划 ${esc(u.next_target||'-')} · 候补 ${u.watch_n} 条</div>
    <div class="t2">${esc((u.upcoming||[]).join(' ; ')||'近期无订单(以同步 '+esc(u.orders_synced||'-')+' 为准)')}</div>
    ${u.last_event?`<div class="t2">最近: ${esc(u.last_event)}</div>`:''}</div>
    ${sess}
    ${owner?'':`<input class="pr" id="pr${u.uid}" value="${u.priority}" title="优先级"><button onclick="savePr(${u.uid})">保存</button>
    <button onclick="toggleEn(${u.uid},${u.enabled})">${u.enabled?'停用':'启用'}</button>`}
    <button onclick="openDetail(${u.uid})">详情</button>
    ${owner?'':`<button class="danger" onclick="removeU(${u.uid},'${esc(unameOf(u))}')">删除</button>`}</div>`}).join('');
}
let aFail=0;
async function load(){try{const r=await api('admin/overview');if(!r||!r.ok)throw new Error((r&&r.error)||'状态异常');A=r;aFail=0;render()}catch(e){aFail++;if(aFail>=3)$('schedInfo').textContent='读取失败('+((e&&e.message)||e)+'), 每5秒自动重试…';setTimeout(load,5000)}}
async function savePr(uid){const v=parseInt($('pr'+uid).value,10);if(!(v>=1&&v<=999)){toast('优先级需为 1-999');return}const r=await api('admin/priority',{uid,priority:v});if(r.ok){toast('优先级已更新');load()}else toast(r.error||'失败',3500)}
async function toggleEn(uid,en){const r=await api('admin/enable',{uid,enabled:!en});if(r.ok){toast(!en?'已启用':'已停用(其候补与调度暂停)');load()}else toast(r.error||'失败')}
async function removeU(uid,name){if(!confirm('确定移除 '+name+' ?\\n其个人链接/计划/候补将被删除; 其在学校系统中的已有订单不受影响。'))return;const r=await api('admin/remove',{uid});if(r.ok){toast('已移除');if($('detailCard').style.display!=='none')closeDetail();load()}else toast(r.error||'失败')}
async function regen(){if(!confirm('重新生成注册链接? 旧链接立即作废。'))return;const r=await api('admin/regen_reg',{});if(r.ok){toast('注册链接已重置');load()}else toast(r.error||'失败')}
function copyReg(){copyText($('regLink').textContent)}
async function saveWc(){const r=await api('watch_config',{start_min:+$('wcStart').value,stop_min:+$('wcStop').value});if(r.ok)toast('扫描窗口已更新');else toast(r.error||'保存失败',3500)}
async function openDetail(uid){const dc=$('detailCard');dc.style.display='block';$('detail').innerHTML='<div class="empty">加载中…</div>';dc.scrollIntoView({behavior:'smooth'});const r=await api('admin/user_state?uid='+uid);if(!r||!r.ok){$('detail').innerHTML='<div class="empty">读取失败</div>';return}renderDetail(r)}
function closeDetail(){$('detailCard').style.display='none'}
function renderDetail(r){
  const u=r.user, st=r.state||{};
  $('detailTitle').innerHTML=`${esc(u.label||u.student_id)} 的详情 <button onclick="closeDetail()">收起</button>`;
  let h='';
  h+='<h3 class="sh">预约计划</h3>';
  const ts=st.targets||[];
  h+=ts.length?ts.map(t=>{
    const t1=t.kind==='skip'?`${esc(t.label)} 不约`:`${t.kind==='weekly'?'每周'+WD[t.weekday]:esc(t.label)} ${t.start}-${t.end}`;
    const state=t.kind==='skip'?'跳过':(t.expired?'已过期':(t.booked?'已约到':(t.in_window?`可约 ${t.next_date}`:`等放票 ${t.next_date}`)));
    const bk=(t.kind!=='skip'&&!t.expired&&!t.booked)?`<button onclick="adminBook(${u.uid},${t.index})">立即预约</button>`:'';
    return `<div class="row"><div class="main"><div class="t1">${t1}</div><div class="t2">最近一次: ${t.next_date}</div></div><span class="badge gr">${state}</span>${bk}</div>`}).join(''):'<div class="empty">无计划</div>';
  h+='<h3 class="sh">订单</h3>';
  const os=st.orders||[];
  h+=os.length?os.map(o=>{const cc=o.status==='waiting_use'||o.status==='pending_approval';
    return `<div class="row"><div class="main"><div class="t1">${o.date} ${o.start}-${o.end}</div><div class="t2">${esc(o.room||'')} · ${esc(o.order_no||'')}</div></div>${stBadge(o.status)}${cc?`<button class="danger" onclick="adminCancel(${u.uid},'${o.order_no}')">取消</button>`:''}</div>`}).join(''):'<div class="empty">无订单</div>';
  h+='<h3 class="sh">门禁密码</h3>';
  const ps=st.passwords||[];
  const CK={done:['已签到 ✓','g'],pending:['等待签到记录','o'],late:['未检测到签到','r'],violation:['已判定爽约','r']};
  h+=ps.length?ps.map(o=>{const ck=CK[o.checkin];const ckb=ck?`<span class="badge ${ck[1]}">${ck[0]}</span>`:'';
    return `<div class="row"><div class="main"><div class="t1">${o.date} ${o.start}-${o.end}</div><div class="t2">${esc(o.room||'')}${ckb?' · '+ckb:''}</div>${o.checkin_note?`<div class="t2">${esc(o.checkin_note)}</div>`:''}</div>${o.password?`<span class="pwd">${esc(o.password)}</span>`:`<span class="badge gr">${esc(o.note||'暂无')}</span>`}</div>`}).join(''):'<div class="empty">无</div>';
  h+='<h3 class="sh">候补中</h3>';
  const ws=st.watch||[];
  h+=ws.length?ws.map(w=>`<div class="row"><div class="main"><div class="t1">${w.date} ${w.start}-${w.end}</div><div class="t2">${esc(w.source==='web'?'手动添加':'约满自动加入')} · ${esc(w.added_at||'')}</div></div><span class="badge o">扫描中</span><button class="danger" onclick="adminUnwatch(${u.uid},'${esc(w.date)}','${esc(w.start)}','${esc(w.end)}')">停止</button></div>`).join(''):'<div class="empty">无</div>';
  h+='<h3 class="sh">动态</h3>';
  const hs=(r.history||[]).slice().reverse();
  h+=hs.length?hs.map(x=>`<div class="t2">${esc(x.ts)} · ${esc(EV[x.event]||x.event)} ${esc(x.detail||'')}</div>`).join(''):'<div class="empty">无</div>';
  if(u.role!=='owner'){
    h+=`<div class="row" style="border-top:none"><button onclick="regenUser(${u.uid})">重置 TA 的个人链接</button><button onclick="prLbl(${u.uid})">改备注名</button></div>
    <div class="tip">重置后旧链接作废, 新链接(复制转发给 TA): <code id="newLink${u.uid}">…</code></div>`;
  }
  $('detail').innerHTML=h;
}
async function adminBook(uid,index){toast('提交中…',12000);const r=await api('admin/book',{uid,index});
  if(r.ok){const res=r.result||{};toast(res.status==='booked'?('✅ 已约上: '+(res.room||'')):(res.kind==='conflict'?'约满, 已自动转候补':'未完成, 见动态'),5000);openDetail(uid)}else toast(r.error||'失败',3500)}
async function adminCancel(uid,no){if(!confirm('确定取消该订单? 请在开始前 30 分钟以上操作。'))return;toast('取消中…',12000);const r=await api('admin/cancel',{uid,order_no:no});toast(r.ok?(r.message||'已取消'):(r.error||'失败'),4500);openDetail(uid)}
async function adminUnwatch(uid,d,s,e){const r=await api('admin/watch_remove',{uid,date:d,start:s,end:e});if(r.ok){toast('已停止候补');openDetail(uid)}else toast(r.error||'失败')}
async function regenUser(uid){if(!confirm('重置该用户的个人链接? 旧链接立即作废。'))return;const r=await api('admin/regen_token',{uid});if(r.ok){const url=new URL('../../u/'+r.token+'/',location.href).href;$('newLink'+uid).textContent=url;toast('已重置')}else toast(r.error||'失败')}
async function prLbl(uid){const v=prompt('备注名(显示用, 最长 16 字, 留空清除):');if(v===null)return;const r=await api('admin/label',{uid,label:v});if(r.ok){toast('已保存');load();openDetail(uid)}else toast(r.error||'失败')}
(function init(){const _l=load;_l();setInterval(_l,60000)})();
</script></body></html>"""


PAGE_REG = """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<link rel="icon" href="data:,">
<title>琴房预约助手 · 注册</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c1e21;--sub:#65676b;--pri:#2563eb;--ok:#16a34a;--bad:#dc2626}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);font-size:15px;padding:12px;max-width:460px;margin:0 auto;padding-bottom:60px}
h1{font-size:19px;margin:20px 2px 14px;text-align:center}
.card{background:var(--card);border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card h2{font-size:16px;margin-bottom:12px;text-align:center}
.grid{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.grid>*{flex:1;min-width:100px}
input{padding:11px;border:1px solid #d9dbe0;border-radius:9px;font-size:15px;background:#fff;color:var(--ink);width:100%}
button{border:none;border-radius:9px;padding:11px 14px;font-size:15px;background:#eef2ff;color:var(--pri);font-weight:600}
button.pri{background:var(--pri);color:#fff}
button:active{opacity:.7}
.tip{font-size:12px;color:var(--sub);margin-top:10px;line-height:1.6}
.okbox{margin-top:12px;background:#ecfdf5;border-radius:10px;padding:12px;font-size:13.5px;line-height:1.6}
.errbox{margin-top:12px;background:#fef2f2;border-radius:10px;padding:12px;font-size:13.5px;color:var(--bad)}
.linkbox{margin:8px 0}
code{background:#f3f4f6;border-radius:6px;padding:4px 6px;font-size:12px;word-break:break-all;display:block}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#111827ee;color:#fff;padding:11px 18px;border-radius:10px;font-size:14px;max-width:88%;display:none;z-index:9}
</style></head><body>
<h1>🎹 琴房预约助手</h1>
<div class="card">
<h2>注册 · 获取个人链接</h2>
<div class="grid"><input id="fSid" placeholder="统一认证学号" inputmode="numeric" autocomplete="off"></div>
<div class="grid"><input id="fPwd" type="password" placeholder="统一认证密码" autocomplete="off"></div>
<button class="pri" style="width:100%" onclick="doReg()">验证并获取我的专属链接</button>
<div id="regOut"></div>
<div class="tip">验证通过后你将获得一个专属链接(即钥匙), 用它进入自己的控制台: 设定每周/单次预约计划、每天 08:00 自动抢票、约满自动候补捡漏、查看门禁密码。<br>
<b>你的密码不会被保存</b>——只用于这一次统一认证登录验证, 服务器仅保存登录后的会话; 会话失效时用你的链接进入面板重新输入密码续期即可。<br>
防爆破保护: 学号 1 小时内错 2 次或同一网络错 10 次会临时锁定。</div>
</div>
<div id="toast"></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(m,ms=4000){const t=$('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',ms)}
async function doReg(){
  const sid=$('fSid').value.trim(), p=$('fPwd').value;
  if(!sid||!p){toast('请输入学号和密码');return}
  toast('统一认证登录验证中…',20000);
  $('regOut').innerHTML='';
  let r;
  try{const resp=await fetch('api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:sid,password:p})});r=await resp.json()}
  catch(e){$('regOut').innerHTML='<div class="errbox">网络异常, 请稍后再试</div>';return}
  if(r.ok){
    const url=new URL('../../'+r.path.replace(/^\\//,''),location.href).href;
    $('regOut').innerHTML=`<div class="okbox">✅ 验证成功！你的专属链接：
      <div class="linkbox"><code id="lnk">${esc(url)}</code></div>
      <button onclick="copyLnk()">复制链接</button>
      <div class="tip">请立即收藏到手机主屏, <b>勿外传</b>。若链接泄露, 回到本页重新验证一次即可让旧链接作废。</div></div>`;
    $('fPwd').value='';
  }else{
    $('regOut').innerHTML='<div class="errbox">'+esc(r.error||'注册失败')+'</div>';
  }
}
function copyText(p){const fb=()=>{const t=document.createElement('textarea');t.value=p;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();let c=false;try{c=document.execCommand('copy')}catch(e){}t.remove();toast(c?'已复制':'复制失败, 请长按链接手动复制',3000)};if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(p).then(()=>toast('已复制')).catch(fb)}else fb()}
function copyLnk(){copyText($('lnk').textContent)}
</script></body></html>"""


# ---------------- HTTP 服务 ----------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to):
        # 301 用相对 Location: 浏览器按当前路径解析, 不感知 nginx 的
        # /piano/ 反代前缀, 换部署路径也不用改这里
        self.send_response(301)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _ip(self):
        xff = self.headers.get("X-Forwarded-For") or ""
        ip = xff.split(",")[0].strip()
        if ip:
            return ip
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        try:
            path = urlparse(self.path).path
            parts = path.strip("/").split("/")
            token = get_token()

            if parts == ["healthz"] and method == "GET":
                self._send(200, json.dumps(hub_health(), ensure_ascii=False))
                return

            # 注册入口(独立 token)
            if len(parts) >= 2 and parts[0] == "reg":
                if parts[1] != reg_token():
                    self._send(404, "not found", "text/plain; charset=utf-8")
                    return
                if len(parts) == 2 and method == "GET" \
                        and not path.endswith("/"):
                    self._redirect(parts[1] + "/")
                    return
                rest = "/".join(parts[2:])
                if rest in ("",) and method == "GET":
                    self._send(200, PAGE_REG, "text/html; charset=utf-8")
                    return
                if rest == "api/register" and method == "POST":
                    self._json(lambda: api_register(self._body(), self._ip()))
                    return
                self._send(404, "not found", "text/plain; charset=utf-8")
                return

            # 身份解析: 站长 /t/<token>/, 成员 /u/<token>/
            uid = None
            if len(parts) >= 2 and parts[0] == "t" and parts[1] == token:
                uid = 0
            elif len(parts) >= 2 and parts[0] == "u":
                u = user_by_token(parts[1])
                if u and u.get("enabled") and u.get("role") == "member":
                    uid = u["uid"]
            if uid is None:
                self._send(404, "not found", "text/plain; charset=utf-8")
                return

            # 目录式入口规范化到尾斜杠: 无斜杠时页面本身能打开, 但页内
            # 相对 API(fetch('api/..')/'../api/..')会解析到丢失 token 的
            # 路径(如 /t/api/state)全部 404, 页面永远停在"加载中"、注册
            # 链接显示不出(2026-10-16 总控制台打不开事故根因)
            if method == "GET" and not path.endswith("/") \
                    and (len(parts) == 2
                         or (len(parts) == 3 and parts[2] == "admin")):
                self._redirect(parts[-1] + "/")
                return

            rest = "/".join(parts[2:])
            if rest in ("",) and method == "GET":
                self._send(200, PAGE, "text/html; charset=utf-8")
                return
            if rest == "admin" and method == "GET" and uid == 0:
                self._send(200, PAGE_ADMIN, "text/html; charset=utf-8")
                return

            if rest == "api/state" and method == "GET":
                self._json(lambda: api_state(uid))
                return
            if rest == "api/admin/overview" and method == "GET" and uid == 0:
                self._json(api_admin_overview)
                return
            if rest == "api/admin/user_state" and method == "GET" and uid == 0:
                qs = parse_qs(urlparse(self.path).query)
                try:
                    quid = int(qs.get("uid", ["0"])[0])
                except ValueError:
                    quid = 0
                self._json(lambda: api_admin_user_state(quid))
                return
            if method == "POST":
                body = self._body()
                simple = {
                    "api/targets": lambda: api_targets_replace(body, uid),
                    "api/book": lambda: api_book(body, uid),
                    "api/cancel": lambda: api_cancel(body, uid),
                    "api/watch": lambda: api_watch_add(body, uid),
                    "api/unwatch": lambda: api_watch_remove(body, uid),
                    "api/renew": lambda: api_renew(uid, body),
                }
                if rest in simple:
                    self._json(simple[rest])
                    return
                if rest == "api/watch_config" and uid == 0:
                    self._json(lambda: api_watch_config(body))
                    return
                if rest.startswith("api/admin/") and uid == 0:
                    sub = rest[len("api/admin/"):]
                    admin_post = {
                        "priority": api_admin_priority,
                        "label": api_admin_label,
                        "enable": api_admin_enable,
                        "remove": api_admin_remove,
                        "regen_reg": lambda b: api_admin_regen_reg(),
                        "regen_token": api_admin_regen_token,
                        "watch_remove": api_admin_watch_remove,
                        "book": lambda b: api_book(b, _int_or(b.get("uid"), 0)),
                        "cancel": lambda b: api_cancel(b, _int_or(b.get("uid"), 0)),
                    }
                    fn = admin_post.get(sub)
                    if fn:
                        self._json(lambda: fn(body))
                        return
            self._send(404, "not found", "text/plain; charset=utf-8")
        except BrokenPipeError:
            pass
        except Exception:
            wlog("请求处理异常:\n" + traceback.format_exc())
            try:
                self._send(500, {"ok": False, "error": "服务器内部错误"})
            except Exception:
                pass

    def _json(self, fn):
        try:
            result = fn()
        except Exception as e:
            wlog("API 异常:\n" + traceback.format_exc())
            result = {"ok": False, "error": f"操作失败: {e}"}
        self._send(200, json.dumps(result, ensure_ascii=False))

    def log_message(self, fmt, *args):
        pass


def _int_or(v, d):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def main():
    ensure_users()
    reg_token()
    load_hub_state()
    for fn, name in ((scheduler_loop, "scheduler"), (watch_loop, "watch"),
                     (keepalive_loop, "keepalive"),
                     (checkin_loop, "checkin")):
        threading.Thread(target=fn, daemon=True, name=name).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    wlog(f"琴房预约助手(多人共享版)启动: http://0.0.0.0:{PORT}/t/{get_token()}/ "
         f"reg=/reg/{reg_token()}/ 下次调度={SCHED.get('next_fire', '') or '待定'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
